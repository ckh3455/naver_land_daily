#!/usr/bin/env python3
"""
부동산 데이터 정리 전용 스크립트
- 크롤링 없이 기존 시트 데이터만 읽어서 정리
- 모든 정리 규칙 포함 (정렬, 중복 통합, 색상 처리 등)
- 실행 시간: 약 1-2분 (크롤링 30분 → 1-2분으로 단축!)
"""

import json
import re
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ====================================================================
# 상수
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

# 스타일 색상 (RGB)
COLOR_전세 = {"red": 0.996, "green": 0.910, "blue": 0.851}
COLOR_월세 = {"red": 0.882, "green": 0.914, "blue": 0.788}
COLOR_압구정원 = {"red": 0.812, "green": 0.886, "blue": 0.953}
COLOR_집주인_TEXT = {"red": 0.416, "green": 0.659, "blue": 0.310}
COLOR_저빈도_TEXT = {"red": 0.800, "green": 0.000, "blue": 0.000}

EXCLUDED_COMPLEXES = ["메이플자이", "트리마제", "한남더힐", "압구정하이츠파크"]
ALIAS_SHEET_NAME = "압구정 중개업소"
ALIAS_HEADER_NAME = "중개업소명"
ALIAS_HEADER_ID = "중개업소ID"
ALIAS_HEADER_CANON = "실제상호"
LOW_FREQUENCY_THRESHOLD = 3

# ====================================================================
# 헬퍼 함수
# ====================================================================
def normalize(v):
    if v is None:
        return ""
    return str(v).strip().replace("  ", " ").replace("  ", " ")

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

def get_header_col_count(sheet_service, spreadsheet_id, sheet_name, header_row=1):
    """헤더 실제 열수 계산"""
    try:
        result = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!{header_row}:{header_row}"
        ).execute()
        values = result.get('values', [])
        if not values:
            return 21
        header_row_data = values[0]
        for i in range(len(header_row_data) - 1, -1, -1):
            if normalize(header_row_data[i]) != "":
                return i + 1
        return len(header_row_data) if header_row_data else 21
    except Exception as e:
        print(f"헤더 열수 계산 오류: {e}")
        return 21

def load_brokerage_alias_maps(sheet_service, spreadsheet_id):
    """압구정 중개업소 시트에서 매핑 로드"""
    by_name = {}
    by_id = {}
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
            return {"byName": by_name, "byId": by_id}
        result = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{ALIAS_SHEET_NAME}'!A:Z"
        ).execute()
        values = result.get('values', [])
        if len(values) < 2:
            return {"byName": by_name, "byId": by_id}
        header = values[0]
        idx_name = header.index(ALIAS_HEADER_NAME) if ALIAS_HEADER_NAME in header else -1
        idx_id = header.index(ALIAS_HEADER_ID) if ALIAS_HEADER_ID in header else -1
        idx_canon = header.index(ALIAS_HEADER_CANON) if ALIAS_HEADER_CANON in header else -1
        if idx_canon < 0:
            return {"byName": by_name, "byId": by_id}
        for row in values[1:]:
            if len(row) <= idx_canon:
                continue
            canon = normalize(row[idx_canon])
            if not canon:
                continue
            if idx_name >= 0 and len(row) > idx_name:
                name = normalize(row[idx_name])
                if name:
                    by_name[name] = canon
            if idx_id >= 0 and len(row) > idx_id:
                id_val = normalize(row[idx_id])
                if id_val:
                    by_id[id_val] = canon
        print(f"[Alias] byName={len(by_name)}, byId={len(by_id)}")
    except Exception as e:
        print(f"매핑 로드 오류: {e}")
    return {"byName": by_name, "byId": by_id}

def sort_and_group_data(rows):
    """단지별 정렬 (매매→전세→월세 → 면적 → 동)"""
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
    """중복 매물 통합"""
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
        display_name = alias["byName"].get(raw_name)
        if not display_name and raw_id:
            display_name = alias["byId"].get(raw_id)
        if not display_name:
            display_name = raw_name
        owner = normalize(r[COL_집주인] if len(r) > COL_집주인 else "") == "집주인"
        if key not in item_map:
            item_map[key] = {
                "대표행": r.copy() if isinstance(r, list) else list(r),
                "상태": {}
            }
        if display_name:
            prev = item_map[key]["상태"].get(display_name, False)
            item_map[key]["상태"][display_name] = prev or owner
            # 원본 중개업소명 저장 (빨간색 체크용)
            if "원본명" not in item_map[key]:
                item_map[key]["원본명"] = {}
            item_map[key]["원본명"][display_name] = raw_name
            # 디버깅: 처음 몇 개만 로그
            if len(item_map) <= 3:
                print(f"[디버깅] 중복통합: display_name={display_name}, raw_name={raw_name}, 변환여부={display_name != raw_name}")
    for key, info in item_map.items():
        row = info["대표행"]
        names = list(info["상태"].keys())
        dup = len(names)
        while len(row) <= COL_중개업소:
            row.append("")
        row[COL_가격변동] = ""
        row[COL_중복업소수] = dup if dup > 1 else ""
        row[COL_중개업소] = ", ".join(names)
        has_apgujeong_one = any("압구정원" in n for n in names)
        infos.append({
            "행": row,
            "중복여부": dup > 1,
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
    """표시이름 기준 전체 빈도수"""
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

def apply_styles_and_alignment(sheet_service, spreadsheet_id, sheet_id, infos, header_row, col_count, brokerage_counts, alias):
    """스타일 적용 (배경색, 폰트, RichText)"""
    try:
        row_count = len(infos)
        if not row_count:
            return
        print(f"스타일 적용 시작: {row_count}행")
        requests = []
        for idx, it in enumerate(infos):
            row = it["행"]
            complex_name = row[COL_단지명] if len(row) > COL_단지명 else ""
            trade = row[COL_거래구분] if len(row) > COL_거래구분 else ""
            bg_color = None
            if it["압구정원포함"]:
                bg_color = COLOR_압구정원
            elif trade == "전세":
                bg_color = COLOR_전세
            elif trade == "월세":
                bg_color = COLOR_월세
            is_bold = it["압구정원포함"] or it["중복여부"]
            for c in range(col_count):
                row_num = header_row + 1 + idx
                if bg_color:
                    requests.append({
                        "updateCells": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": row_num - 1,
                                "endRowIndex": row_num,
                                "startColumnIndex": c,
                                "endColumnIndex": c + 1
                            },
                            "rows": [{
                                "values": [{
                                    "userEnteredFormat": {
                                        "backgroundColor": bg_color
                                    }
                                }]
                            }],
                            "fields": "userEnteredFormat.backgroundColor"
                        }
                    })
                if is_bold:
                    requests.append({
                        "updateCells": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": row_num - 1,
                                "endRowIndex": row_num,
                                "startColumnIndex": c,
                                "endColumnIndex": c + 1
                            },
                            "rows": [{
                                "values": [{
                                    "userEnteredFormat": {
                                        "textFormat": {
                                            "bold": True
                                        }
                                    }
                                }]
                            }],
                            "fields": "userEnteredFormat.textFormat.bold"
                        }
                    })
            if len(row) > COL_중개업소:
                joined = str(row[COL_중개업소] or "")
                names = [normalize(s) for s in joined.split(",") if normalize(s)]
                if names:
                    rich_text_runs = []
                    # joined 문자열에서 정확한 위치 찾기
                    current_pos = 0
                    for name_idx, name in enumerate(names):
                        # joined에서 name의 정확한 위치 찾기
                        name_start = joined.find(name, current_pos)
                        if name_start == -1:
                            # 찾을 수 없으면 현재 위치 사용
                            name_start = current_pos
                        name_end = name_start + len(name)
                        
                        if name_end <= len(joined):
                            was_owner = it["상태"].get(name, False)
                            
                            # 제외 단지가 아니면 색상 체크
                            if complex_name not in EXCLUDED_COMPLEXES:
                                # 원본 중개업소명 가져오기 (변환 전 값)
                                원본명 = it.get("원본명", {}).get(name, name)
                                # 압구정 중개업소 시트의 "중개업소명" 열에 없는 업소명 확인
                                not_in_alias = 원본명 not in alias.get("byName", {})
                                # 저빈도 업소 확인
                                low = brokerage_counts.get(name, 0) <= LOW_FREQUENCY_THRESHOLD
                                
                                # 디버깅 로그 (처음 몇 개만)
                                if idx < 5 and name_idx == 0:
                                    print(f"\n[디버깅] 행 {idx+1}: 단지={complex_name}")
                                    print(f"  표시명='{name}', 원본명='{원본명}'")
                                    print(f"  was_owner={was_owner}, not_in_alias={not_in_alias}, low={low}")
                                    print(f"  alias['byName'] 키 개수: {len(alias.get('byName', {}))}")
                                    print(f"  원본명이 alias에 있는지: {원본명 in alias.get('byName', {})}")
                                    if 원본명 in alias.get('byName', {}):
                                        print(f"  매핑된 실제상호: '{alias.get('byName', {}).get(원본명)}'")
                                    print(f"  저빈도 체크: {name} = {brokerage_counts.get(name, 0)}개 (임계값: {LOW_FREQUENCY_THRESHOLD})")
                                
                                if was_owner:
                                    color = COLOR_집주인_TEXT
                                    if idx < 5 and name_idx == 0:
                                        print(f"  → 초록색 적용 (집주인)")
                                elif not_in_alias:
                                    # 압구정 중개업소 시트에 없는 업소명 → 빨간색
                                    color = COLOR_저빈도_TEXT
                                    if idx < 5 and name_idx == 0:
                                        print(f"  ✅ 빨간색 적용: '{name}' (원본명 '{원본명}'이 alias에 없음)")
                                elif low:
                                    color = COLOR_저빈도_TEXT
                                    if idx < 5 and name_idx == 0:
                                        print(f"  ✅ 빨간색 적용: '{name}' (저빈도: {brokerage_counts.get(name, 0)}개)")
                                else:
                                    color = None
                                    if idx < 5 and name_idx == 0:
                                        print(f"  → 색상 없음")
                            else:
                                # 제외 단지는 색상 적용 안 함
                                color = None
                            
                            if color:
                                rich_text_runs.append({
                                    "startIndex": name_start,
                                    "endIndex": name_end,
                                    "format": {
                                        "foregroundColor": color
                                    }
                                })
                            
                            # 다음 이름 위치로 이동 (", " 고려)
                            current_pos = name_end
                            if name_idx < len(names) - 1:  # 마지막이 아니면
                                comma_pos = joined.find(", ", current_pos)
                                if comma_pos != -1:
                                    current_pos = comma_pos + 2
                                else:
                                    current_pos = name_end
                    if rich_text_runs:
                        row_num = header_row + 1 + idx
                        if idx < 5:
                            print(f"  RichText 적용: {len(rich_text_runs)}개 구간")
                            for rt in rich_text_runs:
                                print(f"    [{rt['startIndex']}:{rt['endIndex']}] = '{joined[rt['startIndex']:rt['endIndex']]}'")
                        
                        # RichText 형식으로 적용
                        requests.append({
                            "updateCells": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "startRowIndex": row_num - 1,
                                    "endRowIndex": row_num,
                                    "startColumnIndex": COL_중개업소,
                                    "endColumnIndex": COL_중개업소 + 1
                                },
                                "rows": [{
                                    "values": [{
                                        "userEnteredValue": {
                                            "stringValue": joined
                                        },
                                        "userEnteredFormat": {
                                            "textFormat": {
                                                "foregroundColor": {"red": 0, "green": 0, "blue": 0}
                                            }
                                        },
                                        "textFormatRuns": rich_text_runs
                                    }]
                                }],
                                "fields": "userEnteredValue.stringValue,userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.textFormatRuns"
                            }
                        })
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
                    "cell": {
                        "userEnteredFormat": {
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat.horizontalAlignment"
                }
            })
        if requests:
            print(f"스타일 요청 {len(requests)}개 실행 중...")
            body = {"requests": requests}
            sheet_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=body
            ).execute()
            print("스타일 적용 완료")
    except Exception as e:
        print(f"스타일 적용 오류: {e}")
        import traceback
        traceback.print_exc()

# ====================================================================
# 메인 함수
# ====================================================================
def process_real_estate_data():
    """부동산 데이터 처리 메인 함수"""
    try:
        print("\n" + "="*70)
        print("🧪 데이터 정리 로직 테스트 (크롤링 제외)")
        print("="*70 + "\n")
        
        # 서비스 계정 인증
        service_account_file = 'service_account.json'
        if not os.path.exists(service_account_file):
            print(f"❌ 오류: {service_account_file} 파일을 찾을 수 없습니다.")
            print("\n💡 해결 방법:")
            print("   1. GitHub Secrets에서 SERVICE_ACCOUNT_JSON 복사")
            print(f"   2. {service_account_file} 파일로 저장")
            return
        
        with open(service_account_file, 'r', encoding='utf-8') as f:
            creds_info = json.load(f)
        
        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        
        sheet_service = build('sheets', 'v4', credentials=credentials)
        
        # 스프레드시트 ID
        spreadsheet_id = os.getenv('SPREADSHEET_ID', '1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4')
        
        print(f"📊 스프레드시트 ID: {spreadsheet_id}")
        print("🔄 데이터 정리 시작...\n")
        
        # 시트 정보 가져오기
        sheet_metadata = sheet_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()
        
        # 첫 번째 시트 (또는 활성 시트) 찾기
        main_sheet = None
        main_sheet_id = None
        for sheet in sheet_metadata.get('sheets', []):
            props = sheet['properties']
            if props.get('sheetId') == 103445093:  # gid=103445093
                main_sheet = props['title']
                main_sheet_id = props['sheetId']
                break
        
        if not main_sheet:
            # 첫 번째 시트 사용
            main_sheet = sheet_metadata['sheets'][0]['properties']['title']
            main_sheet_id = sheet_metadata['sheets'][0]['properties']['sheetId']
        
        print(f"시트명: {main_sheet}, Sheet ID: {main_sheet_id}")
        
        # 데이터 읽기
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
        
        # 헤더 열수 계산
        header_col_count = get_header_col_count(sheet_service, spreadsheet_id, main_sheet, header_row)
        print(f"헤더 열수: {header_col_count}")
        
        # 매핑 로드
        alias = load_brokerage_alias_maps(sheet_service, spreadsheet_id)
        print(f"[디버깅] alias['byName'] 샘플 (처음 5개):")
        for i, (k, v) in enumerate(list(alias.get("byName", {}).items())[:5]):
            print(f"  {i+1}. '{k}' -> '{v}'")
        
        # 데이터 읽기 (헤더 제외)
        raw_data = values[header_row:]
        print(f"읽은 데이터 행수: {len(raw_data)}")
        
        # 정렬
        sorted_data = sort_and_group_data(raw_data)
        print(f"정렬된 데이터 행수: {len(sorted_data)}")
        
        # 중복 통합
        result_data = process_duplicate_listings(sorted_data, alias)
        final_data_rows = result_data["finalDataRows"]
        final_data_infos = result_data["finalDataInfos"]
        print(f"통합된 데이터 행수: {len(final_data_rows)}")
        
        # 빈도수 계산
        brokerage_counts = get_brokerage_counts(final_data_rows)
        print(f"중개업소 종류 수: {len(brokerage_counts)}")
        
        # 빨간색으로 표시될 업소명 찾기
        print("\n" + "="*70)
        print("🔴 빨간색으로 표시될 업소명 목록")
        print("="*70)
        red_list = []
        for idx, it in enumerate(final_data_infos):
            row = it["행"]
            complex_name = row[COL_단지명] if len(row) > COL_단지명 else ""
            
            # 제외 단지 스킵
            if complex_name in EXCLUDED_COMPLEXES:
                continue
            
            if len(row) > COL_중개업소:
                joined = str(row[COL_중개업소] or "")
                names = [normalize(s) for s in joined.split(",") if normalize(s)]
                
                for name in names:
                    was_owner = it["상태"].get(name, False)
                    원본명 = it.get("원본명", {}).get(name, name)
                    not_in_alias = 원본명 not in alias.get("byName", {})
                    low = brokerage_counts.get(name, 0) <= LOW_FREQUENCY_THRESHOLD
                    
                    # 빨간색 조건 확인
                    if not was_owner and (not_in_alias or low):
                        reason = "압구정 중개업소 시트에 없음" if not_in_alias else f"저빈도 ({brokerage_counts.get(name, 0)}개)"
                        red_list.append({
                            "단지": complex_name,
                            "표시명": name,
                            "원본명": 원본명,
                            "이유": reason,
                            "행": idx + 2  # 헤더 포함
                        })
        
        if red_list:
            print(f"총 {len(red_list)}개 업소명이 빨간색으로 표시됩니다:\n")
            for i, item in enumerate(red_list[:50], 1):  # 최대 50개만 출력
                print(f"  {i}. [{item['단지']}] '{item['표시명']}' (원본: '{item['원본명']}') - {item['이유']}")
            if len(red_list) > 50:
                print(f"\n  ... 외 {len(red_list) - 50}개 더")
        else:
            print("빨간색으로 표시될 업소명이 없습니다.")
        print("="*70 + "\n")
        
        # 데이터 쓰기 준비
        col_count = len(final_data_rows[0]) if final_data_rows else header_col_count
        
        # 기존 데이터 및 서식 완전히 지우기 (헤더는 유지)
        print("기존 데이터 및 서식 지우기 중...")
        
        # 실제 데이터 행 수 기반으로 범위 계산
        estimated_max_rows = max(last_row, len(final_data_rows) + 500) if final_data_rows else last_row
        
        # 1. 서식 지우기 (배경색, 폰트, 정렬 등 모두 제거)
        clear_requests = [{
            "repeatCell": {
                "range": {
                    "sheetId": main_sheet_id,
                    "startRowIndex": header_row,
                    "endRowIndex": estimated_max_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": col_count
                },
                "cell": {
                    "userEnteredFormat": {}
                },
                "fields": "userEnteredFormat"
            }
        }]
        
        # 배치 실행
        body = {"requests": clear_requests}
        sheet_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body
        ).execute()
        
        # 2. 값도 지우기
        clear_range = f"'{main_sheet}'!{header_row + 1}:{estimated_max_rows}"
        sheet_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=clear_range
        ).execute()
        
        print("기존 데이터 및 서식 지우기 완료")
        
        # 데이터 쓰기
        if final_data_rows:
            # 행 길이 맞추기
            for row in final_data_rows:
                while len(row) < col_count:
                    row.append("")
            
            write_range = f"'{main_sheet}'!{header_row + 1}:{header_row + len(final_data_rows)}"
            body = {
                'values': final_data_rows
            }
            sheet_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=write_range,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()
            print("데이터 쓰기 완료")
        
        # 스타일 적용
        apply_styles_and_alignment(
            sheet_service, spreadsheet_id, main_sheet_id,
            final_data_infos, header_row, col_count, brokerage_counts, alias
        )
        
        print("\n" + "="*70)
        print("=== 부동산데이터처리 완료 ===")
        print(f"총 매물: {len(final_data_rows)}개")
        print("="*70 + "\n")
        
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == "__main__":
    process_real_estate_data()
