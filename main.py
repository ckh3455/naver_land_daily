import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import json
import re
import os
import time
import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import sys
from datetime import datetime

# ======================================================================
# 1. 상수 및 초기 설정
# ======================================================================

# Playwright 기본 타임아웃 설정 (90초 유지)
DEFAULT_TIMEOUT_MS = 90000 

# 네이버 부동산 단지 목록 (총 23개)
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
    {"id": "494", "name": "현대9차"},
    {"id": "722", "name": "한양1차"},
    {"id": "718", "name": "한양2차"},
    {"id": "706", "name": "한양3차"},
    {"id": "711", "name": "한양4차"},
    {"id": "713", "name": "한양5차"},
    {"id": "716", "name": "한양6차"},
    {"id": "717", "name": "한양7차"},
    {"id": "723", "name": "한양8차"},
    {"id": "730", "name": "한양12차"},
]


# ======================================================================
# 2. 구글 서비스 및 유틸리티 함수
# ======================================================================

def upload_to_google_drive(file_path, file_name, folder_id=None):
    """구글 드라이브에 파일 업로드 (GitHub Actions에서는 로깅 목적으로만 유지)"""
    try:
        credentials = service_account.Credentials.from_service_account_file(
            'service_account.json',
            scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=credentials)
        file_metadata = {'name': file_name}
        if folder_id:
            file_metadata['parents'] = [folder_id]
        
        media = MediaFileUpload(file_path, mimetype='text/plain')
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        print(f"드라이브 업로드 성공: {file.get('id')}", flush=True)
        return file.get('id')
    except Exception as e:
        return None

def setup_google_sheets():
    """구글 시트 설정 및 워크시트 객체 반환"""
    try:
        credentials_file = 'service_account.json'
        if not os.path.exists(credentials_file):
            print(f"서비스 계정 파일이 없습니다: {credentials_file}", flush=True)
            return None
        
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        
        gc = gspread.authorize(credentials)
        spreadsheet_id = os.environ.get('SPREADSHEET_ID')
        if not spreadsheet_id:
            print("환경 변수 SPREADSHEET_ID가 설정되지 않았습니다.", flush=True)
            return None
            
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        worksheet_name = "네이버 매물분석"
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
            print(f"구글 시트 '{worksheet_name}' 연결 성공", flush=True)
            return worksheet
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=20)
            print(f"구글 시트 탭 '{worksheet_name}' 생성 완료", flush=True)
            return worksheet
    except Exception as e:
        print(f"구글 시트 설정 실패: {e}", flush=True)
        return None


# ======================================================================
# 3. 크롤링 핵심 로직 클래스
# ======================================================================

class AggressiveCardScroll:
    BASE_URL = "https://new.land.naver.com/complexes/{complex_id}?a=A1&b=A1:B1:B2&e=RETAIL&g=30&p=1&rT=A1:B1:B2&wts=A&cp=Y"
    
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.total_count = 0
        self.properties = []
        self.error_screenshot_path = None # 에러 발생 시 스크린샷 경로 저장용

    async def run(self):
        print(f"--- {self.complex_name} 크롤링 시작 ---", flush=True)
        start_time = time.time()
        
        browser = None
        try:
            async with async_playwright() as p:
                # 🚨 로그 강제 출력 (flush=True) 적용
                print("DEBUG: 1. Playwright 시작 및 Chromium 브라우저 실행 시도...", flush=True)
                # 이 부분이 90초를 초과하면 TimeoutError가 발생해야 합니다.
                browser = await p.chromium.launch(headless=True, timeout=DEFAULT_TIMEOUT_MS) 
                print("DEBUG: 2. Chromium 브라우저 실행 성공.", flush=True)
                
                page = await browser.new_page()
                print("DEBUG: 3. 새 페이지 생성 완료.", flush=True)
                
                url = self.BASE_URL.format(complex_id=self.complex_id)
                
                # 1. 페이지 접속 및 대기 (타임아웃 90초 적용)
                print(f"DEBUG: 4. URL 접속 시도: {url} (Timeout: {DEFAULT_TIMEOUT_MS/1000}s)", flush=True)
                await page.goto(url, timeout=DEFAULT_TIMEOUT_MS)
                print("DEBUG: 5. 페이지 접속 성공 (Network Idle).", flush=True)
                
                # 🚨 [핵심 수정]: 90초 동안 핵심 요소를 기다리는 코드 제거 및 5초 강제 대기
                print("DEBUG: 6. 핵심 요소 'div.item_area' 대기 로직 제거. JavaScript 로드를 위해 5초 강제 대기 후 스크롤 진입.", flush=True)
                await page.wait_for_timeout(5000) # 5초 강제 대기
                print("DEBUG: 7. 5초 대기 완료. 스크롤 시작 준비.", flush=True)
                
                # 매물 전체 개수 파싱 (요소가 나타나지 않을 수 있으므로, 예외 처리를 염두에 둡니다)
                total_text_locator = page.locator('div.view_info > p.summary > em').first
                try:
                    total_text = await total_text_locator.inner_text(timeout=5000) # 5초 대기로 변경
                    total_match = re.search(r'[\d,]+', total_text.replace(',', ''))
                    self.total_count = int(total_match.group(0)) if total_match else 0
                except PlaywrightTimeoutError:
                    self.total_count = 0
                    print("경고: 매물 전체 개수 요소 파싱 실패(5초 타임아웃). 0개로 설정 후 스크롤 시도.", flush=True)
                except Exception:
                     self.total_count = 0
                
                if self.total_count == 0:
                    # 매물 0개로 설정되었더라도, 적극적 스크롤을 시도하여 숨겨진 매물이 있는지 확인합니다.
                    print(f"총 매물 수 0개로 시작. 강제 스크롤 루프 진입.", flush=True)
                else:
                    print(f"총 {self.total_count}개 매물 확인. 크롤링 시작...", flush=True)

                # 2. 크롤링 루프 (무한 스크롤)
                properties_set = set() # 매물 번호(articleNo) 중복 제거용
                last_scroll_height = 0
                
                while True:
                    # 현재 매물 목록 파싱
                    item_elements = await page.locator('div.item_area > div.item').all()
                    
                    newly_parsed_count = 0
                    previous_count = len(properties_set)
                    
                    for item_element in item_elements:
                        try:
                            data_json = await item_element.get_attribute('data-json')
                            if data_json:
                                data = json.loads(data_json)
                                article_no = data.get('articleNo')
                                
                                if article_no and article_no not in properties_set:
                                    self.properties.append({
                                        'article_no': article_no,
                                        'raw_data': data
                                    })
                                    properties_set.add(article_no)
                                    newly_parsed_count += 1
                        except Exception as e:
                            continue

                    # 🚨 [실시간 로그]: 100개 단위로 진행 상황 로그 출력
                    current_count = len(properties_set)
                    if newly_parsed_count > 0 and (current_count // 100) != (previous_count // 100):
                        print(f"-> 진행 중: 현재까지 {self.complex_name}에서 {current_count}개 매물 수집 완료.", flush=True)
                        
                    # 3. 스크롤 다운
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(500) # 스크롤 후 내용 로딩 대기

                    # 새로운 스크롤 높이 확인
                    new_scroll_height = await page.evaluate("document.body.scrollHeight")
                    
                    # 더 이상 스크롤이 내려가지 않고, 새로 파싱된 매물이 없다면 종료
                    if new_scroll_height == last_scroll_height and newly_parsed_count == 0:
                        print(f"-> 스크롤 종료 지점 도달. 최종 매물 수: {len(self.properties)}", flush=True)
                        break
                        
                    # 매물 수가 목표치에 도달하면 종료
                    if len(self.properties) >= self.total_count and self.total_count > 0:
                        print(f"-> 목표 매물 수({self.total_count}개) 도달. 최종 매물 수: {len(self.properties)}", flush=True)
                        break

                    # 마지막 높이 업데이트
                    last_scroll_height = new_scroll_height
                    
        except PlaywrightTimeoutError:
            print(f"오류: {self.complex_name} 크롤링 타임아웃 발생 (Timeout: {DEFAULT_TIMEOUT_MS/1000}s)", flush=True)
            
            # 🚨 타임아웃 발생 시 스크린샷 저장 로직
            try:
                screenshot_filename = f"{self.complex_name}_TIMEOUT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                # 페이지가 멈춘 상태여도 스크린샷은 시도합니다.
                if page:
                    await page.screenshot(path=screenshot_filename, full_page=True)
                    print(f"***오류 스크린샷 저장 완료: {screenshot_filename} (GitHub Actions 로그에서 확인)***", flush=True)
                    self.error_screenshot_path = screenshot_filename
            except Exception as e_shot:
                print(f"경고: 스크린샷 캡처 실패 - {e_shot}", flush=True)
                
        except Exception as e:
            print(f"치명적인 오류: {self.complex_name} 크롤링 실패 - {e}", flush=True)
            
        finally:
            if browser:
                await browser.close()
                
        end_time = time.time()
        duration = end_time - start_time
        
        return {
            'property_count': len(self.properties),
            'properties': self.properties,
            'duration_seconds': duration,
            'complex_name': self.complex_name,
            'error_screenshot_path': self.error_screenshot_path # 결과에 경로 포함
        }


# ======================================================================
# 4. GitHub Actions 실행 진입점
# ======================================================================

def execute_crawling_and_record():
    """23개 단지를 순차적으로 크롤링하고 구글 시트에 기록"""
    
    print(f"\n=== 23개 단지 자동 크롤링 시작 ===", flush=True)
    print(f"시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    
    results = []
    total_start_time = time.time()
    
    # 1. 구글 시트 설정 및 초기화
    worksheet = setup_google_sheets()
    
    if worksheet:
        try:
            print("=== 기존 구글 시트 데이터 삭제 (헤더 유지) 시작 ===", flush=True)
            
            actual_data_rows = 1
            try:
                all_values = worksheet.get_all_values()
                actual_data_rows = len(all_values)
            except Exception as e:
                 error_msg = str(e)
                 if 'PERMISSION_DENIED' in error_msg:
                     print(f"***경고: 구글 시트 초기화 중 권한 오류 발생. 크롤링은 진행됩니다. (오류: {error_msg})***", flush=True)
                     worksheet = None 
                 else:
                     print(f"경고: 시트 정보 조회 중 오류 발생: {e}", flush=True)
                 actual_data_rows = 1 
            
            if worksheet:
                if actual_data_rows > 1:
                    worksheet.delete_rows(2, actual_data_rows)
                    print(f"=== 2행부터 {actual_data_rows}행까지 기존 데이터 삭제 완료. ===", flush=True)
                else:
                    print("=== 헤더(1행) 외에 기존 데이터가 없거나 시트가 비어있습니다. ===", flush=True)
                    
                if actual_data_rows <= 1 or not worksheet.row_values(1) or worksheet.row_values(1) == ['']:
                    headers = ["단지명", "거래구분", "동", "층수", "면적", "가격", "가격변동", "중복업소", "중개업소", "등록일자", "특기사항", "제공", "매물번호"]
                    worksheet.append_row(headers)
                    print("=== 구글 시트 헤더 추가 완료 ===", flush=True)
            
        except Exception as e:
            print(f"경고: 구글 시트 초기화/삭제 실패: ***{e}***", flush=True)
            worksheet = None 
    else:
        print("경고: 구글 시트 연결 실패. 데이터 기록을 건너뜁니다.", flush=True)

    # 2. 실제 크롤링 실행 및 데이터 수집
    all_rows_to_append = []

    for i, complex_info in enumerate(COMPLEXES):
        print(f"\n=== 단지 {i+1}/{len(COMPLEXES)}: {complex_info['name']} 크롤링 시작 ===", flush=True)
        complex_start_time = time.time()
        
        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            result = asyncio.run(crawler.run())
            
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            property_list = result.get('properties', [])
            screenshot_path = result.get('error_screenshot_path')
            
            print(f"=== {complex_info['name']} 크롤링 완료: {len(property_list)}개 매물 ({complex_duration:.1f}초) ===", flush=True)
            if screenshot_path:
                 print(f"***타임아웃 발생 지점 스크린샷: {screenshot_path}***", flush=True)

            # 매물 데이터 정리 및 일괄 기록 리스트에 추가 (로직 생략)
            property_rows = []
            for property_data in property_list:
                raw_data = property_data.get('raw_data', {})
                
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
                
                broker_name = raw_data.get('realtorName', '')
                if broker_name and broker_name != "Unknown":
                    remove_strings = ['공인중개사사무소', '(주)', '중개법인', '주식회사', '부동산중개', '부동산중개법인주식회사', '부동산중개법인', '공인중개사', '부동산']
                    for remove_str in remove_strings:
                        broker_name = broker_name.replace(remove_str, '')
                    broker_name = re.sub(r'\d+', '', broker_name).strip()
                else:
                    broker_name = "Unknown"
                
                date_str = raw_data.get('articleConfirmYmd', '')
                if date_str and len(date_str) == 8 and date_str.isdigit():
                    registration_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"
                else:
                    registration_date = date_str or "Unknown"
                
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
                
                # 최종 행 데이터
                property_row = [
                    complex_info['name'],
                    trade_type,
                    raw_data.get('buildingName', ''),
                    raw_data.get('floorInfo', ''),
                    area,
                    price,
                    '',
                    1,
                    broker_name,
                    registration_date,
                    special_notes_str,
                    raw_data.get('cpName', '') or 'Unknown',
                    raw_data.get('articleNo', '')
                ]
                
                property_rows.append(property_row)
            
            all_rows_to_append.extend(property_rows)
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': len(property_list),
                'duration_seconds': complex_duration,
                'status': 'success' if not screenshot_path else 'timeout_error',
                'screenshot_path': screenshot_path
            })
            
        except Exception as e:
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            print(f"=== {complex_info['name']} 크롤링 실패: {e} ({complex_duration:.1f}초) ===", flush=True)
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': 0,
                'duration_seconds': complex_duration,
                'status': 'error',
                'error': str(e)
            })
            
    # 3. 크롤링 완료 후 구글 시트에 일괄 기록
    if worksheet and all_rows_to_append:
        try:
            worksheet.append_rows(all_rows_to_append)
            print(f"\n=== 최종 구글 시트 기록 완료: {len(all_rows_to_append)}개 매물 ===", flush=True)
        except Exception as e:
            print(f"\n=== 최종 구글 시트 일괄 기록 실패: {e} ===", flush=True)
    
    # 4. 결과 요약 출력
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] != 'success']
    total_properties = sum(r['property_count'] for r in results)
    
    print(f"\n=== 23개 단지 크롤링 최종 요약 ===", flush=True)
    print(f"종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    print(f"전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)", flush=True)
    print(f"성공한 단지: {len(successful)}개", flush=True)
    print(f"실패/타임아웃 단지: {len(failed)}개", flush=True)
    print(f"총 매물 수: {total_properties}개", flush=True)


# ======================================================================
# 5. 스크립트 실행 진입점 (GitHub Actions용)
# ======================================================================

if __name__ == "__main__":
    execute_crawling_and_record()
