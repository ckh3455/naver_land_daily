#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러 (최종 통합 버전)
- '집주인' (verificationTypeCode: OWNER) 식별
- '경도', '위도' 기록
- '인증광고' (tradeCheckedByOwner: True, 'True', 'Y', '1' 등) 표기 로직 확장
- '중개업소ID' (realtorId) 및 '직거래' (isDirectTrade) 열 추가
- '방향' (direction), '사진 유무' (siteImageCount), '특기사항' (articleFeatureDesc) 열 추가
- '가격변동' (priceChangeState) 필드 값을 '상승'/'하락'으로 표시 (색상 시각화는 시트의 조건부 서식 권장)
- 총 20개 열
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

# 23개 단지 목록 (생략)
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
    {"id": "168097", "name": "메이플자이"}
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
        # 서비스 계정 파일 경로 설정
        credentials_file = 'service_account.json'
        
        if not os.path.exists(credentials_file):
            debug_log(f"서비스 계정 파일이 없습니다: {credentials_file}", "ERROR")
            return None
        
        # 자격 증명 설정
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        gc = gspread.authorize(credentials)
        spreadsheet_id = os.environ.get('SPREADSHEET_ID', '1QP56lm5kPBdsUhrgcgY2U-JdmukXIkKCSxefd1QExKE')
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        # 워크시트 가져오기 또는 새로 생성
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


class AggressiveCardScroll:
    """네이버 부동산 매물 크롤러 (API 응답 기반 최적화)"""
    
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.base_url = f"https://new.land.naver.com/complexes/{self.complex_id}"
        self.property_cards = []
        self.unique_article_nos = set()
        self.page = None
        self.api_responses = []

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
        """API 응답에서 매물 데이터 추출 및 '집주인' 플래그 설정"""
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
                        
                        # 'verificationTypeCode' 필드를 이용해 '집주인' 여부 확인
                        is_owner = article.get('verificationTypeCode') == 'OWNER'
                        
                        property_data = {
                            'complex_id': self.complex_id,
                            'complex_name': self.complex_name,
                            'article_no': article_no,
                            'raw_data': article,
                            'extracted_at': datetime.now().isoformat(),
                            'card_number': len(self.property_cards) + 1,
                            'page_number': page_num,
                            'is_owner_flag': is_owner # API 필드 기반으로 플래그 설정
                        }
                        self.property_cards.append(property_data)
                        new_properties += 1
                        
            if new_properties > 0:
                debug_log(f"  ➕ API 응답에서 {new_properties}개 매물 수집됨 (총 {len(self.property_cards)}개)", "SUCCESS")
            
            return data.get('isMoreData', False)
        
        return False
    
    async def navigate_to_complex_page(self):
        """단지 페이지로 이동"""
        debug_log("=== 단지 페이지 이동 시작 ===", "STEP")
        
        try:
            await self.page.goto(self.base_url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(2)
            
            # 매물/시세 탭으로 이동 시도
            try:
                await self.page.click('text="매물/시세"', timeout=5000)
                await asyncio.sleep(3)
            except Exception:
                 pass
            # 실거래가 탭으로 이동 시도 (API 호출 유발 목적)
            try:
                await self.page.click('text="실거래가"', timeout=5000)
                await asyncio.sleep(3)
            except Exception:
                pass
            
        except Exception as e:
            debug_log(f"페이지 로드 실패: {str(e)}", "ERROR")
            raise

    async def aggressive_scroll(self):
        """적극적인 스크롤 (API 요청 유발)"""
        debug_log("=== 적극적인 스크롤 시작 ===", "STEP")
        
        consecutive_no_change = 0
        
        # 최대 30 라운드 스크롤 시도
        for i in range(30):
            debug_log(f"--- 스크롤 라운드 {i+1}/30 ---", "STEP")
            
            current_count = len(self.property_cards)
            
            try:
                # 다양한 스크롤 방법 시도
                scroll_methods = [
                    "window.scrollTo(0, document.body.scrollHeight);",
                    "window.scrollBy(0, 1000);",
                    """
                    const articleList = document.querySelector('[class*="article"]') || document.querySelector('.list_area');
                    if (articleList) { articleList.scrollTop = articleList.scrollHeight; }
                    """
                ]
                for method in scroll_methods:
                    await self.page.evaluate(method)
                    await asyncio.sleep(0.2)
                await self.page.keyboard.press('End') # End 키로 API 추가 호출 유도
                await asyncio.sleep(0.5)
                
            except Exception as e:
                debug_log(f"스크롤 중 오류: {str(e)}", "WARNING")
            
            new_count = len(self.property_cards)
            if new_count > current_count:
                debug_log(f"🎉 새로운 매물 {new_count - current_count}개 추가됨! (총 {new_count}개)", "SUCCESS")
                consecutive_no_change = 0
            else:
                consecutive_no_change += 1
                debug_log(f"새로운 매물 없음 (연속 {consecutive_no_change}회)", "WARNING")
            
            if consecutive_no_change >= 3:
                debug_log(f"⏹️  연속 {consecutive_no_change}회 변화 없음. 스크롤 종료", "INFO")
                break
            
            await asyncio.sleep(1)

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
    """
    매물 데이터 포맷팅 및 새로운 필드 ('방향', '사진유무', '특기사항', '가격변동', '중개업소ID', '직거래') 적용
    총 20개 열을 반환합니다.
    """
    raw_data = property_data.get('raw_data', {})
    
    # -------------------
    # 면적 정보 처리
    # -------------------
    area1 = raw_data.get('area1', '')
    area2 = raw_data.get('area2', '')
    if area1 and area2 and area1 != area2:
        area = f"{area1}/{area2}m²"
    elif area1:
        area = f"{area1}m²"
    else:
        area = raw_data.get('areaName', '') + "m²" or "Unknown"
    
    # -------------------
    # 가격 정보 처리
    # -------------------
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
    
    # -------------------
    # 필드값 설정 및 요청된 로직 적용
    # -------------------
    
    # 1. 가격변동 (priceChangeState)
    price_change_state_raw = raw_data.get('priceChangeState', '')
    price_change_display = ""
    if price_change_state_raw == "UP":
        price_change_display = "상승"
    elif price_change_state_raw == "DOWN":
        price_change_display = "하락"
    
    # 2. 집주인 (verificationTypeCode)
    is_owner_listing = "집주인" if property_data.get('is_owner_flag') is True else ""
    
    # 3. 인증광고 (tradeCheckedByOwner)
    trade_checked_by_owner = raw_data.get('tradeCheckedByOwner')
    is_certified = (trade_checked_by_owner is True or 
                    str(trade_checked_by_owner).upper() in ['TRUE', 'Y', '1'])
    certification_ad = "인증광고" if is_certified else ""
    
    # 4. 직거래 (isDirectTrade)
    is_direct_trade = raw_data.get('isDirectTrade')
    direct_trade_listing = "직거래" if is_direct_trade is True else ""

    # 5. 사진 유무 (siteImageCount)
    site_image_count = raw_data.get('siteImageCount', 0)
    photo_status = "사진있음" if site_image_count >= 1 else ""
    
    # 6. 등록일자 형식 변환
    date_str = raw_data.get('articleConfirmYmd', '')
    if date_str and len(date_str) == 8 and date_str.isdigit():
        registration_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"
    else:
        registration_date = date_str or "Unknown"

    # -------------------
    # 최종 20개 열 데이터 리스트 생성
    # -------------------
    return [
        property_data.get('complex_name', ''),  # 1. 단지명
        trade_type,                             # 2. 거래구분
        raw_data.get('buildingName', ''),       # 3. 동
        raw_data.get('floorInfo', ''),          # 4. 층수
        area,                                   # 5. 면적
        price,                                  # 6. 가격
        price_change_display,                   # 7. 가격변동 (상승/하락)
        1,                                      # 8. 중복업소 (고정)
        raw_data.get('realtorName', 'Unknown'), # 9. 중개업소
        raw_data.get('realtorId', ''),          # 10. 중개업소ID
        registration_date,                      # 11. 등록일자
        raw_data.get('direction', ''),          # 12. 방향
        raw_data.get('articleFeatureDesc', ''), # 13. 특기사항
        raw_data.get('cpName', 'Unknown'),      # 14. 제공
        is_owner_listing,                       # 15. 집주인
        direct_trade_listing,                   # 16. 직거래
        photo_status,                           # 17. 사진 유무
        raw_data.get('longitude', ''),          # 18. 경도
        raw_data.get('latitude', ''),           # 19. 위도
        raw_data.get('articleNo', ''),          # 20. 매물번호
        certification_ad                        # 21. 인증광고 (총 21개)
    ]


async def main():
    """메인 실행 함수 (디버깅 출력 및 단지별 즉시 기록 통합)"""
    print("\n" + "="*70)
    print("🚀 네이버 부동산 크롤러 시작")
    print(f"⏰ 시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")
    
    # === 1단계: 구글 시트 연결 및 초기화 ===
    debug_log("=== 1단계: 구글 시트 연결 및 초기화 ===", "STEP")
    worksheet = setup_google_sheets()
    if not worksheet:
        debug_log("구글 시트 연결 실패. 프로그램 종료", "ERROR")
        return
    
    debug_log("기존 데이터 삭제 중...", "DEBUG")
    worksheet.clear()
    debug_log("기존 데이터 삭제 완료", "SUCCESS")
    
    # ⭐ 최종 21개 열 헤더 정의
    headers = [
        "단지명", "거래구분", "동", "층수", "면적", "가격", 
        "가격변동", # 7
        "중복업소", 
        "중개업소", "중개업소ID", # 10
        "등록일자", 
        "방향", # 12
        "특기사항", # 13
        "제공", "집주인", 
        "직거래", # 16
        "사진 유무", # 17
        "경도", "위도", "매물번호", 
        "인증광고" # 21
    ]
    debug_log(f"헤더 추가 중 (총 {len(headers)}개 열): {headers}", "DEBUG")
    worksheet.append_row(headers)
    debug_log("헤더 추가 완료", "SUCCESS")
    
    # === 2단계: 크롤링 및 단지별 즉시 기록 ===
    debug_log("=== 2단계: 크롤링 실행 및 단지별 기록 ===", "STEP")
    results = []  
    total_start_time = time.time()
    
    for idx, complex_info in enumerate(COMPLEXES, 1):
        debug_log(f"\n{'#'*70}", "STEP")
        debug_log(f"📍 진행: [{idx}/{len(COMPLEXES)}] {complex_info['name']} ({complex_info['id']})", "STEP")
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
            
            # ⭐ [디버깅 로직]: 수집된 매물 샘플 5개 출력
            if result.get('properties'):
                debug_log(f"\n🔍 {complex_info['name']} - 디버깅 샘플 출력 (최대 5개):", "DEBUG")
                
                for i, prop in enumerate(result['properties']):
                    raw_data = prop.get('raw_data', {})
                    formatted_row = format_property_data(prop)
                    
                    if i < 5:
                        # 디버그 로그 출력
                        debug_log(f"--- 매물 샘플 #{i+1} (Article No: {prop.get('article_no', 'N/A')}) ---", "INFO")
                        debug_log(f"Raw Data 필드: priceChangeState = {raw_data.get('priceChangeState', 'N/A')}", "INFO")
                        debug_log(f"Raw Data 필드: tradeCheckedByOwner = {raw_data.get('tradeCheckedByOwner', 'N/A')}", "INFO") 
                        debug_log(f"Raw Data 필드: isDirectTrade = {raw_data.get('isDirectTrade', 'N/A')}", "INFO") 
                        
                        # 요청에 따라 tradeCheckedByOwner가 false인 경우 로그 기록
                        if raw_data.get('tradeCheckedByOwner') is False:
                             debug_log(f"⚠️  tradeCheckedByOwner 값이 False로 기록됨.", "WARNING")
                        
                        # 최종 시트 데이터 미리보기 (가격변동 포함)
                        debug_log(f"최종 데이터 (가격변동: {formatted_row[6]}, 방향: {formatted_row[11]}, 사진: {formatted_row[16]})", "DEBUG")

                    # 데이터 포맷팅 및 시트 추가
                    if len(formatted_row) == len(headers): 
                        rows_to_append.append(formatted_row)
            
            # 🚀 해당 단지의 매물을 시트에 즉시 기록
            if rows_to_append:
                worksheet.append_rows(rows_to_append)
                debug_log(f"✅ {complex_info['name']} 매물 {len(rows_to_append)}개 시트 기록 완료.", "SUCCESS")
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': property_count,
                'duration_seconds': complex_duration,
                'status': 'success'
            })
        
        # 다음 단지 실행 전 대기
        if idx < len(COMPLEXES):
            debug_log("다음 단지까지 5초 대기...", "DEBUG")
            await asyncio.sleep(5)
    
    # === 3단계: 전체 결과 요약 및 저장 ===
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    total_properties = sum(r['property_count'] for r in results)
    
    print("\n" + "="*70)
    print("📊 전체 결과 요약")
    print("="*70)
    print(f"⏰ 종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️  전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"🏠 총 매물 수: {total_properties}개")
    print("="*70)
    
    # JSON 파일 저장
    result_data = {
        'total_duration_seconds': total_duration,
        'start_time': datetime.fromtimestamp(total_start_time).strftime('%Y-%m-%d %H:%M:%S'),
        'total_properties': total_properties,
        'results': [{k:v for k,v in r.items() if k != 'properties'} for r in results]
    }
    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    
    print("\n" + "="*70)
    print("🎉 크롤링 완료!")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
