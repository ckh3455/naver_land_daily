# 기존의 모든 import, upload_to_google_drive, setup_google_sheets,
# write_to_google_sheets, install_playwright_browsers, AggressiveCardScroll 클래스,
# COMPLEXES 리스트는 그대로 유지됩니다.

# ----------------------------------------------------------------------
# [추가/수정] 23개 단지 크롤링 및 구글 시트 기록 실행 함수
# ----------------------------------------------------------------------

def execute_crawling_and_record():
    """23개 단지를 순차적으로 크롤링하고 구글 시트에 기록"""
    
    print("GitHub Actions 환경이므로 Playwright 설치 단계는 워크플로우에서 처리합니다.")

    print(f"\n=== 23개 단지 자동 크롤링 시작 ===")
    print(f"시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    total_start_time = time.time()
    
    # 1. 구글 시트 설정 및 초기화 (한 번만 실행)
    worksheet = setup_google_sheets() # setup_google_sheets 함수는 os.environ.get('SPREADSHEET_ID')를 사용
    
    if worksheet:
        try:
            # 시트 클리어 및 헤더 추가 (매번 최신 데이터로 업데이트)
            worksheet.clear()
            print("=== 기존 구글 시트 데이터 삭제 완료 ===")
            
            headers = ["단지명", "거래구분", "동", "층수", "면적", "가격", "가격변동", "중복업소", "중개업소", "등록일자", "특기사항", "제공", "매물번호"]
            worksheet.append_row(headers)
            print("=== 구글 시트 헤더 추가 완료 ===")
            
        except Exception as e:
            print(f"경고: 구글 시트 초기화 (삭제/헤더 추가) 실패: {e}")
            worksheet = None # 실패 시 기록 중단
    else:
        print("경고: 구글 시트 연결 실패. 데이터 기록을 건너뜁니다.")

    # 2. 실제 크롤링 실행 및 데이터 수집
    all_rows_to_append = [] # 일괄 기록을 위해 리스트에 모음

    for i, complex_info in enumerate(COMPLEXES): # COMPLEXE 리스트를 순회하며 실행
        print(f"\n=== 단지 {i+1}/{len(COMPLEXES)}: {complex_info['name']} 크롤링 시작 ===")
        complex_start_time = time.time()
        
        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            # run()은 비동기 함수이므로 asyncio.run()으로 실행
            result = asyncio.run(crawler.run())
            
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            property_list = result.get('properties', [])
            
            print(f"=== {complex_info['name']} 크롤링 완료: {len(property_list)}개 매물 ({complex_duration:.1f}초) ===")
            
            # 매물 데이터 정리 (기존 로직 재사용)
            property_rows = []
            for property_data in property_list:
                raw_data = property_data.get('raw_data', {})
                
                # --- 매물 정리 로직 (면적, 특기사항, 중개업소, 날짜, 월세 가격 포맷팅) ---
                # 이 부분은 기존 main.py의 로직을 그대로 사용합니다. (줄임표 대체)
                
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
                    remove_strings = ['공인중개사사무소', '(주)', '중개법인', '주식회사', '부동산중개', '부동산중개법인주식회사', '부동산중개법인', '공인중개사', '부동산']
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
                
                # 정리된 매물 데이터
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


# ----------------------------------------------------------------------
# [추가] GitHub Actions 실행을 위한 진입점 (기존 cloud_function_entry 대체)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    execute_crawling_and_record()
