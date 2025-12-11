#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러 (스크롤 방식 개선)
- 스크롤 컨테이너 방식으로 변경 (.item_list--article)
- 상세매물검색 버튼 클릭 방식 적용
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

# 23개 단지 목록
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
            return None

        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        gc = gspread.authorize(credentials)
        spreadsheet_id = os.environ.get('SPREADSHEET_ID', '1QP56lm5kPBdsUhrgcgY2U-JdmukXIkKCSxefd1QExKE')
        spreadsheet = gc.open_by_key(spreadsheet_id)

        try:
            worksheet = spreadsheet.worksheet("네이버 매물분석")
            return worksheet
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="네이버 매물분석", rows=1000, cols=30)
            return worksheet

    except Exception as e:
        debug_log(f"구글 시트 설정 실패: {str(e)}", "ERROR")
        debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
        return None


# =========================
# 파싱/보정 헬퍼
# =========================
def _truthy(val):
    """다양한 형식(True/'true'/'Y'/'1' 등)을 True로 인식"""
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("true", "y", "1", "yes")


def _to_int(val, default=0):
    """문자열/숫자 혼용된 count를 안전하게 정수 변환"""
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
    """중개업소 ID: 영문/숫자/._- 허용"""
    if val is None:
        return ""
    s = str(val).strip()
    if not s:
        return ""
    s = s.strip().strip('"').strip("'")
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    return s


def _to_text_cell(val):
    """구글시트에서 숫자/지수로 오인하지 않도록 텍스트로 강제"""
    s = _id_or_empty(val)
    return f"'{s}" if s else ""


def _extract_realtor_id(raw):
    """가능한 모든 위치/이름/URL에서 중개업소ID 추출"""
    candidate_keys = (
        "realtorId", "realtorIdStr", "realtorNo", "realEstateAgentNo",
        "agentNo", "realtorIdNo", "agentId", "officeId"
    )

    # 1) 최상위 키
    for k in candidate_keys:
        if k in raw and raw[k]:
            rid = _id_or_empty(raw[k])
            if rid:
                return rid

    # 2) 서브오브젝트
    for sub in ("realtor", "realtorInfo", "agent", "office"):
        obj = raw.get(sub)
        if isinstance(obj, dict):
            for k in candidate_keys:
                if k in obj and obj[k]:
                    rid = _id_or_empty(obj[k])
                    if rid:
                        return rid

    # 3) URL 쿼리
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
    """사진 보유 여부 판단"""
    for k in ("siteImageCount", "representativeImageCount", "imageCount"):
        if k in raw and _to_int(raw.get(k), 0) > 0:
            return True
    for k in ("siteImageCountYn", "representativeImageExistYn"):
        if k in raw and _truthy(raw.get(k)):
            return True
    return False


def _parse_price_number(s):
    """'12억 3,000' 형태 등을 대략 만원 단위 정수로 변환"""
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
    """가격변동 표시"""
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

    async def setup_playwright(self):
        """Playwright 환경 설정"""
        debug_log("=== Playwright 환경 설정 시작 ===", "STEP")
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
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
        """API 응답 모니터링 및 원본 데이터 저장"""
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
        """API 응답에서 매물 데이터 추출"""
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

            # isMoreData 상태 반영
            self.more_data = bool(data.get('isMoreData', False))
            debug_log(f"  📄 isMoreData: {self.more_data}", "DEBUG")
            return self.more_data

        return False

    async def navigate_to_complex_page(self):
        """단지 페이지로 이동 및 매물 탭 활성화"""
        debug_log("=== 단지 페이지 이동 시작 ===", "STEP")
        try:
            # 페이지 로드
            await self.page.goto(self.base_url, wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(3)

            # 상세매물검색 버튼 클릭
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
                    await asyncio.sleep(2)
                else:
                    debug_log("⚠️ 상세매물검색 버튼을 찾지 못함 (이미 활성화 상태일 수 있음)", "WARNING")
                    
            except Exception as e:
                debug_log(f"버튼 클릭 중 오류: {str(e)}", "WARNING")

            # 초기 API 응답 대기
            try:
                await self.page.wait_for_response(
                    lambda r: 'api/articles/complex' in r.url, 
                    timeout=5000
                )
            except:
                pass

        except Exception as e:
            debug_log(f"페이지 로드 실패: {str(e)}", "ERROR")
            raise

    async def aggressive_scroll(self):
        """개선된 스크롤: 컨테이너 높이 변화 감지 방식"""
        debug_log("=== 스크롤 컨테이너 방식 스크롤 시작 ===", "STEP")
        
        last_height = 0
        no_height_change_count = 0
        MAX_NO_CHANGE = 3  # 높이가 3번 연속 안 바뀌면 종료
        scroll_attempts = 0
        
        while self.more_data:
            prev_count = len(self.property_cards)
            
            try:
                # 현재 컨테이너 상태 확인
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
                
                # 높이 변화 확인
                if current_height == last_height and is_at_bottom:
                    no_height_change_count += 1
                    debug_log(f"⚠️ 컨테이너 높이 변화 없음 ({no_height_change_count}/{MAX_NO_CHANGE})", "WARNING")
                    
                    if no_height_change_count >= MAX_NO_CHANGE:
                        if not self.more_data:
                            debug_log("⏹️ isMoreData=False + 높이 변화 없음 → 종료", "INFO")
                            break
                        else:
                            debug_log("⚠️ 높이 변화 없지만 isMoreData=True → 10초 추가 대기", "WARNING")
                            await asyncio.sleep(10)
                            no_height_change_count = 0  # 리셋하고 재시도
                else:
                    no_height_change_count = 0
                    if current_height > last_height:
                        debug_log(f"📈 컨테이너 확장: {last_height} → {current_height} (+{current_height - last_height}px)", "SUCCESS")
                    last_height = current_height
                
                # 스크롤 실행
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
                
                # API 응답 적극 대기
                try:
                    await self.page.wait_for_response(
                        lambda r: 'api/articles/complex' in r.url and 'page=' in r.url,
                        timeout=3000
                    )
                    debug_log("📡 API 응답 감지!", "SUCCESS")
                except:
                    pass
                
                await asyncio.sleep(1.5)
                
                # 새 매물 확인
                if len(self.property_cards) > prev_count:
                    new_count = len(self.property_cards) - prev_count
                    debug_log(f"🎉 새 매물 {new_count}개 추가! (총 {len(self.property_cards)}개)", "SUCCESS")
                
                # 안전 장치 (최대 100회 스크롤 시도)
                if scroll_attempts >= 100:
                    debug_log(f"⛔ 안전중단: 100회 스크롤 시도 초과 (총 {len(self.property_cards)}개 수집)", "WARNING")
                    break
                    
            except Exception as e:
                debug_log(f"스크롤 중 오류: {str(e)}", "WARNING")
                await asyncio.sleep(2)
        
        debug_log(f"✅ 스크롤 완료 (총 {len(self.property_cards)}개 수집, {scroll_attempts}회 시도)", "SUCCESS")

    async def close_browser(self):
        """브라우저 종료"""
        try:
            if hasattr(self, 'browser'):
                await self.browser.close()
            if hasattr(self, 'playwright'):
                await self.playwright.stop()
        except Exception:
            pass

    async def run(self):
        """크롤러 실행"""
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
    """매물 데이터 포맷팅 (기존 방식 유지)"""
    raw_data = property_data.get('raw_data', {})

    # 면적
    area1 = raw_data.get('area1', '')
    area2 = raw_data.get('area2', '')
    if area1 and area2 and area1 != area2:
        area = f"{area1}/{area2}m²"
    elif area1:
        area = f"{area1}m²"
    else:
        area = raw_data.get('areaName', '') + "m²" or "Unknown"

    # 가격
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

    # 보완 필드
    price_change_display = _resolve_price_change(raw_data)
    is_owner_listing = "집주인" if property_data.get('is_owner_flag') is True else ""
    certification_ad = "인증광고" if _truthy(raw_data.get('tradeCheckedByOwner')) else ""
    direct_trade_listing = "직거래" if _truthy(raw_data.get('isDirectTrade')) else ""
    photo_status = "사진있음" if _has_photos(raw_data) else ""

    # 등록일
    date_str = raw_data.get('articleConfirmYmd', '')
    if date_str and len(str(date_str)) == 8 and str(date_str).isdigit():
        registration_date = f"{str(date_str)[:4]}.{str(date_str)[4:6]}.{str(date_str)[6:8]}"
    else:
        registration_date = date_str or "Unknown"

    # 중개업소ID
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


async def main():
    """메인 실행 함수"""
    print("\n" + "="*70)
    print("🚀 네이버 부동산 크롤러 시작 (개선된 스크롤 방식)")
    print(f"⏰ 시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")

    # 1) 구글 시트 연결
    debug_log("=== 1단계: 구글 시트 연결 및 초기화 ===", "STEP")
    worksheet = setup_google_sheets()
    if not worksheet:
        debug_log("구글 시트 연결 실패. 프로그램 종료", "ERROR")
        return

    debug_log("기존 데이터 삭제 중...", "DEBUG")
    worksheet.clear()
    debug_log("기존 데이터 삭제 완료", "SUCCESS")

    headers = [
        "단지명", "거래구분", "동", "층수", "면적", "가격",
        "가격변동", "중복업소",
        "중개업소", "중개업소ID",
        "등록일자", "방향", "특기사항",
        "제공", "집주인", "직거래",
        "사진 유무", "경도", "위도", "매물번호",
        "인증광고"
    ]
    debug_log(f"헤더 추가 중 (총 {len(headers)}개 열)", "DEBUG")
    worksheet.append_row(headers)
    debug_log("헤더 추가 완료", "SUCCESS")

    # 2) 크롤링
    debug_log("=== 2단계: 크롤링 실행 ===", "STEP")
    results = []
    total_start_time = time.time()

    for idx, complex_info in enumerate(COMPLEXES, 1):
        debug_log(f"\n{'#'*70}", "STEP")
        debug_log(f"📍 [{idx}/{len(COMPLEXES)}] {complex_info['name']} ({complex_info['id']})", "STEP")
        debug_log(f"{'#'*70}", "STEP")

        complex_start_time = time.time()

        crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
        result = await crawler.run()

        complex_duration = time.time() - complex_start_time

        if 'error' in result:
            results.append({
                'complex_name': complex_info['name'],
                'property_count': 0,
                'duration_seconds': complex_duration,
                'status': 'error',
                'error': result.get('error')
            })
        else:
            property_count = result['property_count']
            rows_to_append = []

            if result.get('properties'):
                for prop in result['properties']:
                    formatted_row = format_property_data(prop)
                    if len(formatted_row) == len(headers):
                        rows_to_append.append(formatted_row)

            if rows_to_append:
                worksheet.append_rows(rows_to_append)
                debug_log(f"✅ {complex_info['name']} 매물 {len(rows_to_append)}개 시트 기록 완료", "SUCCESS")

            results.append({
                'complex_name': complex_info['name'],
                'property_count': property_count,
                'duration_seconds': complex_duration,
                'status': 'success'
            })

        if idx < len(COMPLEXES):
            await asyncio.sleep(0.5)

    # 3) 결과 요약
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    total_properties = sum(r['property_count'] for r in results)

    print("\n" + "="*70)
    print("📊 전체 결과 요약")
    print("="*70)
    print(f"⏰ 종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️ 전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"🏠 총 매물 수: {total_properties}개")
    print("="*70)

    result_data = {
        'total_duration_seconds': total_duration,
        'start_time': datetime.fromtimestamp(total_start_time).strftime('%Y-%m-%d %H:%M:%S'),
        'total_properties': total_properties,
        'results': [{k: v for k, v in r.items() if k != 'properties'} for r in results]
    }
    
    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)

    print("\n" + "="*70)
    print("🎉 크롤링 완료!")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
