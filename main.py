#!/usr/bin/env python3
"""
GitHub Actions용 네이버 부동산 크롤러
매일 자동으로 23개 단지의 매물 정보를 수집하여 Google Sheets에 기록합니다.
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

def setup_google_sheets():
    """구글 시트 설정"""
    try:
        credentials_file = 'service_account.json'
        if not os.path.exists(credentials_file):
            print(f"❌ 서비스 계정 파일이 없습니다: {credentials_file}")
            return None
        
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        
        gc = gspread.authorize(credentials)
        
        # ▼ 변경: 기본값만 새 스프레드시트 ID로 교체
        spreadsheet_id = os.environ.get(
            'SPREADSHEET_ID',
            '1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4'
        )
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        try:
            worksheet = spreadsheet.worksheet("네이버 매물분석")
            print("✅ 구글 시트 연결 성공")
            return worksheet
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="네이버 매물분석", rows=1000, cols=20)
            print("✅ 구글 시트 탭 생성 완료")
            return worksheet
    except Exception as e:
        print(f"❌ 구글 시트 설정 실패: {e}")
        return None


class AggressiveCardScroll:
    """네이버 부동산 매물 크롤러"""
    
    def __init__(self, complex_id, complex_name):
        self.complex_id = complex_id
        self.complex_name = complex_name
        self.property_cards = []
        self.api_responses = []
        self.seen_article_nos = set()
    
    async def run(self):
        """크롤링 실행"""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            
            page = await context.new_page()
            
            # API 응답 캡처
            async def handle_response(response):
                if 'complexPyeongDetailList' in response.url or 'complexArticleList' in response.url:
                    try:
                        json_data = await response.json()
                        self.api_responses.append(json_data)
                        
                        if 'result' in json_data and json_data['result']:
                            list_data = json_data['result'].get('list', [])
                            for item in list_data:
                                article_no = str(item.get('articleNo', ''))
                                if article_no and article_no not in self.seen_article_nos:
                                    self.seen_article_nos.add(article_no)
                                    self.property_cards.append({
                                        'raw_data': item,
                                        'complex_name': self.complex_name,
                                        'extracted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    })
                    except Exception as e:
                        print(f"⚠️  API 응답 파싱 오류: {e}")
            
            page.on('response', handle_response)
            
            # 페이지 이동
            url = f"https://new.land.naver.com/complexes/{self.complex_id}?ms=37.4779802,127.0413966,16&a=APT&b=A1&e=RETAIL"
            await page.goto(url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(3)
            
            # 매물 탭 클릭
            try:
                trade_button = page.locator('a.complex_link span:has-text("매물")')
                await trade_button.click(timeout=10000)
                await asyncio.sleep(2)
                print(f"  ✓ 매물 탭 클릭 완료")
            except Exception as e:
                print(f"  ⚠️  매물 탭 클릭 실패: {e}")
            
            # 스크롤 및 데이터 수집
            max_scrolls = 100
            no_new_data_count = 0
            
            for scroll_num in range(max_scrolls):
                previous_count = len(self.property_cards)
                
                # 스크롤
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(1.5)
                
                # 새 데이터 확인
                current_count = len(self.property_cards)
                if current_count > previous_count:
                    no_new_data_count = 0
                    print(f"  📊 스크롤 {scroll_num + 1}: {current_count}개 매물")
                else:
                    no_new_data_count += 1
                
                # 연속 3번 새 데이터 없으면 종료
                if no_new_data_count >= 3:
                    print(f"  ✓ 스크롤 완료 (연속 {no_new_data_count}회 변화 없음)")
                    break
            
            await browser.close()
        
        return {
            'complex_name': self.complex_name,
            'property_count': len(self.property_cards),
            'properties': self.property_cards
        }


def format_property_data(property_data):
    """매물 데이터 포맷팅"""
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
        '',  # 가격변동 (비워둠)
        1,  # 중복업소 (기본값)
        broker_name,  # 중개업소
        registration_date,  # 등록일자
        special_notes_str,  # 특기사항
        raw_data.get('cpName', '') or 'Unknown',  # 제공
        raw_data.get('articleNo', '')  # 매물번호
    ]


async def main():
    """메인 실행 함수"""
    print("=" * 60)
    print("🏢 네이버 부동산 크롤러 시작")
    print(f"⏰ 시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 구글 시트 연결
    worksheet = setup_google_sheets()
    if not worksheet:
        print("❌ 구글 시트 연결 실패. 종료합니다.")
        return
    
    # 기존 데이터 삭제 및 헤더 추가
    worksheet.clear()
    headers = ["단지명", "거래구분", "동", "층수", "면적", "가격", "가격변동", 
               "중복업소", "중개업소", "등록일자", "특기사항", "제공", "매물번호"]
    worksheet.append_row(headers)
    print("✅ 구글 시트 초기화 완료")
    
    # 크롤링 결과 저장
    results = []
    all_properties = []
    total_start_time = time.time()
    
    # 23개 단지 순회
    for idx, complex_info in enumerate(COMPLEXES, 1):
        print(f"\n{'='*60}")
        print(f"📍 [{idx}/23] {complex_info['name']} 크롤링 시작")
        print(f"{'='*60}")
        
        complex_start_time = time.time()
        
        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            result = await crawler.run()
            
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            property_count = result['property_count']
            print(f"✅ {complex_info['name']} 완료: {property_count}개 매물 ({complex_duration:.1f}초)")
            
            # 데이터 포맷팅
            if result.get('properties'):
                for property_data in result['properties']:
                    formatted_row = format_property_data(property_data)
                    all_properties.append(formatted_row)
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': property_count,
                'duration_seconds': complex_duration,
                'status': 'success'
            })
            
        except Exception as e:
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            print(f"❌ {complex_info['name']} 실패: {e} ({complex_duration:.1f}초)")
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': 0,
                'duration_seconds': complex_duration,
                'status': 'error',
                'error': str(e)
            })
        
        # 다음 단지 실행 전 대기
        if idx < len(COMPLEXES):
            print("⏳ 5초 대기...")
            await asyncio.sleep(5)
    
    # 구글 시트에 데이터 기록
    if all_properties:
        print(f"\n📝 구글 시트에 {len(all_properties)}개 매물 기록 중...")
        worksheet.append_rows(all_properties)
        print("✅ 구글 시트 기록 완료")
    else:
        print("⚠️  기록할 매물 데이터가 없습니다")
    
    # 전체 결과 요약
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    total_properties = sum(r['property_count'] for r in results)
    
    print(f"\n{'='*60}")
    print("📊 전체 결과 요약")
    print(f"{'='*60}")
    print(f"⏰ 종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️  전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"✅ 성공한 단지: {len(successful)}개")
    print(f"❌ 실패한 단지: {len(failed)}개")
    print(f"🏠 총 매물 수: {total_properties}개")
    
    # 단지별 상세 결과
    print(f"\n📋 단지별 상세 결과:")
    for i, result in enumerate(results, 1):
        status_icon = "✅" if result['status'] == 'success' else "❌"
        print(f"{i:2d}. {status_icon} {result['complex_name']:20s} | {result['property_count']:4d}개 | {result['duration_seconds']:5.1f}초")
    
    # 결과를 JSON 파일로 저장
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
    
    print(f"\n💾 결과가 'crawling_results.json' 파일에 저장되었습니다.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
