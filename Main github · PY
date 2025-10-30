#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러 (검증된 로직 + 초상세 디버깅)
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
        credentials_file = 'service_account.json'
        debug_log(f"서비스 계정 파일 확인: {credentials_file}", "DEBUG")
        
        if not os.path.exists(credentials_file):
            debug_log(f"서비스 계정 파일이 없습니다: {credentials_file}", "ERROR")
            return None
        
        file_size = os.path.getsize(credentials_file)
        debug_log(f"서비스 계정 파일 크기: {file_size} bytes", "DEBUG")
        
        debug_log("서비스 계정 인증 시작...", "DEBUG")
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        debug_log("서비스 계정 인증 완료", "SUCCESS")
        
        debug_log("gspread 클라이언트 생성 중...", "DEBUG")
        gc = gspread.authorize(credentials)
        debug_log("gspread 클라이언트 생성 완료", "SUCCESS")
        
        spreadsheet_id = os.environ.get('SPREADSHEET_ID', '1QP56lm5kPBdsUhrgcgY2U-JdmukXIkKCSxefd1QExKE')
        debug_log(f"스프레드시트 ID: {spreadsheet_id}", "DEBUG")
        
        debug_log("스프레드시트 열기 시도...", "DEBUG")
        spreadsheet = gc.open_by_key(spreadsheet_id)
        debug_log(f"스프레드시트 열기 성공: {spreadsheet.title}", "SUCCESS")
        
        try:
            debug_log("'네이버 매물분석' 워크시트 찾기 시도...", "DEBUG")
            worksheet = spreadsheet.worksheet("네이버 매물분석")
            debug_log("기존 워크시트 발견", "SUCCESS")
            return worksheet
        except gspread.WorksheetNotFound:
            debug_log("워크시트 없음. 새로 생성 중...", "WARNING")
            worksheet = spreadsheet.add_worksheet(title="네이버 매물분석", rows=1000, cols=20)
            debug_log("워크시트 생성 완료", "SUCCESS")
            return worksheet
            
    except Exception as e:
        debug_log(f"구글 시트 설정 실패: {str(e)}", "ERROR")
        debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
        return None


class AggressiveCardScroll:
    """검증된 네이버 부동산 매물 크롤러 (원본 로직 유지)"""
    
    def __init__(self, complex_id, complex_name):
        debug_log(f"크롤러 초기화: {complex_name} (ID: {complex_id})", "DEBUG")
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.base_url = f"https://new.land.naver.com/complexes/{self.complex_id}"
        self.property_cards = []
        self.unique_article_nos = set()
        self.page = None
        self.api_responses = []
        debug_log(f"크롤러 초기화 완료. URL: {self.base_url}", "SUCCESS")

    async def setup_playwright(self):
        """Playwright 환경 설정"""
        debug_log("=== Playwright 환경 설정 시작 ===", "STEP")
        
        try:
            debug_log("Playwright 인스턴스 시작...", "DEBUG")
            self.playwright = await async_playwright().start()
            debug_log("Playwright 인스턴스 시작 완료", "SUCCESS")
            
            debug_log("Chromium 브라우저 실행 중...", "DEBUG")
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
            debug_log("브라우저 실행 완료", "SUCCESS")
            
            debug_log("브라우저 컨텍스트 생성 중...", "DEBUG")
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
            debug_log("브라우저 컨텍스트 생성 완료", "SUCCESS")
            
            debug_log("새 페이지 생성 중...", "DEBUG")
            self.page = await self.context.new_page()
            self.page.on('response', self.handle_response)
            debug_log("새 페이지 생성 완료 및 응답 리스너 등록", "SUCCESS")
            
        except Exception as e:
            debug_log(f"Playwright 설정 실패: {str(e)}", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            raise

    async def handle_response(self, response):
        """API 응답 모니터링 (검증된 로직)"""
        url = response.url
        
        # 매물 관련 API 응답만 저장
        if 'api/articles/complex' in url and 'page=' in url:
            try:
                debug_log(f"API 응답 감지: {url}", "DEBUG")
                data = await response.json()
                self.api_responses.append({
                    'url': url,
                    'data': data,
                    'timestamp': datetime.now().isoformat()
                })
                debug_log(f"API 응답 저장 완료 (총 {len(self.api_responses)}개)", "DEBUG")
                
                # 매물 데이터 추출
                await self.extract_properties_from_response(data, url)
                
            except Exception as e:
                debug_log(f"API 응답 파싱 실패: {url} - {str(e)}", "WARNING")

    async def extract_properties_from_response(self, data, url):
        """API 응답에서 매물 데이터 추출 (검증된 로직)"""
        if isinstance(data, dict) and 'articleList' in data:
            articles = data['articleList']
            page_match = re.search(r'page=(\d+)', url)
            page_num = page_match.group(1) if page_match else "Unknown"
            
            debug_log(f"페이지 {page_num}에서 {len(articles)}개 매물 발견", "INFO")
            
            new_properties = 0
            for article in articles:
                if isinstance(article, dict):
                    article_no = article.get('articleNo', '')
                    if article_no and article_no not in self.unique_article_nos:
                        self.unique_article_nos.add(article_no)
                        
                        property_data = {
                            'complex_id': self.complex_id,
                            'complex_name': self.complex_name,
                            'article_no': article_no,
                            'raw_data': article,
                            'extracted_at': datetime.now().isoformat(),
                            'card_number': len(self.property_cards) + 1,
                            'page_number': page_num
                        }
                        self.property_cards.append(property_data)
                        new_properties += 1
                        
                        # 주요 정보 추출
                        dong = article.get('buildingName', '')
                        trade_type = article.get('tradeTypeName', '')
                        price = article.get('dealOrWarrantPrc', '')
                        
                        debug_log(f"  새 매물 #{len(self.property_cards)} (번호: {article_no}): {dong} - {trade_type} {price}", "DEBUG")
            
            if new_properties > 0:
                debug_log(f"  ➕ {new_properties}개 새 매물 추가됨 (총 {len(self.property_cards)}개)", "SUCCESS")
            
            # isMoreData 확인
            is_more_data = data.get('isMoreData', False)
            debug_log(f"  isMoreData: {is_more_data}", "DEBUG")
            return is_more_data
        
        return False

    async def navigate_to_complex_page(self):
        """단지 페이지로 이동 (검증된 로직)"""
        debug_log("=== 단지 페이지 이동 시작 ===", "STEP")
        debug_log(f"대상 URL: {self.base_url}", "INFO")
        
        try:
            debug_log("페이지 로드 시도 (타임아웃: 60초)...", "DEBUG")
            await self.page.goto(self.base_url, wait_until='networkidle', timeout=60000)
            debug_log("페이지 로드 완료", "SUCCESS")
            
            debug_log("초기 대기 (2초)...", "DEBUG")
            await asyncio.sleep(2)
            
            # 페이지 상태 확인
            title = await self.page.title()
            debug_log(f"페이지 제목: {title}", "INFO")
            
            # 매물/시세 탭 클릭
            try:
                debug_log("'매물/시세' 탭 클릭 시도...", "DEBUG")
                await self.page.click('text="매물/시세"', timeout=5000)
                debug_log("'매물/시세' 탭 클릭 성공", "SUCCESS")
                await asyncio.sleep(3)
            except Exception as e:
                debug_log(f"'매물/시세' 탭 클릭 실패: {str(e)}", "WARNING")

            # 실거래가 탭 클릭
            try:
                debug_log("'실거래가' 탭 클릭 시도...", "DEBUG")
                await self.page.click('text="실거래가"', timeout=5000)
                debug_log("'실거래가' 탭 클릭 성공", "SUCCESS")
                await asyncio.sleep(3)
            except Exception as e:
                debug_log(f"'실거래가' 탭 클릭 실패: {str(e)}", "WARNING")
            
            # 최종 URL 확인
            current_url = self.page.url
            debug_log(f"현재 URL: {current_url}", "INFO")
            
        except Exception as e:
            debug_log(f"페이지 로드 실패: {str(e)}", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            raise

    async def aggressive_scroll(self):
        """적극적인 스크롤 (검증된 로직)"""
        debug_log("=== 적극적인 스크롤 시작 ===", "STEP")
        
        consecutive_no_change = 0
        
        for i in range(30):  # 최대 30번 스크롤
            debug_log(f"--- 스크롤 라운드 {i+1}/30 ---", "STEP")
            
            # 현재 매물 수 기록
            current_count = len(self.property_cards)
            debug_log(f"현재 수집된 매물: {current_count}개", "INFO")
            
            try:
                # 다양한 스크롤 방법 시도
                scroll_methods = [
                    "window.scrollTo(0, document.body.scrollHeight);",
                    "window.scrollBy(0, 1000);",
                    "window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});",
                    """
                    const articleList = document.querySelector('[class*="article"]') || 
                                      document.querySelector('.item_list') ||
                                      document.querySelector('[data-testid*="article"]') ||
                                      document.querySelector('.list_area');
                    if (articleList) {
                        articleList.scrollTop = articleList.scrollHeight;
                    }
                    """,
                    """
                    const scrollContainers = document.querySelectorAll('[style*="overflow"], [class*="scroll"]');
                    scrollContainers.forEach(container => {
                        if (container.scrollHeight > container.clientHeight) {
                            container.scrollTop = container.scrollHeight;
                        }
                    });
                    """
                ]
                
                debug_log(f"JavaScript 스크롤 실행 ({len(scroll_methods)}개 방법)...", "DEBUG")
                for method in scroll_methods:
                    await self.page.evaluate(method)
                    await asyncio.sleep(0.2)
                
                debug_log("마우스 휠 스크롤 실행...", "DEBUG")
                await self.page.mouse.wheel(0, 2000)
                await asyncio.sleep(0.3)
                
                debug_log("키보드 스크롤 실행...", "DEBUG")
                await self.page.keyboard.press('End')
                await asyncio.sleep(0.2)
                
                for _ in range(3):
                    await self.page.keyboard.press('PageDown')
                    await asyncio.sleep(0.1)
                
                for _ in range(5):
                    await self.page.keyboard.press('ArrowDown')
                    await asyncio.sleep(0.05)
                
            except Exception as e:
                debug_log(f"스크롤 중 오류: {str(e)}", "WARNING")
            
            # 새로운 매물 확인
            new_count = len(self.property_cards)
            if new_count > current_count:
                added = new_count - current_count
                debug_log(f"🎉 새로운 매물 {added}개 추가됨! (총 {new_count}개)", "SUCCESS")
                consecutive_no_change = 0
            else:
                consecutive_no_change += 1
                debug_log(f"새로운 매물 없음 (연속 {consecutive_no_change}회)", "WARNING")
            
            debug_log(f"총 API 응답: {len(self.api_responses)}개", "INFO")
            
            # 5번 연속 변화 없으면 중단
            if consecutive_no_change >= 5:
                debug_log(f"⏹️  연속 {consecutive_no_change}회 변화 없음. 스크롤 종료", "INFO")
                break
            
            debug_log("대기 (0.5초)...", "DEBUG")
            await asyncio.sleep(0.5)

    async def close_browser(self):
        """브라우저 종료"""
        debug_log("브라우저 종료 중...", "DEBUG")
        try:
            if hasattr(self, 'browser'):
                await self.browser.close()
                debug_log("브라우저 종료 완료", "SUCCESS")
            if hasattr(self, 'playwright'):
                await self.playwright.stop()
                debug_log("Playwright 종료 완료", "SUCCESS")
        except Exception as e:
            debug_log(f"브라우저 종료 중 오류: {str(e)}", "WARNING")

    async def run(self):
        """크롤러 실행 (검증된 로직)"""
        debug_log(f"\n{'='*70}", "STEP")
        debug_log(f"🏢 {self.complex_name} 크롤링 시작", "STEP")
        debug_log(f"{'='*70}", "STEP")
        
        try:
            await self.setup_playwright()
            await self.navigate_to_complex_page()
            await self.aggressive_scroll()

            debug_log("\n📊 수집 완료 요약:", "STEP")
            debug_log(f"  - 단지: {self.complex_name} (ID: {self.complex_id})", "INFO")
            debug_log(f"  - 고유 매물: {len(self.property_cards)}개", "INFO")
            debug_log(f"  - API 응답: {len(self.api_responses)}개", "INFO")

            await self.close_browser()
            
            return {
                'complex_id': self.complex_id,
                'complex_name': self.complex_name,
                'property_count': len(self.property_cards),
                'api_responses': len(self.api_responses),
                'properties': self.property_cards
            }
            
        except Exception as e:
            debug_log(f"크롤링 중 치명적 오류 발생: {str(e)}", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            await self.close_browser()
            raise


def format_property_data(property_data):
    """매물 데이터 포맷팅 (검증된 로직)"""
    raw_data = property_data.get('raw_data', {})
    
    # 면적 정보
    area_name = raw_data.get('areaName', '')
    area1 = raw_data.get('area1', '')
    area2 = raw_data.get('area2', '')
    
    if not area_name:
        area = "Unknown"
    elif area1 and area2 and area1 != area2:
        area = f"{area1}/{area2}m²"
    elif area1:
        area = f"{area1}m²"
    else:
        area = f"{area_name}m²"
    
    # 특기사항
    special_notes = []
    direction = raw_data.get('direction', '')
    if direction:
        special_notes.append(f"방향: {direction}")
    
    feature_desc = raw_data.get('articleFeatureDesc', '')
    if feature_desc:
        if "제공" in feature_desc:
            feature_desc = feature_desc.split("제공")[0].strip()
        special_notes.append(feature_desc)
    
    tag_list = raw_data.get('tagList', [])
    if tag_list:
        tags = " | ".join(tag_list)
        special_notes.append(f"태그: {tags}")
    
    special_notes_str = " | ".join(special_notes) if special_notes else ""
    
    # 중개업소명 정리
    broker_name = raw_data.get('realtorName', '')
    if broker_name and broker_name != "Unknown":
        remove_strings = ['공인중개사사무소', '(주)', '중개법인', '주식회사', '부동산중개', 
                         '부동산중개법인주식회사', '부동산중개법인', '공인중개사', '부동산']
        for remove_str in remove_strings:
            broker_name = broker_name.replace(remove_str, '')
        broker_name = re.sub(r'\d+', '', broker_name).strip()
    else:
        broker_name = "Unknown"
    
    # 날짜 형식 변환
    date_str = raw_data.get('articleConfirmYmd', '')
    if date_str and len(date_str) == 8 and date_str.isdigit():
        registration_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"
    else:
        registration_date = date_str or "Unknown"
    
    # 가격 정보
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
    
    return [
        property_data.get('complex_name', ''),  # 단지명
        trade_type,  # 거래구분
        raw_data.get('buildingName', ''),  # 동
        raw_data.get('floorInfo', ''),  # 층수
        area,  # 면적
        price,  # 가격
        '',  # 가격변동
        1,  # 중복업소
        broker_name,  # 중개업소
        registration_date,  # 등록일자
        special_notes_str,  # 특기사항
        raw_data.get('cpName', '') or 'Unknown',  # 제공
        raw_data.get('articleNo', '')  # 매물번호
    ]


async def main():
    """메인 실행 함수"""
    print("\n" + "="*70)
    print("🚀 네이버 부동산 크롤러 시작")
    print(f"⏰ 시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")
    
    # 구글 시트 연결
    debug_log("=== 1단계: 구글 시트 연결 ===", "STEP")
    worksheet = setup_google_sheets()
    if not worksheet:
        debug_log("구글 시트 연결 실패. 프로그램 종료", "ERROR")
        return
    
    # 기존 데이터 삭제 및 헤더 추가
    debug_log("=== 2단계: 시트 초기화 ===", "STEP")
    debug_log("기존 데이터 삭제 중...", "DEBUG")
    worksheet.clear()
    debug_log("기존 데이터 삭제 완료", "SUCCESS")
    
    headers = ["단지명", "거래구분", "동", "층수", "면적", "가격", "가격변동", 
               "중복업소", "중개업소", "등록일자", "특기사항", "제공", "매물번호"]
    debug_log(f"헤더 추가 중: {headers}", "DEBUG")
    worksheet.append_row(headers)
    debug_log("헤더 추가 완료", "SUCCESS")
    
    # 크롤링 시작
    debug_log("=== 3단계: 크롤링 실행 ===", "STEP")
    results = []
    all_properties = []
    total_start_time = time.time()
    
    # 23개 단지 순회
    for idx, complex_info in enumerate(COMPLEXES, 1):
        debug_log(f"\n{'#'*70}", "STEP")
        debug_log(f"📍 진행: [{idx}/23] {complex_info['name']}", "STEP")
        debug_log(f"{'#'*70}", "STEP")
        
        complex_start_time = time.time()
        
        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            result = await crawler.run()
            
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            property_count = result['property_count']
            debug_log(f"✅ {complex_info['name']} 완료: {property_count}개 매물 ({complex_duration:.1f}초)", "SUCCESS")
            
            # 데이터 포맷팅
            if result.get('properties'):
                debug_log(f"데이터 포맷팅 중... ({len(result['properties'])}개)", "DEBUG")
                for property_data in result['properties']:
                    formatted_row = format_property_data(property_data)
                    all_properties.append(formatted_row)
                debug_log("데이터 포맷팅 완료", "SUCCESS")
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': property_count,
                'duration_seconds': complex_duration,
                'status': 'success'
            })
            
        except Exception as e:
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            debug_log(f"❌ {complex_info['name']} 실패: {str(e)} ({complex_duration:.1f}초)", "ERROR")
            debug_log(f"상세 에러:\n{traceback.format_exc()}", "DEBUG")
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': 0,
                'duration_seconds': complex_duration,
                'status': 'error',
                'error': str(e)
            })
        
        # 다음 단지 실행 전 대기
        if idx < len(COMPLEXES):
            debug_log("다음 단지까지 5초 대기...", "DEBUG")
            await asyncio.sleep(5)
    
    # 구글 시트에 데이터 기록
    debug_log("=== 4단계: 구글 시트 기록 ===", "STEP")
    if all_properties:
        debug_log(f"총 {len(all_properties)}개 매물 기록 중...", "INFO")
        worksheet.append_rows(all_properties)
        debug_log("구글 시트 기록 완료", "SUCCESS")
    else:
        debug_log("기록할 매물 데이터가 없습니다", "WARNING")
    
    # 전체 결과 요약
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    total_properties = sum(r['property_count'] for r in results)
    
    print("\n" + "="*70)
    print("📊 전체 결과 요약")
    print("="*70)
    print(f"⏰ 종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️  전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"✅ 성공한 단지: {len(successful)}개")
    print(f"❌ 실패한 단지: {len(failed)}개")
    print(f"🏠 총 매물 수: {total_properties}개")
    print("="*70)
    
    # 단지별 상세 결과
    print("\n📋 단지별 상세 결과:")
    print("-"*70)
    for i, result in enumerate(results, 1):
        status_icon = "✅" if result['status'] == 'success' else "❌"
        print(f"{i:2d}. {status_icon} {result['complex_name']:20s} | {result['property_count']:4d}개 | {result['duration_seconds']:5.1f}초")
    print("-"*70)
    
    # 결과를 JSON 파일로 저장
    debug_log("=== 5단계: 결과 파일 저장 ===", "STEP")
    result_data = {
        'total_duration_seconds': total_duration,
        'total_duration_minutes': total_duration/60,
        'start_time': datetime.fromtimestamp(total_start_time).strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': datetime.fromtimestamp(total_end_time).strftime('%Y-%m-%d %H:%M:%S'),
        'successful_count': len(successful),
        'failed_count': len(failed),
        'total_properties': total_properties,
        'results': results
    }
    
    with open('crawling_results.json', 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    
    debug_log("결과 파일 저장 완료: crawling_results.json", "SUCCESS")
    
    print("\n" + "="*70)
    print("🎉 크롤링 완료!")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
