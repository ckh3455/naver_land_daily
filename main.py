# ... (기존의 모든 함수와 클래스 정의, COMPLEX 목록은 그대로 유지)

# Cloud Function Entry Point 제거

def execute_crawling_and_record():
    """23개 단지 자동 순차 실행 및 구글 시트에 기록"""
    # Playwright 브라우저 설치 확인 및 설치
    if not os.path.exists('/root/.cache/ms-playwright/chromium-1091/chrome-linux/chrome') and not os.path.exists('/home/runner/.cache/ms-playwright/chromium-1091/chrome-linux/chrome'):
        print("Playwright 브라우저가 설치되지 않음. 설치 시작...")
        # GitHub Actions 환경에 맞게 경로를 변경하거나, 환경 변수를 이용해야 합니다.
        # GitHub Actions에서는 playwright install 명령을 워크플로우에서 직접 실행하는 것이 더 안정적입니다.
        # 여기서는 로컬 실행을 위해 기존 함수를 호출하지만, Actions에서는 스킵 가능합니다.
        # install_playwright_browsers() # 이 부분은 GitHub Actions 워크플로우에서 처리
        pass 
    
    print(f"\n=== 23개 단지 자동 크롤링 시작 ===")
    print(f"시작시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    all_properties = []
    total_start_time = time.time()
    
    # 구글 시트 설정
    # 워크시트를 미리 한 번만 설정하고 재사용합니다.
    worksheet = setup_google_sheets()
    if not worksheet:
        print("경고: 구글 시트 연결 실패. 데이터 기록을 건너뜁니다.")
        # Google Sheet 연결 실패 시에도 크롤링은 계속하도록 return하지 않음
    else:
        # 기존 데이터 모두 지우기
        # GitHub Actions 실행 시 매번 시트를 클리어할지 여부는 사용자의 정책에 따라 결정
        try:
            worksheet.clear()
            print("=== 기존 구글 시트 데이터 삭제 완료 ===")
        except Exception as e:
            print(f"경고: 구글 시트 데이터 삭제 실패: {e}")

        # 헤더 추가
        headers = ["단지명", "거래구분", "동", "층수", "면적", "가격", "가격변동", "중복업소", "중개업소", "등록일자", "특기사항", "제공", "매물번호"]
        try:
            worksheet.append_row(headers)
            print("=== 구글 시트 헤더 추가 완료 ===")
        except Exception as e:
            print(f"경고: 구글 시트 헤더 추가 실패: {e}")

    # 실제 크롤링 실행
    for i, complex_info in enumerate(COMPLEXES):
        print(f"\n=== 단지 {i+1}/{len(COMPLEXES)}: {complex_info['name']} 크롤링 시작 ===")
        complex_start_time = time.time()
        
        try:
            crawler = AggressiveCardScroll(complex_info['id'], complex_info['name'])
            result = asyncio.run(crawler.run())
            
            complex_end_time = time.time()
            complex_duration = complex_end_time - complex_start_time
            
            print(f"=== {complex_info['name']} 크롤링 완료: {result['property_count']}개 매물 ({complex_duration:.1f}초) ===")
            
            # 매물 데이터를 전체 목록에 추가 (결과 요약용)
            if 'properties' in result:
                all_properties.extend(result['properties'])
            
            # 구글 시트에 정리된 매물 데이터 기록
            if worksheet and 'properties' in result and result['properties']:
                property_rows = []
                for property_data in result['properties']:
                    raw_data = property_data.get('raw_data', {})
                    
                    # ... (기존 main.py의 property_row 정리 로직을 그대로 사용) ...
                    # 이 부분이 길어지므로, 핵심 로직은 그대로 유지하고 주석으로 대체합니다.
                    
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
                
                # 구글 시트에 일괄 추가 (성능 향상)
                if property_rows:
                    worksheet.append_rows(property_rows)
                    print(f"=== {complex_info['name']} 구글 시트 기록 완료: {len(property_rows)}개 매물 ===")
            
            results.append({
                'complex_name': complex_info['name'],
                'property_count': result['property_count'],
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

    # ... (전체 결과 요약 로직은 그대로 유지) ...
    total_end_time = time.time()
    total_duration = total_end_time - total_start_time
    
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    total_properties = sum(r['property_count'] for r in results)
    
    print(f"\n=== 23개 단지 크롤링 완료 ===")
    print(f"종료시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"전체 소요시간: {total_duration:.1f}초 ({total_duration/60:.1f}분)")
    print(f"성공한 단지: {len(successful)}개")
    print(f"실패한 단지: {len(failed)}개")
    print(f"총 매물 수: {total_properties}개")
    
    return {
        "status": "success",
        "message": f"23개 단지 크롤링 완료 - 총 {total_properties}개 매물",
        "total_complexes": len(COMPLEXES),
        "successful_complexes": len(successful),
        "failed_complexes": len(failed),
        "total_properties": total_properties,
        "total_duration_seconds": total_duration,
        "total_duration_minutes": total_duration/60,
        "results": results
    }


# GitHub Actions에서 스크립트를 직접 실행하기 위한 진입점 추가
if __name__ == "__main__":
    # 요청을 처리하는 로직 대신, 크롤링 실행 함수를 직접 호출
    execute_crawling_and_record()
