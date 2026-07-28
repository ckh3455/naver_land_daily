test_external_with_five_ads_is_promoted_to_bad (test_broker_contacts.BrokerContactTests.test_external_with_five_ads_is_promoted_to_bad) ... ok
test_extracts_realtor_contact_fields (test_broker_contacts.BrokerContactTests.test_extracts_realtor_contact_fields) ... ok
test_same_address_rows_share_existing_canonical_name (test_broker_contacts.BrokerContactTests.test_same_address_rows_share_existing_canonical_name) ... [Broker] 활성 광고 5건 이상 외부업소 1행을 양아치업소로 자동 변경
[Broker] 연락처 갱신 2행, 외부업소 추가 0행, 주소/전화 동일업소 통합 1그룹
[Broker] 연락처 갱신 0행, 외부업소 추가 0행, 주소/전화 동일업소 통합 1그룹
[Broker] 연락처 갱신 0행, 외부업소 추가 1행, 주소/전화 동일업소 통합 0그룹
[Broker] 연락처 갱신 0행, 외부업소 추가 0행, 주소/전화 동일업소 통합 0그룹
ok
test_same_phone_groups_rows_and_preserves_bad_type (test_broker_contacts.BrokerContactTests.test_same_phone_groups_rows_and_preserves_bad_type) ... ok
test_unknown_broker_is_recorded_as_external (test_broker_contacts.BrokerContactTests.test_unknown_broker_is_recorded_as_external) ... ok
test_unknown_broker_without_contact_is_not_appended (test_broker_contacts.BrokerContactTests.test_unknown_broker_without_contact_is_not_appended) ... ok

----------------------------------------------------------------------
Ran 6 tests in 0.001s

OK
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

# 같은 실행 안에서 동일 중개업소를 여러 번 상세조회하지 않도록 공유 캐시 사용
BROKER_DETAIL_CACHE = {}
BROKER_DETAIL_ATTEMPTED = set()
BROKER_DETAIL_CONSECUTIVE_FAILURES = 0
BROKER_DETAIL_DISABLED = False

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

def _clean_detail_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()

def _first_detail_value(obj, keys):
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        value = _clean_detail_value(obj.get(key))
        if value:
            return value
    return ""

def _find_realtor_object(payload):
    """상세 응답 구조가 바뀌어도 중개업소 정보 객체를 최대한 찾아낸다."""
    if not isinstance(payload, dict):
        return {}

    for key in ("articleRealtor", "realtor", "realtorInfo", "agent", "office"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value

    queue = [payload]
    best = {}
    best_score = 0
    realtor_keys = {
        "realtorId", "realtorName", "representativeName",
        "representativeTelNo", "cellPhoneNo", "establishmentRegistrationNo",
        "realtorAddress", "officeAddress"
    }
    while queue:
        current = queue.pop(0)
        if not isinstance(current, dict):
            continue
        score = len(realtor_keys.intersection(current.keys()))
        if score > best_score:
            best = current
            best_score = score
        for value in current.values():
            if isinstance(value, dict):
                queue.append(value)
            elif isinstance(value, list):
                queue.extend(v for v in value if isinstance(v, dict))
    return best if best_score else {}

def _extract_broker_detail(payload, fallback=None):
    fallback = fallback if isinstance(fallback, dict) else {}
    realtor = _find_realtor_object(payload)

    realtor_id = _extract_realtor_id(realtor) or _extract_realtor_id(fallback)
    name = _first_detail_value(realtor, (
        "realtorName", "businessName", "officeName", "name"
    )) or _first_detail_value(fallback, ("realtorName",))
    address = _first_detail_value(realtor, (
        "realtorAddress", "officeAddress", "roadAddress",
        "address", "addressDetail"
    ))
    office_phone = _first_detail_value(realtor, (
        "representativeTelNo", "officePhoneNo", "telephoneNo",
        "telNo", "phoneNo", "realtorPhoneNo"
    ))
    mobile_phone = _first_detail_value(realtor, (
        "cellPhoneNo", "mobilePhoneNo", "mobileNo",
        "cellularPhoneNo", "representativeMobileNo"
    ))

    return {
        "id": _id_or_empty(realtor_id),
        "name": name,
        "address": address,
        "office_phone": office_phone,
        "mobile_phone": mobile_phone
    }

def _broker_cache_key(raw):
    realtor_id = _extract_realtor_id(raw)
    if realtor_id:
        return f"id:{realtor_id}"
    name = _clean_detail_value(raw.get("realtorName"))
    return f"name:{name}" if name else ""

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
    def __init__(self, complex_id, complex_name, collect_broker_details=True):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.base_url = f"https://new.land.naver.com/complexes/{self.complex_id}"
        self.property_cards = []
        self.unique_article_nos = set()
        self.page = None
        self.api_responses = []
        self.more_data = True
        self.collect_broker_details_enabled = collect_broker_details

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

    async def collect_broker_details(self):
        """
        중개업소 ID별 대표 매물 한 건만 상세조회한다.
        상세조회 실패가 전체 매물 수집 실패로 이어지지 않도록 보조 단계로 처리한다.
        """
        global BROKER_DETAIL_CONSECUTIVE_FAILURES, BROKER_DETAIL_DISABLED

        representatives = {}
        for prop in self.property_cards:
            raw = prop.get("raw_data", {})
            cache_key = _broker_cache_key(raw)
            if cache_key and cache_key not in representatives:
                representatives[cache_key] = prop

        new_count = 0
        detail_headers = None
        seeded_payloads = {}

        # 상세 API는 브라우저 화면에서 매물을 열 때 생성되는 인증 헤더가
        # 있어야 한다. 목록의 첫 매물을 실제로 한 번 클릭해 그 요청을 확보한다.
        if representatives and not BROKER_DETAIL_DISABLED:
            try:
                await self.page.evaluate("""
                    () => {
                      const container = document.querySelector('.item_list--article');
                      if (container) container.scrollTop = 0;
                    }
                """)
                await asyncio.sleep(0.8)
                first_link = self.page.locator(
                    '.item_list--article .item_link, '
                    '.item_list--article a[class*="item_link"]'
                ).first
                async with self.page.expect_response(
                    lambda r: bool(re.search(r'/api/articles/\d+(?:\?|$)', r.url))
                              and '/api/articles/complex/' not in r.url,
                    timeout=10000
                ) as response_info:
                    await first_link.click(timeout=5000)
                seed_response = await response_info.value
                if seed_response.ok:
                    seed_payload = await seed_response.json()
                    seed_article_match = re.search(
                        r'/api/articles/(\d+)', seed_response.url
                    )
                    if seed_article_match:
                        seeded_payloads[seed_article_match.group(1)] = seed_payload

                    request_headers = await seed_response.request.all_headers()
                    blocked_headers = {
                        'host', 'content-length', 'cookie', 'connection',
                        'accept-encoding', 'sec-fetch-site', 'sec-fetch-mode',
                        'sec-fetch-dest'
                    }
                    detail_headers = {
                        key: value
                        for key, value in request_headers.items()
                        if key.lower() not in blocked_headers
                        and not key.startswith(':')
                        and re.fullmatch(r'[!#$%&\'*+\-.^_`|~0-9A-Za-z]+', key)
                    }
                    debug_log(
                        "중개업소 상세조회 인증 준비 완료 "
                        f"(헤더: {', '.join(sorted(detail_headers.keys()))})",
                        "SUCCESS"
                    )
                await self.page.keyboard.press("Escape")
            except Exception as e:
                debug_log(
                    f"중개업소 상세조회 인증 준비 실패: {e}",
                    "WARNING"
                )

        total_representatives = len(representatives)
        for detail_index, (cache_key, prop) in enumerate(
            representatives.items(), 1
        ):
            raw = prop.get("raw_data", {})
            baseline = _extract_broker_detail(raw, raw)

            if cache_key in BROKER_DETAIL_CACHE:
                prop["broker_detail"] = BROKER_DETAIL_CACHE[cache_key]
                continue
            if cache_key in BROKER_DETAIL_ATTEMPTED:
                continue

            BROKER_DETAIL_ATTEMPTED.add(cache_key)
            article_no = _clean_detail_value(prop.get("article_no"))
            detail = baseline

            fetch_succeeded = False
            if article_no in seeded_payloads:
                fetched = _extract_broker_detail(seeded_payloads[article_no], raw)
                detail = {
                    key: fetched.get(key) or baseline.get(key, "")
                    for key in baseline.keys()
                }
                fetch_succeeded = True
                BROKER_DETAIL_CONSECUTIVE_FAILURES = 0
            elif article_no and detail_headers and not BROKER_DETAIL_DISABLED:
                for attempt in range(1, 3):
                    try:
                        payload = await self.page.evaluate(
                            """
                            async ({articleNo, headers}) => {
                              const controller = new AbortController();
                              const timer = setTimeout(() => controller.abort(), 10000);
                              try {
                                const response = await fetch(`/api/articles/${articleNo}`, {
                                  method: 'GET',
                                  credentials: 'include',
                                  headers,
                                  signal: controller.signal
                                });
                                if (!response.ok) {
                                  throw new Error(`HTTP ${response.status}`);
                                }
                                return await response.json();
                              } finally {
                                clearTimeout(timer);
                              }
                            }
                            """,
                            {
                                "articleNo": article_no,
                                "headers": detail_headers
                            }
                        )
                        fetched = _extract_broker_detail(payload, raw)
                        detail = {
                            key: fetched.get(key) or baseline.get(key, "")
                            for key in baseline.keys()
                        }
                        fetch_succeeded = True
                        BROKER_DETAIL_CONSECUTIVE_FAILURES = 0
                        break
                    except Exception as e:
                        debug_log(
                            f"중개업소 상세조회 실패 {cache_key} "
                            f"(시도 {attempt}/2): {e}",
                            "WARNING"
                        )
                        await asyncio.sleep(attempt)

                if not fetch_succeeded:
                    BROKER_DETAIL_CONSECUTIVE_FAILURES += 1
                    if BROKER_DETAIL_CONSECUTIVE_FAILURES >= 5:
                        BROKER_DETAIL_DISABLED = True
                        debug_log(
                            "중개업소 상세조회가 5곳 연속 실패하여 "
                            "이번 실행의 추가 상세조회를 중단합니다.",
                            "WARNING"
                        )
            elif article_no and not detail_headers:
                debug_log(
                    "상세조회 인증 헤더를 확보하지 못해 이번 단지의 "
                    "추가 중개업소 조회를 생략합니다.",
                    "WARNING"
                )

            BROKER_DETAIL_CACHE[cache_key] = detail
            prop["broker_detail"] = detail
            if detail.get("address") or detail.get("office_phone") or detail.get("mobile_phone"):
                new_count += 1
            if detail_index % 10 == 0 or detail_index == total_representatives:
                debug_log(
                    f"중개업소 상세조회 진행 "
                    f"{detail_index}/{total_representatives}곳",
                    "INFO"
                )
            await asyncio.sleep(0.15)

        # 동일 업소의 다른 매물에도 캐시된 상세정보 연결
        for prop in self.property_cards:
            cache_key = _broker_cache_key(prop.get("raw_data", {}))
            if cache_key and cache_key in BROKER_DETAIL_CACHE:
                prop["broker_detail"] = BROKER_DETAIL_CACHE[cache_key]

        debug_log(
            f"중개업소 상세정보 수집: 신규 {new_count}곳, "
            f"누적 캐시 {len(BROKER_DETAIL_CACHE)}곳",
            "SUCCESS"
        )

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
            if self.collect_broker_details_enabled:
                await self.collect_broker_details()
            else:
                debug_log(
                    "비교 단지는 중개업소 상세조회를 생략합니다.",
                    "INFO"
                )
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
APGUJEONG_COMPLEX_NAMES = {
    item["name"] for item in COMPLEXES
    if item["name"] not in EXCLUDED_COMPLEXES
}
ALIAS_SHEET_NAME = "압구정 중개업소"
ALIAS_HEADER_NAME = "중개업소명"
ALIAS_HEADER_ID = "중개업소ID"
ALIAS_HEADER_ID_FALLBACK = "ID"
ALIAS_HEADER_CANON = "실제상호"
ALIAS_HEADER_TYPE = "구분"
ALIAS_INTERNAL_TYPE = "압구정업소"
ALIAS_EXTERNAL_TYPE = "외부업소"
ALIAS_BAD_TYPE = "양아치업소"
ALIAS_TYPE_OPTIONS = [ALIAS_INTERNAL_TYPE, ALIAS_EXTERNAL_TYPE, ALIAS_BAD_TYPE]
LOW_FREQUENCY_THRESHOLD = 3

def normalize(v):
    if v is None:
        return ""
    s = str(v)
    # ✅ 제로폭 공백/보이지 않는 문자 제거 (탭 목록 매칭 실패 방지)
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
    # 모든 종류의 공백(탭/개행/연속 공백 등)을 1칸으로 축약
    return re.sub(r"\s+", " ", s).strip()

def _normalize_address_key(value):
    text = normalize(value)
    if not text:
        return ""
    text = text.replace("서울특별시", "서울")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()

def _normalize_phone_key(value):
    digits = re.sub(r"\D", "", normalize(value))
    return digits if len(digits) >= 8 else ""

def _apply_broker_type_dropdown(sheet_service, spreadsheet_id, row_count):
    metadata = sheet_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()
    sheet_id = next(
        sheet["properties"]["sheetId"]
        for sheet in metadata.get("sheets", [])
        if sheet["properties"]["title"] == ALIAS_SHEET_NAME
    )
    sheet_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": max(row_count, 1000),
                    "startColumnIndex": 6,
                    "endColumnIndex": 7
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": value}
                            for value in ALIAS_TYPE_OPTIONS
                        ]
                    },
                    "strict": True,
                    "showCustomUi": True
                }
            }
        }]}
    ).execute()

def _group_broker_rows(rows):
    """주소 또는 전화번호가 같은 행을 연결된 동일 업소 그룹으로 묶는다."""
    parent = list(range(len(rows)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    seen = {}
    for index, row in enumerate(rows):
        keys = []
        address = _normalize_address_key(row[3])
        office_phone = _normalize_phone_key(row[4])
        mobile_phone = _normalize_phone_key(row[5])
        if address:
            keys.append(f"address:{address}")
        if office_phone:
            keys.append(f"phone:{office_phone}")
        if mobile_phone:
            keys.append(f"phone:{mobile_phone}")
        for key in keys:
            if key in seen:
                union(index, seen[key])
            else:
                seen[key] = index

    groups = {}
    for index in range(len(rows)):
        groups.setdefault(find(index), []).append(index)
    return [indexes for indexes in groups.values() if len(indexes) > 1]

def update_brokerage_contact_sheet(sheet_service, spreadsheet_id, broker_details):
    """
    '압구정 중개업소'에 주소·사무실번호·휴대전화를 채운다.
    새로 발견한 업소도 추가하되 G열에 '외부업소'로 명시하여 판정에서 구분한다.
    동일 주소 또는 동일 전화번호 행은 첫 번째 기존 실제상호 기준으로 묶는다.
    """
    details = [d for d in broker_details if isinstance(d, dict)]
    if not details:
        print("[Broker] 기록할 중개업소 상세정보가 없습니다.")
        return

    result = sheet_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{ALIAS_SHEET_NAME}'!A:G"
    ).execute()
    values = result.get("values", [])
    if not values:
        values = [[
            "ID", "중개업소명", "실제상호", "주소",
            "사무실번호", "휴대전화", "구분"
        ]]

    headers = [
        "ID", "중개업소명", "실제상호", "주소",
        "사무실번호", "휴대전화", "구분"
    ]
    rows = []
    for source in values[1:]:
        row = list(source[:7])
        row.extend([""] * (7 - len(row)))
        if not normalize(row[6]):
            row[6] = ALIAS_INTERNAL_TYPE
        elif normalize(row[6]) not in ALIAS_TYPE_OPTIONS:
            row[6] = ALIAS_EXTERNAL_TYPE
        rows.append(row)

    updated = 0
    appended = 0
    for detail in details:
        realtor_id = _id_or_empty(detail.get("id"))
        name = normalize(detail.get("name"))
        if not realtor_id and not name:
            continue

        matching_indexes = [
            index for index, row in enumerate(rows)
            if (realtor_id and _id_or_empty(row[0]) == realtor_id)
            or (name and normalize(row[1]) == name)
        ]

        if not matching_indexes:
            has_contact = any(
                _clean_detail_value(detail.get(key))
                for key in ("address", "office_phone", "mobile_phone")
            )
            if not has_contact:
                # 상세조회 실패로 이름만 얻은 외부업소는 빈 행으로 누적하지 않는다.
                continue
            rows.append([
                realtor_id,
                name,
                name,
                _clean_detail_value(detail.get("address")),
                _clean_detail_value(detail.get("office_phone")),
                _clean_detail_value(detail.get("mobile_phone")),
                ALIAS_EXTERNAL_TYPE
            ])
            appended += 1
            continue

        for index in matching_indexes:
            row = rows[index]
            changed = False
            for column, key in (
                (3, "address"),
                (4, "office_phone"),
                (5, "mobile_phone")
            ):
                value = _clean_detail_value(detail.get(key))
                if value and row[column] != value:
                    row[column] = value
                    changed = True
            if changed:
                updated += 1

    grouped = 0
    for indexes in _group_broker_rows(rows):
        bad_indexes = [
            i for i in indexes
            if normalize(rows[i][6]) == ALIAS_BAD_TYPE
        ]
        internal_indexes = [
            i for i in indexes
            if normalize(rows[i][6]) == ALIAS_INTERNAL_TYPE
        ]
        canonical_source = bad_indexes or internal_indexes or indexes
        canon = next((
            normalize(rows[i][2])
            for i in canonical_source
            if normalize(rows[i][2])
        ), "")
        if not canon:
            canon = next((
                normalize(rows[i][1])
                for i in canonical_source
                if normalize(rows[i][1])
            ), "")

        shared_address = next((rows[i][3] for i in indexes if normalize(rows[i][3])), "")
        shared_office = next((rows[i][4] for i in indexes if normalize(rows[i][4])), "")
        shared_mobile = next((rows[i][5] for i in indexes if normalize(rows[i][5])), "")
        if bad_indexes:
            shared_type = ALIAS_BAD_TYPE
        elif internal_indexes:
            shared_type = ALIAS_INTERNAL_TYPE
        else:
            shared_type = ALIAS_EXTERNAL_TYPE

        for index in indexes:
            if canon:
                rows[index][2] = canon
            if shared_address:
                rows[index][3] = shared_address
            if shared_office and not normalize(rows[index][4]):
                rows[index][4] = shared_office
            if shared_mobile and not normalize(rows[index][5]):
                rows[index][5] = shared_mobile
            rows[index][6] = shared_type
        grouped += 1

    output = [headers] + rows
    sheet_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{ALIAS_SHEET_NAME}'!A:G"
    ).execute()
    sheet_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{ALIAS_SHEET_NAME}'!A1:G{len(output)}",
        valueInputOption="RAW",
        body={"values": output}
    ).execute()
    _apply_broker_type_dropdown(sheet_service, spreadsheet_id, len(output))
    print(
        f"[Broker] 연락처 갱신 {updated}행, 외부업소 추가 {appended}행, "
        f"주소/전화 동일업소 통합 {grouped}그룹"
    )

def prepare_brokerage_contact_sheet(sheet_service, spreadsheet_id):
    """
    사용자가 정리한 압구정업소·외부업소·양아치업소를 모두 보존하고
    구분값과 드롭다운만 정리한다.
    """
    result = sheet_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{ALIAS_SHEET_NAME}'!A:G"
    ).execute()
    values = result.get("values", [])
    headers = [
        "ID", "중개업소명", "실제상호", "주소",
        "사무실번호", "휴대전화", "구분"
    ]
    kept_rows = []
    for source in values[1:]:
        row = list(source[:7])
        row.extend([""] * (7 - len(row)))
        row_type = normalize(row[6]) or ALIAS_INTERNAL_TYPE
        row[6] = row_type if row_type in ALIAS_TYPE_OPTIONS else ALIAS_INTERNAL_TYPE
        kept_rows.append(row)

    output = [headers] + kept_rows
    sheet_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{ALIAS_SHEET_NAME}'!A:G"
    ).execute()
    sheet_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{ALIAS_SHEET_NAME}'!A1:G{len(output)}",
        valueInputOption="RAW",
        body={"values": output}
    ).execute()
    _apply_broker_type_dropdown(sheet_service, spreadsheet_id, len(output))
    print(
        f"[Broker] 사용자 분류 {len(kept_rows)}행 보존 및 "
        "구분 드롭다운 정리 완료"
    )

def promote_frequent_external_brokers(
    sheet_service,
    spreadsheet_id,
    counts_by_id,
    counts_by_name,
    threshold=5
):
    """현재 활성 광고가 기준 이상인 외부업소를 양아치업소로 자동 승격한다."""
    result = sheet_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{ALIAS_SHEET_NAME}'!A:G"
    ).execute()
    values = result.get("values", [])
    if len(values) < 2:
        return 0

    rows = []
    for source in values[1:]:
        row = list(source[:7])
        row.extend([""] * (7 - len(row)))
        rows.append(row)

    group_counts = {}
    counted_identities = set()
    for row in rows:
        realtor_id = _id_or_empty(row[0])
        name = normalize(row[1])
        canon = normalize(row[2]) or name
        identity = f"id:{realtor_id}" if realtor_id else f"name:{name}"
        if not canon or identity in counted_identities:
            continue
        counted_identities.add(identity)
        count = (
            counts_by_id.get(realtor_id, 0)
            if realtor_id
            else counts_by_name.get(name, 0)
        )
        group_counts[canon] = group_counts.get(canon, 0) + count

    promoted = 0
    for row in rows:
        if normalize(row[6]) != ALIAS_EXTERNAL_TYPE:
            continue
        canon = normalize(row[2]) or normalize(row[1])
        if group_counts.get(canon, 0) >= threshold:
            row[6] = ALIAS_BAD_TYPE
            promoted += 1

    if promoted:
        output = [values[0][:7]] + rows
        sheet_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{ALIAS_SHEET_NAME}'!A1:G{len(output)}",
            valueInputOption="RAW",
            body={"values": output}
        ).execute()
        _apply_broker_type_dropdown(sheet_service, spreadsheet_id, len(output))
        print(
            f"[Broker] 활성 광고 {threshold}건 이상 외부업소 "
            f"{promoted}행을 양아치업소로 자동 변경"
        )
    return promoted

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
        if ALIAS_HEADER_ID in header:
            idx_id = header.index(ALIAS_HEADER_ID)
        elif ALIAS_HEADER_ID_FALLBACK in header:
            idx_id = header.index(ALIAS_HEADER_ID_FALLBACK)
        else:
            idx_id = -1
        idx_canon = header.index(ALIAS_HEADER_CANON) if ALIAS_HEADER_CANON in header else -1
        idx_type = header.index(ALIAS_HEADER_TYPE) if ALIAS_HEADER_TYPE in header else -1

        if idx_canon < 0:
            return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names, "knownCanonNames": known_canon_names}

        for row in values[1:]:
            if len(row) <= idx_canon:
                continue
            row_type = normalize(row[idx_type]) if idx_type >= 0 and len(row) > idx_type else ""
            if row_type == ALIAS_EXTERNAL_TYPE:
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

        main_sheet = None
        main_sheet_id = None
        for sheet in sheet_metadata.get('sheets', []):
            props = sheet['properties']
            if props.get('sheetId') == 103445093:
                main_sheet = props['title']
                main_sheet_id = props['sheetId']
                break

        if not main_sheet:
            main_sheet = sheet_metadata['sheets'][0]['properties']['title']
            main_sheet_id = sheet_metadata['sheets'][0]['properties']['sheetId']

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

    # 사용자가 정리한 세 가지 분류를 모두 보존하고,
    # 구분 열의 드롭다운을 이 단계에서 즉시 적용한다.
    prepare_brokerage_contact_sheet(sheet_service, spreadsheet_id)

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
    active_broker_counts_by_id = {}
    active_broker_counts_by_name = {}
    total_start_time = time.time()

    for idx, complex_info in enumerate(COMPLEXES, 1):
        debug_log(f"\n{'#'*70}", "STEP")
        debug_log(f"📍 [{idx}/{len(COMPLEXES)}] {complex_info['name']} ({complex_info['id']})", "STEP")
        debug_log(f"{'#'*70}", "STEP")

        complex_start_time = time.time()
        crawler = AggressiveCardScroll(
            complex_info['id'],
            complex_info['name'],
            collect_broker_details=(
                complex_info["name"] in APGUJEONG_COMPLEX_NAMES
            )
        )
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

            # 연락처는 전체 크롤링 종료를 기다리지 않고 압구정 단지별로 저장한다.
            # 비교 단지의 중개업소는 '압구정 중개업소' 탭에 기록하지 않는다.
            if complex_info["name"] in APGUJEONG_COMPLEX_NAMES:
                for prop in result.get("properties", []):
                    raw = prop.get("raw_data", {})
                    realtor_id = _extract_realtor_id(raw)
                    broker_name = normalize(raw.get("realtorName"))
                    if realtor_id:
                        active_broker_counts_by_id[realtor_id] = (
                            active_broker_counts_by_id.get(realtor_id, 0) + 1
                        )
                    elif broker_name:
                        active_broker_counts_by_name[broker_name] = (
                            active_broker_counts_by_name.get(broker_name, 0) + 1
                        )

                complex_broker_details = [
                    prop.get("broker_detail")
                    for prop in result.get("properties", [])
                    if isinstance(prop.get("broker_detail"), dict)
                ]
                try:
                    update_brokerage_contact_sheet(
                        sheet_service,
                        spreadsheet_id,
                        complex_broker_details
                    )
                    promote_frequent_external_brokers(
                        sheet_service,
                        spreadsheet_id,
                        active_broker_counts_by_id,
                        active_broker_counts_by_name,
                        threshold=5
                    )
                except Exception as e:
                    debug_log(
                        f"{complex_info['name']} 중개업소 연락처 즉시 저장 실패: {e}",
                        "WARNING"
                    )

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
