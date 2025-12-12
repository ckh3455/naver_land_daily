#!/usr/bin/env python3
"""
부동산 데이터 정리 전용(크롤링 없음) + 철저 디버깅 버전 (전체본)
- 기존 시트 데이터만 읽어서 정리(정렬/중복통합/표시색/볼드 적용)
- 디버깅:
  * 초록/빨강/볼드 판정 샘플 10개씩 출력
  * 초록+빨강이 같은 행에 함께 존재하는 케이스 10개 출력
  * 조건부서식 존재 여부 출력(충돌 가능성 점검)
  * 스타일 요청 개수/종류 출력
  * batchUpdate 실패 시 에러 바디 출력
- 핵심 수정: textFormatRuns.startIndex는 반드시 len(text)보다 작아야 함(==len 금지)
"""

import json
import re
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ====================================================================
# 상수 (컬럼 인덱스)
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

# ====================================================================
# 스타일 색상 (RGB)
# ====================================================================
COLOR_전세 = {"red": 0.996, "green": 0.910, "blue": 0.851}
COLOR_월세 = {"red": 0.882, "green": 0.914, "blue": 0.788}
COLOR_압구정원 = {"red": 0.812, "green": 0.886, "blue": 0.953}

COLOR_집주인_TEXT = {"red": 0.416, "green": 0.659, "blue": 0.310}
COLOR_저빈도_TEXT = {"red": 0.800, "green": 0.000, "blue": 0.000}
DEFAULT_BLACK = {"red": 0, "green": 0, "blue": 0}

EXCLUDED_COMPLEXES = ["메이플자이", "트리마제", "한남더힐", "압구정하이츠파크"]
ALIAS_SHEET_NAME = "압구정 중개업소"
ALIAS_HEADER_NAME = "중개업소명"
ALIAS_HEADER_ID = "중개업소ID"
ALIAS_HEADER_CANON = "실제상호"
LOW_FREQUENCY_THRESHOLD = 3

# ====================================================================
# 헬퍼
# ====================================================================
def normalize(v):
    if v is None:
        return ""
    return str(v).strip().replace("  ", " ").replace("  ", " ")

def extract_dong_number(s):
    if not isinstance(s, str):
        return 0
    cleaned = re.sub(r"[^\d-]", "", s)
    try:
        return float(cleaned.replace("-", "."))
    except Exception:
        return 0

def extract_area_number(s):
    if not s:
        return 0
    s = str(s).strip()
    if not s:
        return 0
    numbers = re.findall(r"\d+\.?\d*", s)
    if numbers:
        try:
            return float(numbers[0])
        except Exception:
            return 0
    return 0

def get_header_col_count(sheet_service, spreadsheet_id, sheet_name, header_row=1):
    """헤더 실제 열수 계산(디버그용)"""
    try:
        result = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!{header_row}:{header_row}"
        ).execute()
        values = result.get("values", [])
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
    """
    압구정 중개업소 시트에서 매핑 로드
    - byName: 중개업소명 -> 실제상호
    - byId:   중개업소ID -> 실제상호
    - validCanonNames: 실제상호 컬럼의 전체 값 set
    """
    by_name = {}
    by_id = {}
    valid_canon_names = set()

    try:
        meta = sheet_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        alias_sheet_id = None
        for sh in meta.get("sheets", []):
            if sh.get("properties", {}).get("title") == ALIAS_SHEET_NAME:
                alias_sheet_id = sh["properties"]["sheetId"]
                break

        if alias_sheet_id is None:
            print(f"'{ALIAS_SHEET_NAME}' 시트를 찾을 수 없습니다.")
            return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names}

        res = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{ALIAS_SHEET_NAME}'!A:Z"
        ).execute()
        values = res.get("values", [])
        if len(values) < 2:
            return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names}

        header = values[0]
        idx_name = header.index(ALIAS_HEADER_NAME) if ALIAS_HEADER_NAME in header else -1
        idx_id = header.index(ALIAS_HEADER_ID) if ALIAS_HEADER_ID in header else -1
        idx_canon = header.index(ALIAS_HEADER_CANON) if ALIAS_HEADER_CANON in header else -1
        if idx_canon < 0:
            return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names}

        for row in values[1:]:
            if len(row) <= idx_canon:
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

        print(f"[Alias] byName={len(by_name)}, byId={len(by_id)}, validCanonNames={len(valid_canon_names)}")
    except Exception as e:
        print(f"매핑 로드 오류: {e}")

    return {"byName": by_name, "byId": by_id, "validCanonNames": valid_canon_names}

# ====================================================================
# 정렬/중복통합 (기존 로직 유지)
# ====================================================================
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
    """중복 매물 통합 (기존 로직 유지)"""
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
                "상태": {},
                "원본명": {}
            }

        if display_name:
            prev = item_map[key]["상태"].get(display_name, False)
            item_map[key]["상태"][display_name] = prev or owner
            item_map[key]["원본명"][display_name] = raw_name  # 디버그용 원본명

    for _, info in item_map.items():
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
    """표시이름 기준 전체 빈도수 (기존 로직 유지)"""
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

# ====================================================================
# 디버그: 조건부서식 존재 여부
# ====================================================================
def debug_check_conditional_formats(sheet_service, spreadsheet_id, target_sheet_id):
    meta = sheet_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title),conditionalFormats)"
    ).execute()

    for sh in meta.get("sheets", []):
        props = sh.get("properties", {})
        if props.get("sheetId") == target_sheet_id:
            cfs = sh.get("conditionalFormats", []) or []
            print("\n" + "="*80)
            print(f"🧪 [디버그] 조건부서식 개수: {len(cfs)} (sheet='{props.get('title')}', id={target_sheet_id})")
            if cfs:
                for i, cf in enumerate(cfs[:3], 1):
                    rngs = cf.get("ranges", [])
                    print(f" - 샘플{i}: ranges={len(rngs)} ruleKeys={list(cf.keys())}")
                if len(cfs) > 3:
                    print(f"   ... (외 {len(cfs)-3}개)")
            print("="*80)
            return

# ====================================================================
# 디버그: 초록/빨강/볼드/혼합행 샘플 출력
# ====================================================================
def debug_print_samples(final_data_infos, brokerage_counts, alias, header_row=1, max_each=10):
    greens, reds, bolds, mixed = [], [], [], []

    validCanon = alias.get("validCanonNames", set())

    for idx, it in enumerate(final_data_infos):
        row = it["행"]
        complex_name = row[COL_단지명] if len(row) > COL_단지명 else ""
        trade = row[COL_거래구분] if len(row) > COL_거래구분 else ""

        joined = str(row[COL_중개업소] or "") if len(row) > COL_중개업소 else ""
        names = [normalize(s) for s in joined.split(",") if normalize(s)]

        is_bold = bool(it.get("압구정원포함")) or bool(it.get("중복여부"))
        if is_bold and len(bolds) < max_each:
            bolds.append({
                "row": header_row + 1 + idx,
                "단지": complex_name,
                "거래": trade,
                "중복여부": it.get("중복여부"),
                "압구정원포함": it.get("압구정원포함"),
                "중개업소": joined[:120] + ("..." if len(joined) > 120 else "")
            })

        if complex_name in EXCLUDED_COMPLEXES:
            continue

        row_has_green = False
        row_has_red = False

        for name in names:
            was_owner = it.get("상태", {}).get(name, False)
            not_in_alias = name not in validCanon
            low = brokerage_counts.get(name, 0) <= LOW_FREQUENCY_THRESHOLD

            if was_owner:
                row_has_green = True
                if len(greens) < max_each:
                    greens.append({
                        "row": header_row + 1 + idx,
                        "단지": complex_name,
                        "거래": trade,
                        "표시명": name,
                        "freq": brokerage_counts.get(name, 0),
                        "not_in_alias(validCanon)": not_in_alias,
                        "low": low
                    })
            else:
                if not_in_alias or low:
                    row_has_red = True
                    if len(reds) < max_each:
                        reason = "ALIAS탭(실제상호)없음" if not_in_alias else f"저빈도({brokerage_counts.get(name, 0)}개)"
                        reds.append({
                            "row": header_row + 1 + idx,
                            "단지": complex_name,
                            "거래": trade,
                            "표시명": name,
                            "freq": brokerage_counts.get(name, 0),
                            "이유": reason
                        })

        if row_has_green and row_has_red and len(mixed) < max_each:
            mixed.append({
                "row": header_row + 1 + idx,
                "단지": complex_name,
                "거래": trade,
                "중개업소": joined[:160] + ("..." if len(joined) > 160 else "")
            })

        if len(greens) >= max_each and len(reds) >= max_each and len(bolds) >= max_each and len(mixed) >= max_each:
            break

    print("\n" + "="*90)
    print("🧪 [디버그] 색/볼드 판정 샘플 출력(정리 적용 전)")
    print("="*90)

    print(f"\n🟩 초록(집주인) 대상 샘플 {len(greens)}/{max_each}")
    for i, x in enumerate(greens, 1):
        print(f" {i:02d}) R{x['row']} [{x['단지']}/{x['거래']}] '{x['표시명']}' freq={x['freq']} not_in_alias={x['not_in_alias(validCanon)']} low={x['low']}")

    print(f"\n🟥 빨강(미등록/저빈도) 대상 샘플 {len(reds)}/{max_each}")
    for i, x in enumerate(reds, 1):
        print(f" {i:02d}) R{x['row']} [{x['단지']}/{x['거래']}] '{x['표시명']}' freq={x['freq']} 이유={x['이유']}")

    print(f"\n🅱️ 볼드 대상 행 샘플 {len(bolds)}/{max_each}")
    for i, x in enumerate(bolds, 1):
        print(f" {i:02d}) R{x['row']} [{x['단지']}/{x['거래']}] 중복={x['중복여부']} 압구정원포함={x['압구정원포함']} 중개업소='{x['중개업소']}'")

    print(f"\n🟩+🟥 같은 행에 초록/빨강이 함께 존재하는 샘플 {len(mixed)}/{max_each}")
    for i, x in enumerate(mixed, 1):
        print(f" {i:02d}) R{x['row']} [{x['단지']}/{x['거래']}] 중개업소='{x['중개업소']}'")

    print("="*90 + "\n")

# ====================================================================
# 표시(서식) 적용: 안정형(요청수 폭발 방지 + RichText 정상 적용)
# ====================================================================
def apply_styles_and_alignment(sheet_service, spreadsheet_id, sheet_id, infos, header_row, col_count, brokerage_counts, alias):
    """
    - 배경/볼드: 행 단위 repeatCell 적용(요청수 최소화)
    - 중개업소 컬럼: textFormatRuns로 부분색(초록/빨강) 적용
    - 정렬: 1~8열 가운데 정렬
    - batchUpdate 실패시 HttpError content 출력
    """
    row_count = len(infos)
    if not row_count:
        return

    validCanon = alias.get("validCanonNames", set())

    def build_text_runs(text, segments, debug_row_num=None):
        """
        segments: [{"start": int, "end": int, "color": {...}}, ...]
        IMPORTANT:
          - Sheets API: TextFormatRun.startIndex는 반드시 len(text)보다 작아야 함.
          - 따라서 end==len(text) 위치에 '검정 복귀 run'을 넣으면 400 발생.
        """
        if not text:
            return None  # 빈 문자열이면 runs 자체를 쓰지 않음

        L = len(text)

        # 이벤트 맵: pos -> color
        events = {0: DEFAULT_BLACK}

        def set_event(pos, color):
            # 핵심: pos는 반드시 0 <= pos < L 이어야 함
            if not isinstance(pos, int):
                return
            if pos < 0 or pos >= L:
                return

            # 같은 pos 충돌 시: 검정 < 색 우선
            if pos not in events:
                events[pos] = color
                return
            if events[pos] == DEFAULT_BLACK and color != DEFAULT_BLACK:
                events[pos] = color

        for seg in segments:
            s = seg["start"]
            e = seg["end"]
            c = seg["color"]

            if not (isinstance(s, int) and isinstance(e, int)):
                continue
            if s < 0 or s >= L:
                continue
            if e <= s:
                continue
            # e는 L까지 가능하지만, 이벤트(pos)로는 e==L을 넣으면 안 됨
            if e > L:
                continue

            set_event(s, c)
            if e < L:
                set_event(e, DEFAULT_BLACK)  # e==L이면 추가하지 않음(중요)

        # runs 생성
        runs = []
        prev_color = None
        for pos in sorted(events.keys()):
            color = events[pos]
            if prev_color is None or color != prev_color:
                runs.append({"startIndex": pos, "format": {"foregroundColor": color}})
                prev_color = color

        # 방어: startIndex가 len(text) 이상인 run 제거 + 로그
        bad = [r for r in runs if r.get("startIndex", -1) >= L]
        if bad:
            print(f"[디버그] ❗ 잘못된 textFormatRuns 발견(제거): row={debug_row_num}, len={L}, bad={bad}")
            runs = [r for r in runs if r.get("startIndex", -1) < L]

        # 방어: 첫 run이 0이 아니면 0 run 추가
        if runs and runs[0]["startIndex"] != 0:
            runs.insert(0, {"startIndex": 0, "format": {"foregroundColor": DEFAULT_BLACK}})

        # 최종적으로 runs가 비면 None
        return runs if runs else None

    print(f"스타일 적용 시작: {row_count}행")
    requests = []
    req_bg_bold = 0
    req_rich = 0

    for idx, it in enumerate(infos):
        row = it["행"]
        complex_name = row[COL_단지명] if len(row) > COL_단지명 else ""
        trade = row[COL_거래구분] if len(row) > COL_거래구분 else ""

        # 배경색 결정
        bg_color = None
        if it.get("압구정원포함"):
            bg_color = COLOR_압구정원
        elif trade == "전세":
            bg_color = COLOR_전세
        elif trade == "월세":
            bg_color = COLOR_월세

        # 볼드
        is_bold = bool(it.get("압구정원포함")) or bool(it.get("중복여부"))

        row_num_1based = header_row + 1 + idx
        start_row = row_num_1based - 1
        end_row = row_num_1based

        # 배경/볼드: 행 단위 repeatCell
        fmt = {}
        fields = []
        if bg_color:
            fmt["backgroundColor"] = bg_color
            fields.append("userEnteredFormat.backgroundColor")
        if is_bold:
            fmt.setdefault("textFormat", {})
            fmt["textFormat"]["bold"] = True
            fields.append("userEnteredFormat.textFormat.bold")

        if fields:
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row,
                        "endRowIndex": end_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": col_count
                    },
                    "cell": {"userEnteredFormat": fmt},
                    "fields": ",".join(fields)
                }
            })
            req_bg_bold += 1

        # 중개업소 부분색
        if len(row) > COL_중개업소:
            joined = str(row[COL_중개업소] or "")
            names = [normalize(s) for s in joined.split(",") if normalize(s)]

            if joined and names:
                segments = []
                cur = 0
                for name_idx, name in enumerate(names):
                    s = joined.find(name, cur)
                    if s == -1:
                        s = joined.find(name)
                    if s == -1:
                        continue
                    e = s + len(name)

                    was_owner = it.get("상태", {}).get(name, False)

                    if complex_name in EXCLUDED_COMPLEXES:
                        color = None
                    else:
                        not_in_alias = name not in validCanon
                        low = brokerage_counts.get(name, 0) <= LOW_FREQUENCY_THRESHOLD

                        if was_owner:
                            color = COLOR_집주인_TEXT
                        elif not_in_alias:
                            color = COLOR_저빈도_TEXT
                        elif low:
                            color = COLOR_저빈도_TEXT
                        else:
                            color = None

                    if color:
                        segments.append({"start": s, "end": e, "color": color})

                    cur = e
                    if name_idx < len(names) - 1:
                        comma_pos = joined.find(", ", cur)
                        if comma_pos != -1:
                            cur = comma_pos + 2

                if segments:
                    runs = build_text_runs(joined, segments, debug_row_num=row_num_1based)

                    if runs:
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
                                        "userEnteredFormat": {
                                            "textFormat": {"foregroundColor": DEFAULT_BLACK}
                                        },
                                        "textFormatRuns": runs
                                    }]
                                }],
                                "fields": "userEnteredValue,userEnteredFormat.textFormat.foregroundColor,textFormatRuns"
                            }
                        })
                        req_rich += 1

    # 정렬(센터)
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

    print(f"[디버그] 스타일 요청 생성: 총 {len(requests)}개 (배경/볼드 repeatCell={req_bg_bold}, RichText updateCells={req_rich})")

    if not requests:
        print("적용할 스타일 요청이 없습니다.")
        return

    # batchUpdate는 요청 수/바디 크기에 민감 → 분할 실행
    CHUNK = 600
    try:
        for i in range(0, len(requests), CHUNK):
            chunk = requests[i:i+CHUNK]
            print(f"[디버그] batchUpdate 실행: {i+1} ~ {i+len(chunk)}")
            sheet_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": chunk}
            ).execute()
        print("스타일 적용 완료")
    except HttpError as e:
        print("\n" + "="*90)
        print("❌ [디버그] batchUpdate 실패(HttpError)")
        try:
            print(e.content.decode("utf-8"))
        except Exception:
            print(str(e))
        print("="*90)
        raise

# ====================================================================
# 메인 처리(정리만)
# ====================================================================
def process_real_estate_data():
    try:
        print("\n" + "="*70)
        print("🧪 데이터 정리 디버깅 테스트 (크롤링 제외)")
        print("="*70 + "\n")

        service_account_file = "service_account.json"
        if not os.path.exists(service_account_file):
            print(f"❌ 오류: {service_account_file} 파일을 찾을 수 없습니다.")
            return

        with open(service_account_file, "r", encoding="utf-8") as f:
            creds_info = json.load(f)

        credentials = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        sheet_service = build("sheets", "v4", credentials=credentials)

        spreadsheet_id = os.getenv("SPREADSHEET_ID", "1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4")
        print(f"📊 스프레드시트 ID: {spreadsheet_id}")

        meta = sheet_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

        # 메인 시트 찾기 (gid=103445093 우선)
        main_sheet = None
        main_sheet_id = None
        for sh in meta.get("sheets", []):
            props = sh.get("properties", {})
            if props.get("sheetId") == 103445093:
                main_sheet = props.get("title")
                main_sheet_id = props.get("sheetId")
                break
        if not main_sheet:
            props = meta["sheets"][0]["properties"]
            main_sheet = props["title"]
            main_sheet_id = props["sheetId"]

        print(f"시트명: {main_sheet}, Sheet ID: {main_sheet_id}")

        header_row = 1  # 데이터는 2행부터
        values_res = sheet_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{main_sheet}'!A:Z"
        ).execute()
        values = values_res.get("values", [])
        if len(values) <= header_row:
            print("처리할 데이터가 없습니다.")
            return

        last_row = len(values)
        print(f"마지막 행(읽기 기준): {last_row}")

        header_col_count = get_header_col_count(sheet_service, spreadsheet_id, main_sheet, header_row)
        print(f"헤더 열수(추정): {header_col_count}")

        alias = load_brokerage_alias_maps(sheet_service, spreadsheet_id)

        raw_data = values[header_row:]
        print(f"읽은 원본 데이터 행수: {len(raw_data)}")

        sorted_data = sort_and_group_data(raw_data)
        print(f"정렬된 데이터 행수: {len(sorted_data)}")

        result_data = process_duplicate_listings(sorted_data, alias)
        final_data_rows = result_data["finalDataRows"]
        final_data_infos = result_data["finalDataInfos"]
        print(f"통합된 데이터 행수: {len(final_data_rows)}")

        brokerage_counts = get_brokerage_counts(final_data_rows)
        print(f"중개업소 종류 수(빈도 dict): {len(brokerage_counts)}")

        debug_check_conditional_formats(sheet_service, spreadsheet_id, main_sheet_id)
        debug_print_samples(final_data_infos, brokerage_counts, alias, header_row=header_row, max_each=10)

        # ---- 실제 시트 반영 ----
        col_count = len(final_data_rows[0]) if final_data_rows else header_col_count

        print("기존 데이터/서식 지우기(헤더 제외) 중...")
        estimated_max_rows = max(last_row, len(final_data_rows) + 500) if final_data_rows else last_row

        # 서식 초기화(userEnteredFormat만 초기화)
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

        # 값 삭제
        clear_range = f"'{main_sheet}'!{header_row + 1}:{estimated_max_rows}"
        sheet_service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=clear_range
        ).execute()

        print("기존 데이터/서식 지우기 완료")

        # 데이터 쓰기
        if final_data_rows:
            for row in final_data_rows:
                while len(row) < col_count:
                    row.append("")

            write_range = f"'{main_sheet}'!{header_row + 1}:{header_row + len(final_data_rows)}"
            sheet_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=write_range,
                valueInputOption="USER_ENTERED",
                body={"values": final_data_rows}
            ).execute()
            print("정리 데이터 쓰기 완료")

        # 스타일 적용
        apply_styles_and_alignment(
            sheet_service, spreadsheet_id, main_sheet_id,
            final_data_infos, header_row, col_count, brokerage_counts, alias
        )

        print("\n" + "="*70)
        print("✅ 정리(디버그) 완료")
        print(f"총 매물(정리 후): {len(final_data_rows)}개")
        print("="*70 + "\n")

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == "__main__":
    process_real_estate_data()
