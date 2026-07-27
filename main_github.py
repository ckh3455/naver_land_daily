#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러 + 데이터 정리 통합 스크립트
- 크롤링 후 자동으로 데이터 정리까지 수행
- 표시(서식) 로직은 테스트 버전에서 검증된 안정형으로 통합
"""

import asyncio
from playwright.async_api import async_playwright
import json
from datetime import datetime
import re
import os
import time
import gspread
from google.oauth2 import service_account
import traceback
import urllib.parse

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ====================================================================
# 크롤링 관련 코드 (기존 코드)
# ====================================================================

COMPLEXES = [
    {"id": "728", "name": "미성1차"},
    {"id": "742", "name": "미성2차"},
    {"id": "3037", "name": "신현대"},
    {"id": "705", "name": "현대3차"},
    {"id": "712", "name": "현대1,2차"},
    {"id": "495", "name": "현대4차"},
    {"id": "708", "name": "현대5차"},
    {"id": "514", "name": "현대10,13,14차"},
    {"id": "724", "name": "현대6,7차"},
    {"id": "27498", "name": "현대65동(대림아크로빌)"},
    {"id": "114923", "name": "현대빌라트"},
    {"id": "107189", "name": "대림빌라트"},
    {"id": "8617", "name": "현대8차"},
    {"id": "497", "name": "한양3차"},
    {"id": "498", "name": "한양4차"},
    {"id": "500", "name": "한양6차"},
    {"id": "494", "name": "영동한양1차"},
    {"id": "496", "name": "한양2차"},
    {"id": "499", "name": "한양5차"},
    {"id": "501", "name": "한양7차"},
    {"id": "502", "name": "한양8차"},
    {"id": "108405", "name": "트리마제"},
    {"id": "168097", "name": "메이플자이"},
    {"id": "102737", "name": "한남더힐"},
    {"id": "9428", "name": "압구정하이츠파크"}
]

def debug_log(message, level="INFO"):
    """초상세 디버깅 로그"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    prefix = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "ERROR": "❌",
        "WARNING": "⚠️ ",
        "DEBUG": "🔍",
        "STEP": "➡️ "
    }.get(level, "")
    print(f"[{timestamp}] {prefix} {message}")

def setup_google_sheets():
    """구글 시트 설정"""
    debug_log("=== 구글 시트 설정 시작 ===", "STEP")
    try:
        credentials_file = 'service_account.json'
        if not os.path.exists(credentials_file):
            debug_log(f"서비스 계정 파일이 없습니다: {credentials_file}", "ERROR")
            return None, None

        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        gc = gspread.authorize(credentials)
        spreadsheet_id = os.environ.get('SPREADSHEET_ID', '1QP56lm5kPBdsUhrgcgY2U-JdmukXIkKCSxefd1QExKE')
        spreadsheet = gc.open_by_key(spreadsheet_id)

        try:
            worksheet = spreadsheet.worksheet("네이버 매물분석")
            return worksheet, spreadsheet
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="네이버 매물분석", rows=1000, cols=30)
            return worksheet, spreadsheet
    except Exception as e:
        debug_log(f"구글 시트 설정 실패: {str(e)}", "ERROR")
        debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
        return None, None

# =========================
# 파싱/보정 헬퍼 (기존 코드)
# =========================
def _truthy(val):
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("true", "y", "1", "yes")

def _to_int(val, default=0):
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).strip().replace(",", "")
        return int(float(s))
    except Exception:
        return default

def _id_or_empty(val):
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    s = s.strip().strip('"').strip("'")
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    return s

def _to_text_cell(val):
    s = _id_or_empty(val)
    return f"'{s}" if s else ""

def _extract_realtor_id(raw):
    candidate_keys = (
        "realtorId", "realtorIdStr", "realtorNo", "realEstateAgentNo",
        "agentNo", "realtorIdNo", "agentId", "officeId"
    )
    for k in candidate_keys:
        if k in raw and raw[k]:
            rid = _id_or_empty(raw[k])
            if rid:
                return rid
    for sub in ("realtor", "realtorInfo", "agent", "office"):
        obj = raw.get(sub)
        if isinstance(obj, dict):
            for k in candidate_keys:
                if k in obj and obj[k]:
                    rid = _id_or_empty(obj[k])
                    if rid:
                        return rid
    url = (raw.get("realtorLinkUrl") or raw.get("realtorUrl") or "").strip()
    if url:
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            for qk in ("realtorId", "realtor_id", "agentId", "officeId"):
                if qk in qs and qs[qk]:
                    rid = urllib.parse.unquote(qs[qk][0])
                    rid = _id_or_empty(rid)
                    if rid:
                        return rid
        except Exception:
            pass
    return ""

def _has_photos(raw):
    for k in ("siteImageCount", "representativeImageCount", "imageCount"):
        if k in raw and _to_int(raw.get(k), 0) > 0:
            return True
    for k in ("siteImageCountYn", "representativeImageExistYn"):
        if k in raw and _truthy(raw.get(k)):
            return True
    return False

def _parse_price_number(s):
    if s is None:
        return None
    t = str(s).strip()
    try:
        if "억" in t:
            parts = t.replace(" ", "").split("억")
            eok = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            base = int(float(eok)) * 10000
            rest = rest.replace(",", "").replace("만", "")
            add = int(rest) if rest.isdigit() else 0
            return base + add
        return int(float(t.replace(",", "").replace("만", "")))
    except Exception:
        return None

def _resolve_price_change(raw):
    v = raw.get("priceChangeState")
    if isinstance(v, str) and v:
        updown = v.strip().upper()
        if updown == "UP":
            return "상승"
        if updown == "DOWN":
            return "하락"
    changed = _truthy(v)
    if not changed:
        return ""
    for k in ("priceChangeType", "dealPriceChangeTypeCode", "rentPriceChangeTypeCode", "priceChangeDirection"):
        s = raw.get(k)
        if isinstance(s, str):
            su = s.upper()
            if "UP" in su:
                return "상승"
            if "DOWN" in su:
                return "하락"
    for k in ("priceChange", "priceChangeAmount"):
        delta = raw.get(k)
        if delta is not None:
            try:
                d = float(str(delta).replace(",", ""))
                if d > 0:
                    return "상승"
                if d < 0:
                    return "하락"
            except Exception:
                pass
    prev_candidates = ("previousDealOrWarrantPrc", "prevPrice", "previousPrice")
    curr_candidates = ("dealOrWarrantPrc", "price", "currentPrice")
    prev_val = None
    curr_val = None
    for pk in prev_candidates:
        pv = _parse_price_number(raw.get(pk))
        if pv is not None:
            prev_val = pv
            break
    for ck in curr_candidates:
        cv = _parse_price_number(raw.get(ck))
        if cv is not None:
            curr_val = cv
            break
    if prev_val is not None and curr_val is not None and prev_val != curr_val:
        return "상승" if curr_val > prev_val else "하락"
    return "변동"

class AggressiveCardScroll:
    """네이버 부동산 매물 크롤러 (개선된 스크롤 방식)"""
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.base_url = f"https://new.land.naver.com/complexes/{self.complex_id}"
        self.property_cards = []
        self.unique_article_nos = set()
        self.page = None
        self.api_responses = []
        self.more_data = True

    # --------------------------------------------------------------------
    # ✅ 접속(네트워크) 부분만 수정:
    #   1) PROXY_SERVER 지원
    # --------------------------------------------------------------------
    async def setup_playwright(self):
        debug_log("=== Playwright 환경 설정 시작 ===", "STEP")
        try:
            self.playwright = await async_playwright().start()

            proxy_server = os.getenv("PROXY_SERVER", "").strip()
            launch_kwargs = dict(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor',
                    '--window-size=1920,1080'
                ]
            )
            if proxy_server:
                launch_kwargs["proxy"] = {"server": proxy_server}
                debug_log(f"[PROXY] enabled: {proxy_server}", "WARNING")

            self.browser = await self.playwright.chromium.launch(**launch_kwargs)

            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )

            self.page = await self.context.new_page()
            self.page.on('response', self.handle_response)
        except Exception as e:
            debug_log(f"Playwright 설정 실패: {str(e)}", "ERROR")
            raise

    async def handle_response(self, response):
        url = response.url
        if 'api/articles/complex' in url and 'page=' in url:
            try:
                data = await response.json()
                self.api_responses.append({
                    'url': url,
                    'data': data,
                    'timestamp': datetime.now().isoformat()
                })
                await self.extract_properties_from_response(data, url)
            except Exception as e:
                debug_log(f"API 응답 파싱 실패: {url} - {str(e)}", "WARNING")

    async def extract_properties_from_response(self, data, url):
        if isinstance(data, dict) and 'articleList' in data:
            articles = data['articleList']
            page_match = re.search(r'page=(\d+)', url)
            page_num = page_match.group(1) if page_match else "Unknown"
            new_properties = 0
            for article in articles:
                if isinstance(article, dict):
                    article_no = article.get('articleNo', '')
                    if article_no and article_no not in self.unique_article_nos:
                        self.unique_article_nos.add(article_no)
                        is_owner = article.get('verificationTypeCode') == 'OWNER'
                        property_data = {
                            'complex_id': self.complex_id,
                            'complex_name': self.complex_name,
                            'article_no': article_no,
                            'raw_data': article,
                            'extracted_at': datetime.now().isoformat(),
                            'card_number': len(self.property_cards) + 1,
                            'page_number': page_num,
                            'is_owner_flag': is_owner
                        }
                        self.property_cards.append(property_data)
                        new_properties += 1
            if new_properties > 0:
                debug_log(f"  ➕ 페이지 {page_num}에서 {new_properties}개 매물 추가 (총 {len(self.property_cards)}개)", "SUCCESS")
            self.more_data = bool(data.get('isMoreData', False))
            debug_log(f"  📄 isMoreData: {self.more_data}", "DEBUG")
            return self.more_data
        return False

    # --------------------------------------------------------------------
    # ✅ 접속(네트워크) 부분만 수정:
    #   2) page.goto 재시도(backoff) 추가
    # --------------------------------------------------------------------
    async def _goto_with_retry(self, url, wait_until='domcontentloaded', timeout=60000, attempts=5):
        last_err = None
        for i in range(1, attempts + 1):
            try:
                debug_log(f"[GOTO] {url} (try {i}/{attempts})", "DEBUG")
                await self.page.goto(url, wait_until=wait_until, timeout=timeout)
                return True
            except Exception as e:
                last_err = e
                debug_log(f"[GOTO FAIL] {url} (try {i}/{attempts}) - {e}", "WARNING")
                # 점진적 backoff
                await asyncio.sleep(min(2 * i, 10))
        raise last_err

    async def navigate_to_complex_page(self):
        debug_log("=== 단지 페이지 이동 시작 ===", "STEP")
        try:
            # ✅ 기존 page.goto를 재시도 버전으로만 교체 (나머지 로직 유지)
            await self._goto_with_retry(self.base_url, wait_until='domcontentloaded', timeout=60000, attempts=5)

            await asyncio.sleep(1.5)
            debug_log("상세매물검색 버튼 클릭 시도...", "DEBUG")
            try:
                clicked = await self.page.evaluate("""
                    () => {
                        const button = Array.from(document.querySelectorAll('button'))
                            .find(b => b.innerText?.includes('상세매물검색'));
                        if (button) {
                            button.click();
                            return true;
                        }
                        return false;
                    }
                """)
                if clicked:
                    debug_log("✅ 상세매물검색 버튼 클릭 성공", "SUCCESS")
                    await asyncio.sleep(1)
                else:
                    debug_log("⚠️ 상세매물검색 버튼을 찾지 못함 (이미 활성화 상태일 수 있음)", "WARNING")
            except Exception as e:
                debug_log(f"버튼 클릭 중 오류: {str(e)}", "WARNING")
            try:
                await self.page.wait_for_response(
                    lambda r: 'api/articles/complex' in r.url,
                    timeout=2500
                )
            except:
                pass
        except Exception as e:
            debug_log(f"페이지 로드 실패: {str(e)}", "ERROR")
            raise

    async def aggressive_scroll(self):
        debug_log("=== 스크롤 컨테이너 방식 스크롤 시작 ===", "STEP")
        last_height = 0
        no_height_change_count = 0
        MAX_NO_CHANGE = 3
        scroll_attempts = 0
        no_new_data_count = 0
        while self.more_data:
            prev_count = len(self.property_cards)
            try:
                container_state = await self.page.evaluate("""
                    () => {
                        const container = document.querySelector('.item_list--article');
                        if (!container) return { success: false };
                        return {
                            success: true,
                            scrollHeight: container.scrollHeight,
                            scrollTop: container.scrollTop,
                            clientHeight: container.clientHeight
                        };
                    }
                """)
                if not container_state['success']:
                    debug_log("⚠️ 스크롤 컨테이너를 찾지 못함", "WARNING")
                    break

                current_height = container_state['scrollHeight']
                is_at_bottom = (container_state['scrollTop'] + container_state['clientHeight']) >= (current_height - 10)

                if current_height == last_height and is_at_bottom:
                    no_height_change_count += 1
                    debug_log(f"⚠️ 컨테이너 높이 변화 없음 ({no_height_change_count}/{MAX_NO_CHANGE})", "WARNING")
                    if no_height_change_count >= MAX_NO_CHANGE:
                        if not self.more_data:
                            debug_log("⏹️ isMoreData=False + 높이 변화 없음 → 종료", "INFO")
                            break
                        else:
                            debug_log("⚠️ 높이 변화 없지만 isMoreData=True → 3초 추가 대기", "WARNING")
                            await asyncio.sleep(3)
                            no_height_change_count = 0
                else:
                    no_height_change_count = 0
                    if current_height > last_height:
                        debug_log(f"📈 컨테이너 확장: {last_height} → {current_height} (+{current_height - last_height}px)", "SUCCESS")
                    last_height = current_height

                scroll_result = await self.page.evaluate("""
                    () => {
                        const container = document.querySelector('.item_list--article');
                        if (container) {
                            const before = container.scrollTop;
                            container.scrollTop += 1000;
                            const after = container.scrollTop;
                            return {
                                success: true,
                                scrolled: after - before,
                                progress: ((after / (container.scrollHeight - container.clientHeight)) * 100).toFixed(1)
                            };
                        }
                        return { success: false };
                    }
                """)
                if scroll_result['success']:
                    scroll_attempts += 1
                    debug_log(f"🔄 [{scroll_attempts}] 스크롤: {scroll_result['scrolled']}px (진행률: {scroll_result['progress']}%)", "DEBUG")

                try:
                    await self.page.wait_for_response(
                        lambda r: 'api/articles/complex' in r.url and 'page=' in r.url,
                        timeout=1500
                    )
                    debug_log("📡 API 응답 감지!", "SUCCESS")
                except:
                    pass

                await asyncio.sleep(0.5)

                if len(self.property_cards) > prev_count:
                    new_count = len(self.property_cards) - prev_count
                    no_new_data_count = 0
                    debug_log(f"🎉 새 매물 {new_count}개 추가! (총 {len(self.property_cards)}개)", "SUCCESS")
                else:
                    no_new_data_count += 1
                    if no_new_data_count >= 6:
                        debug_log("⏹️ 6회 연속 새 매물이 없어 스크롤 종료", "INFO")
                        break

                if scroll_attempts >= 100:
                    debug_log(f"⛔ 안전중단: 100회 스크롤 시도 초과 (총 {len(self.property_cards)}개 수집)", "WARNING")
                    break

            except Exception as e:
                debug_log(f"스크롤 중 오류: {str(e)}", "WARNING")
                await asyncio.sleep(2)

        debug_log(f"✅ 스크롤 완료 (총 {len(self.property_cards)}개 수집, {scroll_attempts}회 시도)", "SUCCESS")

    async def close_browser(self):
        try:
            if hasattr(self, 'browser'):
                await self.browser.close()
            if hasattr(self, 'playwright'):
                await self.playwright.stop()
        except Exception:
            pass

    async def run(self):
        try:
            await self.setup_playwright()
            await self.navigate_to_complex_page()
            await self.aggressive_scroll()
            await self.close_browser()
            return {
                'complex_id': self.complex_id,
                'complex_name': self.complex_name,
                'property_count': len(self.property_cards),
                'properties': self.property_cards
            }
        except Exception as e:
            debug_log(f"크롤링 중 치명적 오류 발생: {str(e)}", "ERROR")
            await self.close_browser()
            return {
                'complex_id': self.complex_id,
                'complex_name': self.complex_name,
                'property_count': 0,
                'properties': [],
                'error': str(e)
            }

def format_property_data(property_data):
    raw_data = property_data.get('raw_data', {})
    area1 = raw_data.get('area1', '')
    area2 = raw_data.get('area2', '')
    if area1 and area2 and area1 != area2:
        area = f"{area1}/{area2}m²"
    elif area1:
        area = f"{area1}m²"
    else:
        area = raw_data.get('areaName', '') + "m²" or "Unknown"

    trade_type = raw_data.get('tradeTypeName', '')
    price = raw_data.get('dealOrWarrantPrc', '')

    if trade_type == '월세':
        deposit = raw_data.get('dealOrWarrantPrc', '')
        monthly = raw_data.get('rentPrc', '')
        if deposit and monthly:
            price = f"{deposit}/{monthly}만원"
        elif deposit:
            price = deposit
        elif monthly:
            price = f"{monthly}만원"

    price_change_display = _resolve_price_change(raw_data)

    is_owner_listing = "집주인" if property_data.get('is_owner_flag') is True else ""
    certification_ad = "인증광고" if _truthy(raw_data.get('tradeCheckedByOwner')) else ""
    direct_trade_listing = "직거래" if _truthy(raw_data.get('isDirectTrade')) else ""
    photo_status = "사진있음" if _has_photos(raw_data) else ""

    date_str = raw_data.get('articleConfirmYmd', '')
    if date_str and len(str(date_str)) == 8 and str(date_str).isdigit():
        registration_date = f"{str(date_str)[:4]}.{str(date_str)[4:6]}.{str(date_str)[6:8]}"
    else:
        registration_date = date_str or "Unknown"

    realtor_id_raw = _extract_realtor_id(raw_data)
    realtor_id_cell = _to_text_cell(realtor_id_raw)

    return [
        property_data.get('complex_name', ''),
        trade_type,
        raw_data.get('buildingName', ''),
        raw_data.get('floorInfo', ''),
        area,
        price,
        price_change_display,
        1,
        raw_data.get('realtorName', 'Unknown'),
        realtor_id_cell,
        registration_date,
        raw_data.get('direction', ''),
        raw_data.get('articleFeatureDesc', ''),
        raw_data.get('cpName', 'Unknown'),
        is_owner_listing,
        direct_trade_listing,
        photo_status,
        raw_data.get('longitude', ''),
        raw_data.get('latitude', ''),
        raw_data.get('articleNo', ''),
        certification_ad
    ]

# ====================================================================
# 데이터 정리 관련 코드
# ====================================================================

COL_단지명 = 0
COL_거래구분 = 1
COL_동 = 2
COL_층수 = 3
COL_면적 = 4
COL_가격 = 5
COL_가격변동 = 6
COL_중복업소수 = 7
COL_중개업소 = 8
COL_중개업소ID = 9
COL_등록일자 = 10
COL_방향 = 11
COL_특기사항 = 12
COL_제공 = 13
COL_집주인 = 14
COL_직거래 = 15
COL_사진유무 = 16
COL_경도 = 17
COL_위도 = 18
COL_매물번호 = 19
COL_인증광고 = 20

COLOR_전세 = {"red": 0.996, "green": 0.910, "blue": 0.851}
COLOR_월세 = {"red": 0.882, "green": 0.914, "blue": 0.788}
COLOR_압구정원 = {"red": 0.812, "green": 0.886, "blue": 0.953}
COLOR_집주인_TEXT = {"red": 0.416, "green": 0.659, "blue": 0.310}
COLOR_저빈도_TEXT = {"red": 0.800, "green": 0.000, "blue": 0.000}
# ✅ 집주인 + (탭없음 또는 저빈도) 보라색
COLOR_집주인_저빈도_TEXT = {"red": 0.600, "green": 0.200, "blue": 0.800}

DEFAULT_BLACK = {"red": 0, "green": 0, "blue": 0}

EXCLUDED_COMPLEXES = ["메이플자이", "트리마제", "한남더힐", "압구정하이츠파크"]
ALIAS_SHEET_NAME = "압구정 중개업소"
ALIAS_HEADER_NAME = "중개업소명"
ALIAS_HEADER_ID = "중개업소ID"
ALIAS_HEADER_CANON = "실제상호"
LOW_FREQUENCY_THRESHOLD = 3

def normalize(v):
    if v is None:
        return ""
    s = str(v)
    # ✅ 제로폭 공백/보이지 않는 문자 제거 (탭 목록 매칭 실패 방지)
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
    # 모든 종류의 공백(탭/개행/연속 공백 등)을 1칸으로 축약
    return re.sub(r"\s+", " ", s).strip()

def extract_dong_number(s):
    if not isinstance(s, str):
        return 0
    cleaned = re.sub(r'[^\d-]', '', s)
    try:
        return float(cleaned.replace('-', '.'))
    except:
        return 0

def extract_area_number(s):
    if not s:
        return 0
    s = str(s).strip()
    if not s:
        return 0
    numbers = re.findall(r'\d+\.?\d*', s)
    if numbers:
        try:
            return float(numbers[0])
        except:
            return 0
    return 0

# ✅ (통합) 테스트버전 성공과 동일: validCanonNames 포함 + knownCanonNames(탭존재 판정 강화)
def load_brokerage_alias_maps(sheet_service, spreadsheet_id):
    by_name = {}
    by_id = {}
    valid_canon_names = set()   # '실제상호' 전체 값
    known_canon_names = set()   # ✅ 탭에 존재하는 것으로 판정할 집합(실제상호 + 매핑값)

    try:
        sheet_metadata = sheet_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()

        alias_sheet_id = None
        for sheet in sheet_metadata.get('sheets', []):
            if sheet['properties']['title'] == ALIAS_SHEET_NAME:
                alias_sheet_id = sheet['properties']['sheetId']
                break

        if alias_sheet_id is None:
            print(f"'{ALIAS_SHEET_NAME}' 시트를 찾을 수 없습니다.")
            return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names, "knownCanonNames": known_canon_names}

        result = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{ALIAS_SHEET_NAME}'!A:Z"
        ).execute()

        values = result.get('values', [])
        if len(values) < 2:
            return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names, "knownCanonNames": known_canon_names}

        header = values[0]
        idx_name = header.index(ALIAS_HEADER_NAME) if ALIAS_HEADER_NAME in header else -1
        idx_id = header.index(ALIAS_HEADER_ID) if ALIAS_HEADER_ID in header else -1
        idx_canon = header.index(ALIAS_HEADER_CANON) if ALIAS_HEADER_CANON in header else -1

        if idx_canon < 0:
            return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names, "knownCanonNames": known_canon_names}

        for row in values[1:]:
            if len(row) <= idx_canon:
                continue

            canon = normalize(row[idx_canon])
            if not canon:
                continue

            valid_canon_names.add(canon)

            if idx_name >= 0 and len(row) > idx_name:
                name = normalize(row[idx_name])
                if name:
                    by_name[name] = canon

            if idx_id >= 0 and len(row) > idx_id:
                id_val = normalize(row[idx_id])
                if id_val:
                    by_id[id_val] = canon

        # ✅ '탭에 존재' 판정용 확장 집합(실제상호 + 매핑값) 생성
        for x in valid_canon_names:
            nx = normalize(x)
            if nx:
                known_canon_names.add(nx)
        for x in by_name.values():
            nx = normalize(x)
            if nx:
                known_canon_names.add(nx)
        for x in by_id.values():
            nx = normalize(x)
            if nx:
                known_canon_names.add(nx)

        print(f"[Alias] byName={len(by_name)}, byId={len(by_id)}, validCanonNames={len(valid_canon_names)}, knownCanonNames={len(known_canon_names)}")
    except Exception as e:
        print(f"매핑 로드 오류: {e}")

    return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names, "knownCanonNames": known_canon_names}


def sort_and_group_data(rows):
    by_complex = {}
    order = []
    for r in rows:
        complex_name = r[COL_단지명] if len(r) > COL_단지명 else ""
        if complex_name not in by_complex:
            by_complex[complex_name] = []
            order.append(complex_name)
        by_complex[complex_name].append({
            "원본행": r,
            "거래": r[COL_거래구분] if len(r) > COL_거래구분 else "",
            "면적": extract_area_number(r[COL_면적] if len(r) > COL_면적 else ""),
            "동": extract_dong_number(r[COL_동] if len(r) > COL_동 else "")
        })
    out = []
    for c in order:
        arr = by_complex[c]
        arr.sort(key=lambda x: (
            1 if x["거래"] == "매매" else (2 if x["거래"] == "전세" else (3 if x["거래"] == "월세" else 99)),
            x["면적"],
            x["동"]
        ))
        for x in arr:
            out.append(x["원본행"])
    return out

def process_duplicate_listings(rows, alias):
    """
    중복 매물 통합
    - key 동일한 행들을 한 줄로 합치고, 중개업소를 콤마로 합침
    - 중복업소(열)는 '최종 중개업소 개수'를 모든 행에 숫자로 표시 (최소 1)
    - 중복여부(볼드 조건)는 중개업소 개수 >= 2 로만 결정
    """
    item_map = {}
    infos = []

    for r in rows:
        key_parts = [
            r[COL_단지명] if len(r) > COL_단지명 else "",
            r[COL_거래구분] if len(r) > COL_거래구분 else "",
            r[COL_동] if len(r) > COL_동 else "",
            r[COL_층수] if len(r) > COL_층수 else "",
            r[COL_가격] if len(r) > COL_가격 else ""
        ]
        key = "|".join(str(p) for p in key_parts)

        raw_name = normalize(r[COL_중개업소] if len(r) > COL_중개업소 else "")
        raw_id = normalize(r[COL_중개업소ID] if len(r) > COL_중개업소ID else "")

        display_name = alias.get("byName", {}).get(raw_name)
        if not display_name and raw_id:
            display_name = alias.get("byId", {}).get(raw_id)
        if not display_name:
            display_name = raw_name
        # 공백/특수공백 차이로 인한 "집주인/저빈도" 매칭 실패 방지
        display_name = normalize(display_name)

        owner = normalize(r[COL_집주인] if len(r) > COL_집주인 else "") == "집주인"

        if key not in item_map:
            item_map[key] = {
                "대표행": r.copy() if isinstance(r, list) else list(r),
                "상태": {},
                "원본명": {}
            }

        if display_name:
            prev = item_map[key]["상태"].get(display_name, False)
            item_map[key]["상태"][display_name] = prev or owner
            # 원본 중개업소명 저장(빨간색 체크용)
            item_map[key]["원본명"][display_name] = raw_name

    for key, info in item_map.items():
        row = info["대표행"]
        names = [n for n in info["상태"].keys() if normalize(n)]
        # 최종 중개업소 수(표시/볼드 기준)
        dup_count = max(1, len(names))

        # 길이 보정
        while len(row) <= COL_중개업소:
            row.append("")
        while len(row) <= COL_중복업소수:
            row.append("")

        row[COL_가격변동] = ""
        # ✅ 모든 행에 숫자 표시(1 이상)
        row[COL_중복업소수] = dup_count
        row[COL_중개업소] = ", ".join(names)

        has_apgujeong_one = any("압구정원" in n for n in names)

        infos.append({
            "행": row,
            # ✅ 볼드는 '중개업소 수 >= 2' 일 때만
            "중복여부": dup_count >= 2,
            "거래구분": row[COL_거래구분] if len(row) > COL_거래구분 else "",
            "압구정원포함": has_apgujeong_one,
            "상태": info["상태"],
            "원본명": info.get("원본명", {})
        })

    return {
        "finalDataRows": [x["행"] for x in infos],
        "finalDataInfos": infos
    }


def get_brokerage_counts(final_rows):
    cnt = {}
    for r in final_rows:
        complex_name = r[COL_단지명] if len(r) > COL_단지명 else ""
        if complex_name in EXCLUDED_COMPLEXES:
            continue
        joined = str(r[COL_중개업소] if len(r) > COL_중개업소 else "")
        names = [normalize(s) for s in joined.split(",") if normalize(s)]
        for name in names:
            cnt[name] = cnt.get(name, 0) + 1
    return cnt


SALES_LIST_SHEET_NAME = "매매물건 목록"
SALES_LIST_COL_COUNT = 25
FEATURES_COLUMN_INDEX = 14  # O열: 특징
CHECK_REQUIRED_TEXT = "확인요망"
CHECK_REQUIRED_BLUE = {"red": 0.0, "green": 0.35, "blue": 0.80}


def _number_from_text(value):
    text = normalize(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _normalize_complex_key(value):
    return re.sub(r"[\s,·.]", "", normalize(value)).replace("아파트", "")


def _normalize_dong_key(value):
    text = normalize(value)
    match = re.search(r"\d+", text)
    return str(int(match.group(0))) if match else text


def _area_to_pyeong(area_value):
    """네이버 공급면적(첫 번째 ㎡ 값)을 매물장의 '00평' 형식으로 변환."""
    area_m2 = _number_from_text(area_value)
    if area_m2 is None or area_m2 <= 0:
        return ""
    return f"{int(round(area_m2 / 3.305785))}평"


def _floor_parts(value):
    """
    네이버 floorInfo를 매물장용 층 표시와 층대(저/중/고)로 변환.
    예: 7/15 -> ('7층(네이버)', '중', 7)
    """
    text = normalize(value)
    numbers = [int(x) for x in re.findall(r"\d+", text)]
    current = numbers[0] if numbers else None
    total = numbers[1] if len(numbers) >= 2 and numbers[1] > 0 else None

    if "저" in text:
        band = "저"
    elif "중" in text:
        band = "중"
    elif "고" in text:
        band = "고"
    elif current is not None and total:
        ratio = current / total
        band = "저" if ratio <= 0.33 else ("중" if ratio <= 0.66 else "고")
    else:
        band = ""

    floor_cell = f"{current}층(네이버)" if current is not None else f"{text}(네이버)"
    return floor_cell, band, current


def _price_key(value):
    """매매가격 문자열에서 억 단위 비교값을 만든다."""
    text = normalize(value).replace(",", "")
    if not text:
        return None
    if "억" in text:
        left, right = text.split("억", 1)
        base = _number_from_text(left)
        extra = _number_from_text(right)
        if base is not None:
            return round(base + ((extra or 0) / 10000), 4)
    number = _number_from_text(text)
    if number is None:
        return None
    # 네이버가 만원 단위 정수로 주는 경우(예: 560000)도 억 단위로 맞춘다.
    return round(number / 10000, 4) if number >= 1000 else round(number, 4)


def _listing_floor_number(value):
    """매물장 '층/호' 값에서 비교용 층수를 얻는다(예: 1304호 -> 13층)."""
    text = normalize(value)
    number = _number_from_text(text)
    if number is None:
        return None
    if "층" in text:
        return int(number)
    if float(number).is_integer() and number >= 100:
        return int(number) // 100
    return int(number)


def _existing_listing_matches(existing_rows, candidate):
    candidate_complex = _normalize_complex_key(candidate["단지명"])
    candidate_pyeong = _number_from_text(candidate["평형"])
    candidate_dong = _normalize_dong_key(candidate["동"])
    candidate_price = _price_key(candidate["가격"])

    for row in existing_rows[1:]:
        if len(row) < 9:
            continue
        if _normalize_complex_key(row[2] if len(row) > 2 else "") != candidate_complex:
            continue
        if _number_from_text(row[3] if len(row) > 3 else "") != candidate_pyeong:
            continue
        if _normalize_dong_key(row[5] if len(row) > 5 else "") != candidate_dong:
            continue
        if _price_key(row[8] if len(row) > 8 else "") != candidate_price:
            continue

        existing_floor = normalize(row[6] if len(row) > 6 else "")
        existing_band = normalize(row[7] if len(row) > 7 else "")
        existing_floor_no = _listing_floor_number(existing_floor)
        candidate_floor_no = candidate.get("층번호")

        # 정확한 층이 같거나, 정확한 층을 알 수 없을 때 층대가 같으면 기존 물건으로 본다.
        if candidate_floor_no is not None and existing_floor_no == candidate_floor_no:
            return True
        if candidate.get("층대") and existing_band == candidate["층대"]:
            return True
        if not candidate.get("층대") and not existing_band:
            return True
    return False


def _row_sort_number(value, default=999999):
    number = _number_from_text(value)
    return number if number is not None else default


def _find_sales_insert_index(rows, candidate):
    """
    기존 사용자 정렬 순서를 최대한 보존한다.
    같은 단지/평형/동 그룹 안에서는 층 순서로, 그룹이 없으면 가장 가까운
    단지/평형/구역 그룹의 마지막 다음 위치에 삽입한다.
    반환값은 Sheets API의 0-based 행 인덱스이자 rows 리스트 삽입 인덱스다.
    """
    zone = normalize(candidate["구역"])
    complex_key = _normalize_complex_key(candidate["단지명"])
    pyeong_no = _number_from_text(candidate["평형"])
    dong_key = _normalize_dong_key(candidate["동"])
    floor_no = candidate.get("층번호")

    exact_group = []
    pyeong_group = []
    complex_group = []
    zone_group = []

    for idx, row in enumerate(rows[1:], start=1):
        row_zone = normalize(row[1] if len(row) > 1 else "")
        row_complex = _normalize_complex_key(row[2] if len(row) > 2 else "")
        row_pyeong = _number_from_text(row[3] if len(row) > 3 else "")
        row_dong = _normalize_dong_key(row[5] if len(row) > 5 else "")

        if row_zone == zone:
            zone_group.append(idx)
        if row_complex == complex_key:
            complex_group.append(idx)
            if row_pyeong == pyeong_no:
                pyeong_group.append(idx)
                if row_dong == dong_key:
                    exact_group.append(idx)

    if exact_group:
        target_floor = floor_no if floor_no is not None else 999999
        for idx in exact_group:
            row_floor = _listing_floor_number(rows[idx][6] if len(rows[idx]) > 6 else "")
            row_floor = row_floor if row_floor is not None else 999999
            if target_floor < row_floor:
                return idx
        return exact_group[-1] + 1

    if pyeong_group:
        target_dong = _row_sort_number(candidate["동"])
        for idx in pyeong_group:
            row_dong = _row_sort_number(rows[idx][5] if len(rows[idx]) > 5 else "")
            if target_dong < row_dong:
                return idx
        return pyeong_group[-1] + 1

    if complex_group:
        target_pyeong = pyeong_no if pyeong_no is not None else 999999
        for idx in complex_group:
            row_pyeong = _row_sort_number(rows[idx][3] if len(rows[idx]) > 3 else "")
            if target_pyeong < row_pyeong:
                return idx
        return complex_group[-1] + 1

    if zone_group:
        return zone_group[-1] + 1
    return len(rows)


def _build_check_required_candidates(final_data_infos, brokerage_counts, alias, sales_rows):
    known_canon = alias.get("knownCanonNames", alias.get("validCanonNames", set()))
    # 매물장에 실제 존재하는 단지만 자동삽입 대상으로 제한한다.
    complex_to_zone = {}
    for row in sales_rows[1:]:
        if len(row) > 3:
            ckey = _normalize_complex_key(row[2])
            if ckey and ckey not in complex_to_zone:
                complex_to_zone[ckey] = normalize(row[1] if len(row) > 1 else "")

    candidates = []
    seen_keys = set()
    for info in final_data_infos:
        row = info["행"]
        if len(row) <= COL_중개업소 or normalize(row[COL_거래구분]) != "매매":
            continue

        complex_name = normalize(row[COL_단지명])
        if complex_name in EXCLUDED_COMPLEXES:
            continue
        complex_key = _normalize_complex_key(complex_name)
        zone = complex_to_zone.get(complex_key)
        if not zone:
            continue

        names = [normalize(x) for x in str(row[COL_중개업소] or "").split(",") if normalize(x)]
        low_frequency = [n for n in names if brokerage_counts.get(n, 0) <= LOW_FREQUENCY_THRESHOLD]
        external = [n for n in names if n not in known_canon]
        multiple = len(names) >= 2

        reasons = []
        if multiple:
            reasons.append(f"여러 업소 중복광고({len(names)}곳)")
        if low_frequency:
            reasons.append("저빈도 업소: " + ", ".join(low_frequency))
        if external:
            reasons.append("외부 업소: " + ", ".join(external))
        if not reasons:
            continue

        pyeong = _area_to_pyeong(row[COL_면적] if len(row) > COL_면적 else "")
        if not pyeong:
            continue
        floor_cell, floor_band, floor_no = _floor_parts(row[COL_층수] if len(row) > COL_층수 else "")
        candidate = {
            "구역": zone,
            "단지명": complex_name,
            "평형": pyeong,
            "동": normalize(row[COL_동] if len(row) > COL_동 else ""),
            "층/호": floor_cell,
            "층대": floor_band,
            "층번호": floor_no,
            "가격": normalize(row[COL_가격] if len(row) > COL_가격 else ""),
            "중개업소": ", ".join(names),
            "등록일": normalize(row[COL_등록일자] if len(row) > COL_등록일자 else ""),
            "매물번호": normalize(row[COL_매물번호] if len(row) > COL_매물번호 else ""),
            "사유": " / ".join(reasons)
        }
        dedupe_key = (
            complex_key, _number_from_text(pyeong), _normalize_dong_key(candidate["동"]),
            floor_band, floor_no, _price_key(candidate["가격"])
        )
        if dedupe_key in seen_keys or _existing_listing_matches(sales_rows, candidate):
            continue
        seen_keys.add(dedupe_key)
        candidates.append(candidate)
    return candidates


def insert_check_required_sales_listings(
    sheet_service, sales_spreadsheet_id, final_data_infos, brokerage_counts, alias
):
    """선별된 네이버 매물을 매매물건 목록의 기존 정렬 위치에 중복 없이 삽입."""
    metadata = sheet_service.spreadsheets().get(
        spreadsheetId=sales_spreadsheet_id
    ).execute()
    sales_sheet_id = None
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == SALES_LIST_SHEET_NAME:
            sales_sheet_id = props.get("sheetId")
            break
    if sales_sheet_id is None:
        print(f"'{SALES_LIST_SHEET_NAME}' 시트를 찾지 못해 확인요망 자동삽입을 생략합니다.")
        return 0

    response = sheet_service.spreadsheets().values().get(
        spreadsheetId=sales_spreadsheet_id,
        range=f"'{SALES_LIST_SHEET_NAME}'!A:Y"
    ).execute()
    sales_rows = response.get("values", [])
    if not sales_rows:
        print("매매물건 목록이 비어 있어 확인요망 자동삽입을 생략합니다.")
        return 0

    candidates = _build_check_required_candidates(
        final_data_infos, brokerage_counts, alias, sales_rows
    )
    print(f"매매물건 목록 확인요망 신규 후보: {len(candidates)}건")

    inserted = 0
    today = datetime.now().strftime("%y.%m.%d")
    for candidate in candidates:
        # 앞선 후보 삽입으로 바뀐 가상 행 목록을 기준으로 매번 위치를 다시 계산한다.
        insert_index = _find_sales_insert_index(sales_rows, candidate)
        source_index = max(1, insert_index - 1)

        values = [""] * SALES_LIST_COL_COUNT
        pyeong_no = int(_number_from_text(candidate["평형"]) or 0)
        values[0] = f"{(pyeong_no // 10) * 10}평형대" if pyeong_no else ""
        values[1] = candidate["구역"]
        values[2] = candidate["단지명"]
        values[3] = candidate["평형"]
        values[5] = candidate["동"]
        values[6] = candidate["층/호"]
        values[7] = candidate["층대"]
        values[8] = candidate["가격"]
        values[9] = today
        values[10] = candidate["등록일"] or today
        values[11] = candidate["중개업소"]
        values[12] = (
            f"[네이버 자동확인] {candidate['사유']}"
            + (f" / 매물번호 {candidate['매물번호']}" if candidate["매물번호"] else "")
        )
        values[14] = CHECK_REQUIRED_TEXT
        values[15] = "확인"

        requests = [{
            "insertDimension": {
                "range": {
                    "sheetId": sales_sheet_id,
                    "dimension": "ROWS",
                    "startIndex": insert_index,
                    "endIndex": insert_index + 1
                },
                "inheritFromBefore": insert_index > 1
            }
        }]

        # 인접한 기존 행의 서식/유효성 검사를 복사하되 값은 복사하지 않는다.
        requests.append({
            "copyPaste": {
                "source": {
                    "sheetId": sales_sheet_id,
                    "startRowIndex": source_index,
                    "endRowIndex": source_index + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": SALES_LIST_COL_COUNT
                },
                "destination": {
                    "sheetId": sales_sheet_id,
                    "startRowIndex": insert_index,
                    "endRowIndex": insert_index + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": SALES_LIST_COL_COUNT
                },
                "pasteType": "PASTE_FORMAT",
                "pasteOrientation": "NORMAL"
            }
        })
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sales_sheet_id,
                    "startRowIndex": insert_index,
                    "endRowIndex": insert_index + 1,
                    "startColumnIndex": FEATURES_COLUMN_INDEX,
                    "endColumnIndex": FEATURES_COLUMN_INDEX + 1
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": CHECK_REQUIRED_BLUE
                        }
                    }
                },
                "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.foregroundColor"
            }
        })
        sheet_service.spreadsheets().batchUpdate(
            spreadsheetId=sales_spreadsheet_id,
            body={"requests": requests}
        ).execute()
        sheet_service.spreadsheets().values().update(
            spreadsheetId=sales_spreadsheet_id,
            range=f"'{SALES_LIST_SHEET_NAME}'!A{insert_index + 1}:Y{insert_index + 1}",
            valueInputOption="USER_ENTERED",
            body={"values": [values]}
        ).execute()

        sales_rows.insert(insert_index, values)
        inserted += 1
        print(
            f"[확인요망 추가] {candidate['단지명']} {candidate['평형']} "
            f"{candidate['동']} {candidate['층/호']} {candidate['가격']}"
        )
    return inserted


# ==========================================================
# ✅ (통합) 테스트버전 성공 로직: 안정형 RichText + 행단위 repeatCell + chunk batchUpdate
# ==========================================================
def apply_styles_and_alignment(sheet_service, spreadsheet_id, sheet_id, infos, header_row, col_count, brokerage_counts, alias):
    try:
        row_count = len(infos)
        if not row_count:
            return

        print(f"스타일 적용 시작: {row_count}행")
        requests = []

        DEFAULT_BLACK = {"red": 0, "green": 0, "blue": 0}

        def _make_text_format_runs(text, segments):
            """
            segments: [{"start": int, "end": int, "color": {r,g,b}}, ...]
            - textFormatRuns는 startIndex만 허용
            - startIndex는 반드시 len(text)보다 작아야 함
            - 따라서 end==len(text)인 경우에는 reset(black) run을 추가하면 안 됨
            """
            if not text:
                return [{"startIndex": 0, "format": {"foregroundColor": DEFAULT_BLACK}}]

            runs_map = {0: {"foregroundColor": DEFAULT_BLACK}}

            L = len(text)

            for seg in segments:
                s = seg.get("start", -1)
                e = seg.get("end", -1)
                color = seg.get("color")

                if not isinstance(s, int) or not isinstance(e, int):
                    continue
                if not isinstance(color, dict):
                    continue
                if s < 0 or e <= s:
                    continue
                if s >= L:
                    continue
                if e > L:
                    e = L

                runs_map[s] = {"foregroundColor": color}
                # ✅ e==L이면 startIndex=L이 되어 API 에러 → reset run 추가 금지
                if e < L:
                    runs_map[e] = {"foregroundColor": DEFAULT_BLACK}

            runs = [{"startIndex": idx, "format": fmt} for idx, fmt in sorted(runs_map.items(), key=lambda x: x[0])]

            # 동일 색 연속 run 압축
            compact = []
            prev = None
            for r in runs:
                cur = r.get("format", {}).get("foregroundColor")
                if prev is None or cur != prev:
                    compact.append(r)
                    prev = cur
            return compact

        # ---------- 요청 생성 ----------
        for idx, it in enumerate(infos):
            row = it["행"]
            complex_name = row[COL_단지명] if len(row) > COL_단지명 else ""
            trade = row[COL_거래구분] if len(row) > COL_거래구분 else ""

            row_num = header_row + 1 + idx  # 1-based
            start_row = row_num - 1
            end_row = row_num

            # 배경색(기존 유지)
            bg_color = None
            if it.get("압구정원포함"):
                bg_color = COLOR_압구정원
            elif trade == "전세":
                bg_color = COLOR_전세
            elif trade == "월세":
                bg_color = COLOR_월세

            # ✅ 볼드 기준: 중복(중개업소 수 >=2)만
            is_bold = bool(it.get("중복여부"))

            if bg_color:
                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row,
                            "endRowIndex": end_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": col_count
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": bg_color
                            }
                        },
                        "fields": "userEnteredFormat.backgroundColor"
                    }
                })

            if is_bold:
                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row,
                            "endRowIndex": end_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": col_count
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True}
                            }
                        },
                        "fields": "userEnteredFormat.textFormat.bold"
                    }
                })

            # 중개업소 컬럼 부분색 적용
            if len(row) > COL_중개업소:
                joined = str(row[COL_중개업소] or "")

                if joined and complex_name not in EXCLUDED_COMPLEXES:
                    segments = []
                    # ✅ 탭 존재 판정은 knownCanonNames(없으면 validCanonNames fallback)
                    valid_canon = alias.get("knownCanonNames", alias.get("validCanonNames", set()))

                    # joined에서 콤마 단위 토큰의 "실제 위치"를 얻어 정확히 부분색을 적용한다.
                    # (normalize()된 문자열을 find()로 찾지 않기 때문에 공백/특수공백 차이로 인한 누락을 방지)
                    for mm in re.finditer(r"[^,]+", joined):
                        token = mm.group(0)

                        # 토큰 내부의 좌/우 공백을 제외한 실제 이름 구간 계산
                        left_ws = len(token) - len(token.lstrip())
                        right_ws = len(token) - len(token.rstrip())
                        name_raw = token.strip()
                        name = normalize(name_raw)
                        if not name:
                            continue

                        start = mm.start() + left_ws
                        end = mm.end() - right_ws

                        was_owner = it.get("상태", {}).get(name, False)

                        not_in_canon = name not in valid_canon
                        low = brokerage_counts.get(name, 0) <= LOW_FREQUENCY_THRESHOLD

                        # ✅ 우선순위:
                        # 1) 집주인 + (탭없음 또는 저빈도) => 보라
                        # 2) 집주인 => 초록
                        # 3) (탭없음 또는 저빈도) => 빨강
                        if was_owner and (not_in_canon or low):
                            color = COLOR_집주인_저빈도_TEXT
                        elif was_owner:
                            color = COLOR_집주인_TEXT
                        elif not_in_canon or low:
                            color = COLOR_저빈도_TEXT
                        else:
                            color = None

                        if color and 0 <= start < end <= len(joined):
                            segments.append({"start": start, "end": end, "color": color})

                    if segments:
                        text_runs = _make_text_format_runs(joined, segments)

                        requests.append({
                            "updateCells": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": start_row,
                                    "endRowIndex": end_row,
                                    "startColumnIndex": COL_중개업소,
                                    "endColumnIndex": COL_중개업소 + 1
                                },
                                "rows": [{
                                    "values": [{
                                        "userEnteredValue": {"stringValue": joined},
                                        "textFormatRuns": text_runs,
                                        "userEnteredFormat": {
                                            "textFormat": {"foregroundColor": DEFAULT_BLACK}
                                        }
                                    }]
                                }],
                                "fields": "userEnteredValue,textFormatRuns,userEnteredFormat.textFormat.foregroundColor"
                            }
                        })

        # 정렬(기존 유지)
        align_cols = min(col_count, 8)
        if align_cols > 0:
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": header_row,
                        "endRowIndex": header_row + row_count,
                        "startColumnIndex": 0,
                        "endColumnIndex": align_cols
                    },
                    "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                    "fields": "userEnteredFormat.horizontalAlignment"
                }
            })

        # ---------- batchUpdate 청크 실행 ----------
        if requests:
            print(f"[디버그] 스타일 요청 생성: 총 {len(requests)}개")
            CHUNK = 500
            for i in range(0, len(requests), CHUNK):
                chunk = requests[i:i+CHUNK]
                print(f"[디버그] batchUpdate 실행: {i+1} ~ {i+len(chunk)}")
                sheet_service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": chunk}
                ).execute()
            print("스타일 적용 완료")

    except Exception as e:
        print(f"스타일 적용 오류: {e}")
        import traceback
        traceback.print_exc()


def process_real_estate_data(spreadsheet, worksheet, sheet_service, spreadsheet_id):
    """데이터 정리 함수"""
    try:
        print("=== 부동산데이터처리 시작 ===")
        sheet_metadata = sheet_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()

        main_sheet = worksheet.title
        main_sheet_id = None
        for sheet in sheet_metadata.get('sheets', []):
            props = sheet['properties']
            if props.get('title') == main_sheet:
                main_sheet_id = props['sheetId']
                break

        if main_sheet_id is None:
            raise RuntimeError(
                f"대상 시트 '{main_sheet}'의 sheetId를 찾을 수 없습니다. "
                "첫 번째 시트로 대체하지 않고 안전하게 중단합니다."
            )

        print(f"시트명: {main_sheet}, Sheet ID: {main_sheet_id}")

        header_row = 1
        result = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{main_sheet}'!A:Z"
        ).execute()

        values = result.get('values', [])
        if len(values) <= header_row:
            print("처리할 데이터가 없습니다.")
            return

        last_row = len(values)
        print(f"마지막 행: {last_row}")

        header_col_count = 21

        # ✅ (통합) validCanonNames + knownCanonNames 포함
        alias = load_brokerage_alias_maps(sheet_service, spreadsheet_id)

        raw_data = values[header_row:]
        print(f"읽은 데이터 행수: {len(raw_data)}")

        sorted_data = sort_and_group_data(raw_data)
        print(f"정렬된 데이터 행수: {len(sorted_data)}")

        result_data = process_duplicate_listings(sorted_data, alias)
        final_data_rows = result_data["finalDataRows"]
        final_data_infos = result_data["finalDataInfos"]
        print(f"통합된 데이터 행수: {len(final_data_rows)}")

        brokerage_counts = get_brokerage_counts(final_data_rows)
        print(f"중개업소 종류 수: {len(brokerage_counts)}")

        col_count = len(final_data_rows[0]) if final_data_rows else header_col_count

        print("기존 데이터 및 서식 지우기 중...")
        estimated_max_rows = max(last_row, len(final_data_rows) + 500) if final_data_rows else last_row

        clear_requests = [{
            "repeatCell": {
                "range": {
                    "sheetId": main_sheet_id,
                    "startRowIndex": header_row,
                    "endRowIndex": estimated_max_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": col_count
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat"
            }
        }]

        sheet_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": clear_requests}
        ).execute()

        clear_range = f"'{main_sheet}'!{header_row + 1}:{estimated_max_rows}"
        sheet_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=clear_range
        ).execute()

        print("기존 데이터 및 서식 지우기 완료")

        if final_data_rows:
            for row in final_data_rows:
                while len(row) < col_count:
                    row.append("")

            write_range = f"'{main_sheet}'!{header_row + 1}:{header_row + len(final_data_rows)}"
            sheet_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=write_range,
                valueInputOption='USER_ENTERED',
                body={'values': final_data_rows}
            ).execute()
            print("데이터 쓰기 완료")

        # ✅ (통합) 안정형 표시(서식)
        apply_styles_and_alignment(
            sheet_service, spreadsheet_id, main_sheet_id,
            final_data_infos, header_row, col_count, brokerage_counts, alias
        )

        sales_spreadsheet_id = os.environ.get(
            "SALES_SPREADSHEET_ID",
            "1QP56lm5kPBdsUhrgcgY2U-JdmukXIkKCSxefd1QExKE"
        )
        inserted_count = insert_check_required_sales_listings(
            sheet_service, sales_spreadsheet_id,
            final_data_infos, brokerage_counts, alias
        )
        print(f"매매물건 목록 확인요망 자동삽입: {inserted_count}건")

        print("=== 부동산데이터처리 완료 ===")
        print(f"총 매물: {len(final_data_rows)}개")

    except Exception as e:
        print(f"오류 발생: {e}")
        import traceback
        traceback.print_exc()
        raise

# ====================================================================
# 메인 실행 함수
# ====================================================================
async def main():
    """메인 실행 함수 - 크롤링 + 데이터 정리"""
    print("\n" + "="*70)
    print("🚀 네이버 부동산 크롤러 + 데이터 정리 통합 실행")
    print(f"⏰ 시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")

    # 1) 구글 시트 연결
    debug_log("=== 1단계: 구글 시트 연결 ===", "STEP")
    worksheet, spreadsheet = setup_google_sheets()
    if not worksheet:
        raise RuntimeError("구글 시트 연결 실패")

    # Google Sheets API 서비스 생성 (데이터 정리용)
    credentials_file = 'service_account.json'
    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    sheet_service = build('sheets', 'v4', credentials=credentials)
    spreadsheet_id = os.environ.get('SPREADSHEET_ID', '1QP56lm5kPBdsUhrgcgY2U-JdmukXIkKCSxefd1QExKE')

    headers = [
        "단지명", "거래구분", "동", "층수", "면적", "가격",
        "가격변동", "중복업소",
        "중개업소", "중개업소ID",
        "등록일자", "방향", "특기사항",
        "제공", "집주인", "직거래",
        "사진 유무", "경도", "위도", "매물번호",
        "인증광고"
    ]
    # 2) 크롤링
    debug_log("=== 2단계: 크롤링 실행 ===", "STEP")
    results = []
    all_rows = []
    total_start_time = time.time()

    for idx, complex_info in enumerate(COMPLEXES, 1):
        debug_log(f"\n{'#'*70}", "STEP")
        debug_log(f"📍 [{idx}/{len(COMPLEXES)}] {complex_info['name']} ({complex_info['id']})", "STEP")
        debug_log(f"{'#'*70}", "STEP")

        complex_start_time = time.time()
        crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
        result = await crawler.run()
        complex_duration = time.time() - complex_start_time

        if 'error' in result or result.get('property_count', 0) <= 0:
            results.append({
                'complex_name': complex_info['name'],
                'property_count': 0,
                'duration_seconds': complex_duration,
                'status': 'error',
                'error': result.get('error', '수집 매물 0건')
            })
        else:
            property_count = result['property_count']
            rows_to_append = []
            if result.get('properties'):
                for prop in result['properties']:
                    formatted_row = format_property_data(prop)
                    if len(formatted_row) == len(headers):
                        rows_to_append.append(formatted_row)
            all_rows.extend(rows_to_append)
            debug_log(f"✅ {complex_info['name']} 매물 {len(rows_to_append)}개 임시 저장 완료", "SUCCESS")

            results.append({
                'complex_name': complex_info['name'],
                'property_count': property_count,
                'duration_seconds': complex_duration,
                'status': 'success'
            })

        if idx < len(COMPLEXES):
            await asyncio.sleep(0.2)

    # 3) 결과 검증 후에만 시트 교체
    successful_count = sum(1 for r in results if r['status'] == 'success')
    min_successful = int(os.getenv('MIN_SUCCESSFUL_COMPLEXES', str(len(COMPLEXES) - 2)))
    min_total = int(os.getenv('MIN_TOTAL_PROPERTIES', '100'))
    total_properties = sum(r['property_count'] for r in results)

    result_data = {
        'total_duration_seconds': time.time() - total_start_time,
        'start_time': datetime.fromtimestamp(total_start_time).strftime('%Y-%m-%d %H:%M:%S'),
        'successful_complexes': successful_count,
        'total_complexes': len(COMPLEXES),
        'total_properties': total_properties,
        'results': results
    }
    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    if successful_count < min_successful or total_properties < min_total:
        raise RuntimeError(
            f"수집 검증 실패: 성공 단지 {successful_count}/{len(COMPLEXES)} "
            f"(최소 {min_successful}), 매물 {total_properties}건 (최소 {min_total})"
        )

    debug_log("수집 검증 통과. 기존 시트를 교체합니다.", "SUCCESS")
    worksheet.clear()
    worksheet.append_row(headers)
    # API 요청 수를 줄이기 위해 단지별 쓰기 대신 최대 500행씩 일괄 기록
    for start in range(0, len(all_rows), 500):
        worksheet.append_rows(all_rows[start:start + 500], value_input_option='RAW')
    debug_log(f"✅ 구글 시트 원본 {len(all_rows)}행 기록 완료", "SUCCESS")

    # 4) 데이터 정리
    debug_log("\n=== 4단계: 데이터 정리 실행 ===", "STEP")
    try:
        process_real_estate_data(spreadsheet, worksheet, sheet_service, spreadsheet_id)
        debug_log("✅ 데이터 정리 완료", "SUCCESS")
    except Exception as e:
        debug_log(f"❌ 데이터 정리 실패: {str(e)}", "ERROR")
        debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
        raise

    # 5) 결과 요약
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time

    print("\n" + "="*70)
    print("📊 전체 결과 요약")
    print("="*70)
    print(f"⏰ 종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️ 전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"🏠 총 매물 수: {total_properties}개")
    print("="*70)

    result_data['total_duration_seconds'] = total_duration
    result_data['published_to_sheet'] = True

    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    print("\n" + "="*70)
    print("🎉 크롤링 및 데이터 정리 완료!")
    print("="*70 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
