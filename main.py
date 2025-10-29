import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import json
from datetime import datetime
import re
import os
import time
import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import sys

# ======================================================================
# 1. 상수 및 초기 설정
# ======================================================================

# Playwright 기본 타임아웃 설정 (15초)
DEFAULT_TIMEOUT_MS = 15000 

# 네이버 부동산 단지 목록 (총 23개) - 이 부분은 기존 파일의 목록을 유지합니다.
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
    """구글 드라이브에 파일 업로드 (크롤링 결과 파일 업로드용)"""
    # GitHub Actions에서는 사용하지 않음 (로깅 목적)
    try:
        credentials = service_account.Credentials.from_service_account_file(
            'service_account.json',
            scopes=['https://www.googleapis.com/auth/drive']
        )
        
        service = build('drive', 'v3', credentials=credentials)
        
        file_metadata = {
            'name': file_name
        }
        if folder_id:
            file_metadata['parents'] = [folder_id]
        
        media = MediaFileUpload(file_path, mimetype='text/plain')
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        print(f"드라이브 업로드 성공: {file.get('id')}")
        return file.get('id')
    except Exception as e:
        print(f"드라이브 업로드 실패: {e}")
        return None

def setup_google_sheets():
    """구글 시트 설정 및 워크시트 객체 반환"""
    try:
        # 서비스 계정 파일 경로를 GitHub Actions에서 생성한 'service_account.json'으로 지정
        credentials_file = 'service_account.json'
        if not os.path.exists(credentials_file):
            print(f"서비스 계정 파일이 없습니다: {credentials_file}")
            return None
        
        # 서비스 계정 인증
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        
        # gspread 클라이언트 생성
        gc = gspread.authorize(credentials)
        
        # 스프레드시트 ID는 환경 변수에서 가져옵니다.
        spreadsheet_id = os.environ.get('SPREADSHEET_ID')
        if not spreadsheet_id:
            print("환경 변수 SPREADSHEET_ID가 설정되지 않았습니다.")
            return None
            
        # 스프레드시트 열기
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        # "네이버 매물분석" 탭 선택
        worksheet_name = "네이버 매물분석"
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
            print(f"구글 시트 '{worksheet_name}' 연결 성공")
            return worksheet
        except gspread.WorksheetNotFound:
            # 탭이 없으면 생성
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=20)
            print(f"구글 시트 탭 '{worksheet_name}' 생성 완료")
            return worksheet
    except Exception as e:
        print(f"구글 시트 설정 실패: {e}")
        return None

# def install_playwright_browsers():
#     """Playwright 브라우저 설치 (GitHub Actions에서는 워크플로우에서 처리)"""
#     # 이 함수는 GitHub Actions 워크플로우에서 직접 처리하므로 주석 처리합니다.
#     return True


# ======================================================================
# 3. 크롤링 핵심 로직 클래스
# ======================================================================

class AggressiveCardScroll:
    # ... (AggressiveCardScroll 클래스 내용은 기존 main.py와 동일) ...
    
    # 크롤러 클래스 정의 (기존 main.py의 내용을 그대로 유지)
    
    # URL 형식: https://new.land.naver.com/complexes/728?ms=37.525526,127.025345,16&a=A1&b=A1&e=RETAIL&g=30&p=1&rT=A1:B1:B2&wts=A&cp=Y
    BASE_URL = "https://new.land.naver.com/complexes/{complex_id}?a=A1&b=A1:B1:B2&e=RETAIL&g=30&p=1&rT=A1:B1:B2&wts=A&cp=Y"
    
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.total_count = 0
        self.properties = []

    async def run(self):
        print(f"--- {self.complex_name} 크롤링 시작 ---")
        start_time = time.time()
        
        try:
            async with async_playwright() as p:
                # 런치 옵션: GitHub Actions 환경에 맞게 headless 설정
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                url = self.BASE_URL.format(complex_id=self.complex_id)
                
                # 1. 페이지 접속 및 대기
                await page.goto(url, timeout=DEFAULT_TIMEOUT_MS)
                await page.wait_for_selector('div.item_area', timeout=DEFAULT_TIMEOUT_MS)
                
                # 매물 전체 개수 파싱
                total_text = await page.locator('div.view_info > p.summary > em').first.inner_text()
                total_match = re.search(r'[\d,]+', total_text.replace(',', ''))
                self.total_count = int(total_match.group(0)) if total_match else 0
                
                if self.total_count == 0:
                    print(f"경고: {self.complex_name} 매물 0개 확인.")
                    await browser.close()
                    return {'property_count': 0, 'properties': []}
                
                print(f"총 {self.total_count}개 매물 확인. 크롤링 시작...")

                # 2. 크롤링 루프 (무한 스크롤)
                properties_set = set() # 매물 번호(articleNo) 중복 제거용
                
                # 마지막 스크롤 높이를 추적
                last_scroll_height = 0
                
                while True:
                    # 현재 매물 목록 파싱
                    item_elements = await page.locator('div.item_area > div.item').all()
                    
                    newly_parsed_count = 0
                    
                    for item_element in item_elements:
                        try:
                            data_json = await item_element.get_attribute('data-json')
                            if data_json:
                                data = json.loads(data_json)
                                article_no = data.get('articleNo')
                                
                                if article_no and article_no not in properties_set:
                                    # 매물 데이터 정리 및 추가
                                    self.properties.append({
                                        'article_no': article_no,
                                        'raw_data': data
                                    })
                                    properties_set.add(article_no)
                                    newly_parsed_count += 1
                        except Exception as e:
                            # print(f"경고: 매물 데이터 파싱 오류 - {e}")
                            continue

                    # 3. 스크롤 다운
                    # 현재 스크롤 위치를 최대한 아래로 이동
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(500) # 스크롤 후 내용 로딩 대기

                    # 새로운 스크롤 높이 확인
                    new_scroll_height = await page.evaluate("document.body.scrollHeight")
                    
                    # 더 이상 스크롤이 내려가지 않고, 새로 파싱된 매물이 없다면 종료
                    if new_scroll_height == last_scroll_height and newly_parsed_count == 0:
                        print(f"-> 스크롤 종료 지점 도달. 최종 매물 수: {len(self.properties)}")
                        break
                        
                    # 매물 수가 목표치에 도달하면 종료
                    if len(self.properties) >= self.total_count and self.total_count > 0:
                        print(f"-> 목표 매물 수({self.total_count}개) 도달. 최종 매물 수: {len(self.properties)}")
                        break

                    # 마지막 높이 업데이트
                    last_scroll_height = new_scroll_height
                    
                    # 4. 페이징 버튼 클릭 시도 (Playwright에서는 스크롤만으로 충분할 수 있지만, 예비적으로)
                    try:
                        next_button = page.locator('button.btn_next:not([disabled])')
                        if await next_button.is_visible():
                            await next_button.click(timeout=100) # 짧은 타임아웃
                        else:
                            # 버튼이 없거나 비활성화되면 스크롤 종료로 간주
                            # print("-> 다음 버튼 없음/비활성화.")
                            pass
                    except PlaywrightTimeoutError:
                        pass
                    except Exception as e:
                        # print(f"경고: 다음 버튼 클릭 오류 - {e}")
                        pass
                        
                    # 무한 루프 탈출 조건: 매물 개수와 스크롤 높이 변화가 없을 때

        except PlaywrightTimeoutError:
            print(f"오류: {self.complex_name} 크롤링 타임아웃 발생 (Timeout: {DEFAULT_TIMEOUT_MS/1000}s)")
        except Exception as e:
            print(f"치명적인 오류: {self.complex_name} 크롤링 실패 - {e}")
            
        finally:
            # 브라우저가 열려 있다면 닫습니다.
            if 'browser' in locals() and browser:
                await browser.close()
                
        end_time = time.time()
        duration = end_time - start_time
        
        return {
            'property_count': len(self.properties),
            'properties': self.properties,
            'duration_seconds': duration,
            'complex_name': self.complex_name
        }


# ======================================================================
# 4. GitHub Actions 실행 진입점 (핵심 수정 부분)
# ======================================================================

def execute_crawling_and_record():
    """23개 단지를 순차적으로 크롤링하고 구글 시트에 기록"""
    
    # print("GitHub Actions 환경이므로 Playwright 설치 단계는 워크플로우에서 처리합니다.")

    print(f"\n=== 23개 단지 자동 크롤링 시작 ===")
    print(f"시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    total_start_time = time.time()
    
    # 1. 구글 시트 설정 및 초기화 (한 번만 실행)
    worksheet = setup_google_sheets()
    
    if worksheet:
        try:
            # 🚨 수정된 로직: 헤더(1행)를 제외하고 2행부터 마지막 행까지 삭제
            print("=== 기존 구글 시트 데이터 삭제 (헤더 유지) 시작 ===")
            
            # 현재 시트의 모든 행 개수를 가져옵니다.
            # gspread의 row_count는 시트의 최대 크기를 반환할 수 있으므로, 
            # 실제 데이터가 있는 마지막 행 번호를 찾아야 하지만, 
            # delete_rows는 행 개수를 기준으로 동작합니다.
            
            # 모든 데이터를 읽어와 행 개수를 정확히 파악하는 것이 안전하지만, 
            # 여기서는 성능을 위해 간단히 row_count를 사용합니다.
            num_rows = worksheet.row_count
            
            # 실제 데이터가 있는 마지막 행을 확인하는 더 안전한 방법:
            try:
                # 마지막 행까지의 모든 값을 가져옵니다.
                all_values = worksheet.get_all_values()
                actual_data_rows = len(all_values)
            except Exception:
                actual_data_rows = num_rows # 실패 시 기본값 사용
            
            # 2행부터 실제 데이터의 마지막 행까지 삭제 (헤더는 1행에 유지)
            if actual_data_rows > 1:
                # delete_rows는 첫 번째 인덱스가 시작 행, 두 번째 인덱스가 끝 행입니다.
                worksheet.delete_rows(2, actual_data_rows)
                print(f"=== 2행부터 {actual_data_rows}행까지 기존 데이터 삭제 완료. ===")
            else:
                print("=== 헤더(1행) 외에 기존 데이터가 없거나 시트가 비어있습니다. ===")
                
            
            # 헤더가 누락되었거나 시트가 완전히 비었을 경우에만 헤더를 추가합니다.
            if actual_data_rows == 0 or not worksheet.row_values(1):
                headers = ["단지명", "거래구분", "동", "층수", "면적", "가격", "가격변동", "중복업소", "중개업소", "등록일자", "특기사항", "제공", "매물번호"]
                worksheet.append_row(headers)
                print("=== 구글 시트 헤더 추가 완료 ===")
            
        except Exception as e:
            print(f"경고: 구글 시트 초기화/삭제 실패: {e}")
            worksheet = None # 실패 시 기록 중단
    else:
        print("경고: 구글 시트 연결 실패. 데이터 기록을 건너뜁니다.")

    # 2. 실제 크롤링 실행 및 데이터 수집
    all_rows_to_append = [] # 일괄 기록을 위해 리스트에 모음

    for i, complex_info in enumerate(COMPLEXES):
        print(f"\n=== 단지 {i+1}/{len(COMPLEXES)}: {complex_info['name']} 크롤링 시작 ===")
        complex_start_time = time.time()
        
        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            result = asyncio.run(crawler.run())
            
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            property_list = result.get('properties', [])
            
            print(f"=== {complex_info['name']} 크롤링 완료: {len(property_list)}개 매물 ({complex_duration:.1f}초) ===")
            
            # 매물 데이터 정리 및 일괄 기록 리스트에 추가
            property_rows = []
            for property_data in property_list:
                raw_data = property_data.get('raw_data', {})
                
                # 면적 정보 포맷팅
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
                
                # 특기사항 포맷팅
                special_notes = []
                direction = raw_data.get('direction', '')
                if direction:
                    special_notes.append(f"방향: {direction}")
                
                feature_desc = raw_data.get('articleFeatureDesc', '')
                if feature_desc:
                    # '제공' 이전 텍스트만 추출
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
                    # 불필요한 문구 제거
                    remove_strings = ['공인중개사사무소', '(주)', '중개법인', '주식회사', '부동산중개', '부동산중개법인주식회사', '부동산중개법인', '공인중개사', '부동산']
                    for remove_str in remove_strings:
                        broker_name = broker_name.replace(remove_str, '')
                    # 숫자 제거
                    broker_name = re.sub(r'\d+', '', broker_name).strip()
                else:
                    broker_name = "Unknown"
                
                # 날짜 형식 변환 (YYYYMMDD -> YYYY.MM.DD)
                date_str = raw_data.get('articleConfirmYmd', '')
                if date_str and len(date_str) == 8 and date_str.isdigit():
                    registration_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"
                else:
                    registration_date = date_str or "Unknown"
                
                # 월세 가격 표기 수정
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
                    complex_info['name'],  # 단지명
                    trade_type,  # 거래구분
                    raw_data.get('buildingName', ''),  # 동
                    raw_data.get('floorInfo', ''),  # 층수
                    area,  # 면적
                    price,  # 가격
                    '',  # 가격변동 (비워둠)
                    1,  # 중복업소 (기본값)
                    broker_name,  # 중개업소
                    registration_date,  # 등록일자
                    special_notes_str,  # 특기사항
                    raw_data.get('cpName', '') or 'Unknown',  # 제공
                    raw_data.get('articleNo', '')  # 매물번호
                ]
                
                property_rows.append(property_row)
            
            all_rows_to_append.extend(property_rows) # 일괄 기록을 위해 리스트에 추가
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': len(property_list),
                'duration_seconds': complex_duration,
                'status': 'success'
            })
            
        except Exception as e:
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            print(f"=== {complex_info['name']} 크롤링 실패: {e} ({complex_duration:.1f}초) ===")
            
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
            # append_rows를 사용하여 한 번에 시트에 기록
            worksheet.append_rows(all_rows_to_append)
            print(f"\n=== 최종 구글 시트 기록 완료: {len(all_rows_to_append)}개 매물 ===")
        except Exception as e:
            print(f"\n=== 최종 구글 시트 일괄 기록 실패: {e} ===")
    
    # 4. 결과 요약 출력
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    total_properties = sum(r['property_count'] for r in results)
    
    print(f"\n=== 23개 단지 크롤링 최종 요약 ===")
    print(f"종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"성공한 단지: {len(successful)}개")
    print(f"실패한 단지: {len(failed)}개")
    print(f"총 매물 수: {total_properties}개")


# ======================================================================
# 5. 스크립트 실행 진입점 (GitHub Actions용)
# ======================================================================

if __name__ == "__main__":
    execute_crawling_and_record()
