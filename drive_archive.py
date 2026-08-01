#!/usr/bin/env python3
"""네이버 매물 개별 원본을 Google 공유 드라이브에 보관한다.

기존 ``main_github.py``의 수집·중복처리 로직은 변경하지 않고,
중복처리 직전 Google Sheet에 기록된 개별 광고 행을 받아 다음을 수행한다.

- 압구정 대상 단지의 개별 광고 일일 원본 저장
- 전일 공식 스냅샷과 비교한 신규·가격변경·미노출 이력 저장
- 당일 공식 스냅샷과 latest 스냅샷 갱신
- 실행 로그 저장
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

SHARED_DRIVE_ID = os.getenv("NAVER_SHARED_DRIVE_ID", "0APa-MWwUseXzUk9PVA")
RAW_FOLDER_ID = os.getenv("NAVER_RAW_FOLDER_ID", "1VaNfmXUvStoyEy3ky11GwJ2Y8U5v97eb")
OFFICIAL_FOLDER_ID = os.getenv("NAVER_OFFICIAL_FOLDER_ID", "1jICqHFCLXwE0mFI7u4KV89IslIhxtBkA")
CHANGE_FOLDER_ID = os.getenv("NAVER_CHANGE_FOLDER_ID", "1n--RaXCyjz8RAovf9uwtKabUIKj50wZl")
LOG_FOLDER_ID = os.getenv("NAVER_LOG_FOLDER_ID", "17lQp0aXVfmAuqM14GmkgBPvtfjA_t0UN")

TREND_SHEET_NAME = "광고동향"
TREND_HEADERS = [
    "기준일", "구역", "단지명", "거래구분", "면적", "동", "층표시",
    "표시조건군ID", "광고수", "전일광고수", "광고수증감", "업소수",
    "신규광고", "미노출광고", "가격인하광고", "가격인상광고",
    "최저가(억)", "전일최저가(억)", "최저가증감(억)", "10%가격(억)",
    "중앙가(억)", "전일중앙가(억)", "중앙가증감(억)", "방향",
    "최빈가(억)", "최고가(억)", "집주인광고",
]

ZONE_BY_COMPLEX = {
    "미성1차": "1구역",
    "미성2차": "1구역",
    "신현대": "2구역",
    "현대3차": "3구역",
    "현대1,2차": "3구역",
    "현대4차": "3구역",
    "현대5차": "3구역",
    "현대10,13,14차": "3구역",
    "현대6,7차": "3구역",
    "현대65동(대림아크로빌)": "3구역",
    "현대빌라트": "3구역",
    "대림빌라트": "3구역",
    "현대8차": "4구역",
    "한양3차": "4구역",
    "한양4차": "4구역",
    "한양6차": "4구역",
    "영동한양1차": "5구역",
    "한양2차": "5구역",
    "한양5차": "6구역",
    "한양7차": "6구역",
    "한양8차": "6구역",
    "압구정하이츠파크": "기타",
}

# 비교단지(트리마제·메이플자이·한남더힐)는 원본 장기보관 대상에서 제외한다.
# 압구정하이츠파크는 압구정동 소재이므로 포함한다.
APGUJEONG_COMPLEXES = {
    "미성1차", "미성2차", "신현대", "현대3차", "현대1,2차",
    "현대4차", "현대5차", "현대10,13,14차", "현대6,7차",
    "현대65동(대림아크로빌)", "현대빌라트", "대림빌라트", "현대8차",
    "한양3차", "한양4차", "한양6차", "영동한양1차", "한양2차",
    "한양5차", "한양7차", "한양8차", "압구정하이츠파크",
}

META_HEADERS = ["수집일", "수집시각", "러너", "실행구분"]
ARTICLE_KEY = "매물번호"

CHANGE_HEADERS = [
    "감지일", "감지시각", "이벤트", "매물번호", "단지명", "거래구분",
    "동", "층수", "면적", "이전가격", "현재가격", "이전중개업소",
    "현재중개업소", "이전등록일자", "현재등록일자", "이전집주인",
    "현재집주인", "변경필드", "비교기준파일",
]

INFO_COMPARE_FIELDS = [
    "단지명", "거래구분", "동", "층수", "면적", "중개업소", "중개업소ID",
    "등록일자", "방향", "특기사항", "제공", "집주인", "직거래", "사진 유무",
    "인증광고",
]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", value or "")
    return cleaned.strip("_") or "runner"


def rows_to_dicts(values: list[list[Any]]) -> tuple[list[str], list[dict[str, str]]]:
    """시트의 2차원 배열을 헤더 기반 딕셔너리 목록으로 변환한다."""
    if not values:
        return [], []
    headers = [_safe_text(v) for v in values[0]]
    rows: list[dict[str, str]] = []
    for source in values[1:]:
        padded = list(source) + [""] * max(0, len(headers) - len(source))
        row = {headers[i]: _safe_text(padded[i]) for i in range(len(headers))}
        if any(row.values()):
            rows.append(row)
    return headers, rows


def filter_apgujeong_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """압구정 대상 단지이면서 매물번호가 있는 개별 광고만 남긴다."""
    filtered: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        complex_name = _safe_text(row.get("단지명"))
        article_no = _safe_text(row.get(ARTICLE_KEY)).lstrip("'")
        if complex_name not in APGUJEONG_COMPLEXES or not article_no:
            continue
        if article_no in seen:
            continue
        normalized = dict(row)
        normalized[ARTICLE_KEY] = article_no
        filtered.append(normalized)
        seen.add(article_no)
    return filtered


def _parse_money_component(text: str) -> int | None:
    """한국식 금액 문자열 한 부분을 만원 단위 정수로 변환한다."""
    value = _safe_text(text).replace("만원", "").replace(" ", "")
    if not value:
        return None
    try:
        if "억" in value:
            eok, rest = value.split("억", 1)
            eok_num = int(float(eok.replace(",", "")))
            rest_num = int(rest.replace(",", "")) if rest.replace(",", "").isdigit() else 0
            return eok_num * 10000 + rest_num
        numeric = value.replace(",", "")
        return int(float(numeric)) if numeric else None
    except (TypeError, ValueError):
        return None


def parse_price(text: str) -> tuple[int | None, int | None]:
    """매매·전세는 (금액, None), 월세는 (보증금, 월세)로 반환한다."""
    raw = _safe_text(text)
    if "/" in raw:
        deposit, monthly = raw.split("/", 1)
        return _parse_money_component(deposit), _parse_money_component(monthly)
    return _parse_money_component(raw), None


def _normalize_number_text(value: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _safe_text(value)
    return str(int(number)) if number.is_integer() else str(number).rstrip("0").rstrip(".")


def normalize_area_display(value: str) -> str:
    """면적 표기의 공백·제곱미터 기호 차이만 정리한다."""
    text = _safe_text(value).replace("㎡", "m²").replace(" ", "")
    match = re.fullmatch(r"([0-9.]+)(?:/([0-9.]+))?m?²?", text)
    if not match:
        return text
    first = _normalize_number_text(match.group(1))
    second = _normalize_number_text(match.group(2)) if match.group(2) else ""
    return f"{first}/{second}m²" if second else f"{first}m²"


def normalize_dong_display(value: str) -> str:
    """101, 101동처럼 같은 동 표기만 통일한다."""
    text = re.sub(r"\s+", "", _safe_text(value))
    if not text:
        return ""
    if re.fullmatch(r"\d+", text):
        return f"{text}동"
    return text


def normalize_floor_display(value: str) -> str:
    """네이버에 보이는 층 표기를 유지하면서 철자 차이만 통일한다."""
    text = re.sub(r"\s+", "", _safe_text(value))
    if not text:
        return ""
    parts = text.split("/", 1)
    label = parts[0].replace("저층", "저").replace("중층", "중").replace("고층", "고")
    label = {"저": "저층", "중": "중층", "고": "고층"}.get(label, label)
    if label.isdigit():
        label = f"{int(label)}층"
    if len(parts) == 1:
        return label
    total = re.sub(r"층$", "", parts[1])
    total = f"{int(total)}층" if total.isdigit() else parts[1]
    return f"{label}/{total}"


def display_group_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    """실제 매물 추정 없이 광고에 표시된 조건만으로 묶는 키."""
    return (
        _safe_text(row.get("단지명")),
        _safe_text(row.get("거래구분")),
        normalize_area_display(row.get("면적", "")),
        normalize_dong_display(row.get("동", "")),
        normalize_floor_display(row.get("층수", "")),
    )


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _price_mode(values: list[int]) -> float | None:
    if not values:
        return None
    counts = Counter(values)
    highest = max(counts.values())
    # 반복 가격이 전혀 없으면 '최빈가'를 억지로 만들지 않고 중앙가를 사용한다.
    if highest == 1:
        return float(median(values))
    candidates = [price for price, count in counts.items() if count == highest]
    return float(min(candidates))


def _to_eok(value: float | int | None) -> float | str:
    if value is None:
        return ""
    return round(float(value) / 10000, 4)


def _group_metrics(rows: Iterable[dict[str, str]]) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        if _safe_text(row.get("거래구분")) != "매매":
            continue
        key = display_group_key(row)
        article_no = _safe_text(row.get(ARTICLE_KEY)).lstrip("'")
        if not article_no:
            continue
        group = groups.setdefault(key, {"rows": {}, "brokers": set(), "prices": [], "owners": 0})
        if article_no in group["rows"]:
            continue
        group["rows"][article_no] = row
        price, monthly = parse_price(row.get("가격", ""))
        if price is not None and monthly is None:
            group["prices"].append(price)
        broker_id = _safe_text(row.get("중개업소ID")).lstrip("'")
        broker_name = re.sub(r"\s+", "", _safe_text(row.get("중개업소"))).lower()
        broker_key = f"id:{broker_id}" if broker_id else (f"name:{broker_name}" if broker_name else "")
        if broker_key:
            group["brokers"].add(broker_key)
        if _safe_text(row.get("집주인")) == "집주인":
            group["owners"] += 1
    return groups


def build_ad_trend_rows(
    previous_rows: Iterable[dict[str, str]],
    current_rows: Iterable[dict[str, str]],
    now: datetime,
) -> list[list[Any]]:
    """표시조건군별 광고수와 가격분포를 전일과 비교한 일별 행을 만든다."""
    previous = _group_metrics(previous_rows)
    current = _group_metrics(current_rows)
    output: list[list[Any]] = []

    def sort_key(key: tuple[str, str, str, str, str]):
        complex_name, _, area, dong, floor = key
        zone = ZONE_BY_COMPLEX.get(complex_name, "기타")
        return (zone, complex_name, area, dong, floor)

    # 오늘 완전히 사라진 표시조건군도 광고수 0으로 하루 기록해
    # 표시조건군의 소멸을 누락하지 않는다.
    for key in sorted(set(current) | set(previous), key=sort_key):
        complex_name, trade_type, area, dong, floor = key
        group = current.get(key, {"rows": {}, "brokers": set(), "prices": [], "owners": 0})
        old = previous.get(key, {"rows": {}, "brokers": set(), "prices": [], "owners": 0})
        current_ids = set(group["rows"])
        previous_ids = set(old["rows"])
        common_ids = current_ids & previous_ids

        lowered = 0
        raised = 0
        for article_no in common_ids:
            old_price, old_monthly = parse_price(old["rows"][article_no].get("가격", ""))
            new_price, new_monthly = parse_price(group["rows"][article_no].get("가격", ""))
            if None in (old_price, new_price) or old_monthly is not None or new_monthly is not None:
                continue
            if new_price < old_price:
                lowered += 1
            elif new_price > old_price:
                raised += 1

        prices = group["prices"]
        old_prices = old["prices"]
        minimum = min(prices) if prices else None
        old_minimum = min(old_prices) if old_prices else None
        middle = float(median(prices)) if prices else None
        old_middle = float(median(old_prices)) if old_prices else None
        min_delta = minimum - old_minimum if minimum is not None and old_minimum is not None else None
        middle_delta = middle - old_middle if middle is not None and old_middle is not None else None
        direction = "▼" if middle_delta is not None and middle_delta < 0 else (
            "▲" if middle_delta is not None and middle_delta > 0 else "―"
        )
        group_id = "|".join(key)

        output.append([
            now.strftime("%Y-%m-%d"),
            ZONE_BY_COMPLEX.get(complex_name, "기타"),
            complex_name,
            trade_type,
            area,
            dong,
            floor,
            group_id,
            len(current_ids),
            len(previous_ids),
            len(current_ids) - len(previous_ids),
            len(group["brokers"]),
            len(current_ids - previous_ids),
            len(previous_ids - current_ids),
            lowered,
            raised,
            _to_eok(minimum),
            _to_eok(old_minimum),
            _to_eok(min_delta),
            _to_eok(_percentile(prices, 0.10)),
            _to_eok(middle),
            _to_eok(old_middle),
            _to_eok(middle_delta),
            direction,
            _to_eok(_price_mode(prices)),
            _to_eok(max(prices) if prices else None),
            group["owners"],
        ])
    return output


def classify_price_change(old_price: str, new_price: str) -> str:
    """가격 문자열 변경을 인하·인상·조건변경으로 분류한다."""
    if _safe_text(old_price) == _safe_text(new_price):
        return ""
    old_deposit, old_monthly = parse_price(old_price)
    new_deposit, new_monthly = parse_price(new_price)
    if old_deposit is None or new_deposit is None:
        return "가격변경"

    if old_monthly is None and new_monthly is None:
        return "가격인하" if new_deposit < old_deposit else "가격인상"

    # 월세는 보증금과 월세가 같은 방향으로 움직일 때만 인하·인상으로 분류한다.
    if old_monthly is None or new_monthly is None:
        return "가격조건변경"
    lower_or_equal = new_deposit <= old_deposit and new_monthly <= old_monthly
    higher_or_equal = new_deposit >= old_deposit and new_monthly >= old_monthly
    if lower_or_equal and (new_deposit < old_deposit or new_monthly < old_monthly):
        return "가격인하"
    if higher_or_equal and (new_deposit > old_deposit or new_monthly > old_monthly):
        return "가격인상"
    return "가격조건변경"


def _base_change_row(
    now: datetime,
    event: str,
    old: dict[str, str] | None,
    new: dict[str, str] | None,
    previous_file_name: str,
    changed_fields: str = "",
) -> dict[str, str]:
    source = new or old or {}
    old = old or {}
    new = new or {}
    return {
        "감지일": now.strftime("%Y-%m-%d"),
        "감지시각": now.strftime("%H:%M:%S"),
        "이벤트": event,
        "매물번호": _safe_text(source.get(ARTICLE_KEY)).lstrip("'"),
        "단지명": _safe_text(source.get("단지명")),
        "거래구분": _safe_text(source.get("거래구분")),
        "동": _safe_text(source.get("동")),
        "층수": _safe_text(source.get("층수")),
        "면적": _safe_text(source.get("면적")),
        "이전가격": _safe_text(old.get("가격")),
        "현재가격": _safe_text(new.get("가격")),
        "이전중개업소": _safe_text(old.get("중개업소")),
        "현재중개업소": _safe_text(new.get("중개업소")),
        "이전등록일자": _safe_text(old.get("등록일자")),
        "현재등록일자": _safe_text(new.get("등록일자")),
        "이전집주인": _safe_text(old.get("집주인")),
        "현재집주인": _safe_text(new.get("집주인")),
        "변경필드": changed_fields,
        "비교기준파일": previous_file_name,
    }


def build_change_events(
    previous_rows: Iterable[dict[str, str]],
    current_rows: Iterable[dict[str, str]],
    now: datetime,
    previous_file_name: str = "",
) -> list[dict[str, str]]:
    """매물번호 기준으로 신규·가격변경·정보변경·미노출 이벤트를 생성한다."""
    previous = {
        _safe_text(row.get(ARTICLE_KEY)).lstrip("'"): row
        for row in previous_rows
        if _safe_text(row.get(ARTICLE_KEY)).lstrip("'")
    }
    current = {
        _safe_text(row.get(ARTICLE_KEY)).lstrip("'"): row
        for row in current_rows
        if _safe_text(row.get(ARTICLE_KEY)).lstrip("'")
    }

    events: list[dict[str, str]] = []
    for article_no in sorted(current.keys() - previous.keys()):
        events.append(_base_change_row(now, "신규", None, current[article_no], previous_file_name))

    for article_no in sorted(previous.keys() - current.keys()):
        events.append(_base_change_row(now, "미노출", previous[article_no], None, previous_file_name))

    for article_no in sorted(previous.keys() & current.keys()):
        old = previous[article_no]
        new = current[article_no]
        price_event = classify_price_change(old.get("가격", ""), new.get("가격", ""))
        if price_event:
            events.append(_base_change_row(now, price_event, old, new, previous_file_name, "가격"))

        changed = [
            field for field in INFO_COMPARE_FIELDS
            if _safe_text(old.get(field)) != _safe_text(new.get(field))
        ]
        if changed:
            events.append(
                _base_change_row(
                    now,
                    "정보변경",
                    old,
                    new,
                    previous_file_name,
                    ", ".join(changed),
                )
            )
    return events


def _write_csv_gz(path: Path, headers: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _read_csv_gz(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _drive_service(credentials_file: str = "service_account.json"):
    # 지연 import로 순수 함수 테스트 시 Google 패키지가 없어도 모듈을 불러올 수 있게 한다.
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _sheets_service(credentials_file: str = "service_account.json"):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _ensure_trend_sheet(sheet_service, spreadsheet_id: str) -> int:
    metadata = sheet_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title,gridProperties))",
    ).execute()
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == TREND_SHEET_NAME:
            return int(props["sheetId"])

    response = sheet_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "addSheet": {
                "properties": {
                    "title": TREND_SHEET_NAME,
                    "gridProperties": {
                        "rowCount": 200000,
                        "columnCount": len(TREND_HEADERS),
                        "frozenRowCount": 1,
                    },
                }
            }
        }]},
    ).execute()
    sheet_id = int(response["replies"][0]["addSheet"]["properties"]["sheetId"])
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(TREND_HEADERS),
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.92, "green": 0.92, "blue": 0.92},
                        "textFormat": {"bold": True},
                        "horizontalAlignment": "CENTER",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 200000,
                        "startColumnIndex": 0,
                        "endColumnIndex": len(TREND_HEADERS),
                    }
                }
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 200000,
                    "startColumnIndex": 8,
                    "endColumnIndex": 16,
                },
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER", "numberFormat": {"type": "NUMBER", "pattern": "0"}}},
                "fields": "userEnteredFormat(horizontalAlignment,numberFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 200000,
                    "startColumnIndex": 16,
                    "endColumnIndex": 23,
                },
                "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "NUMBER", "pattern": "0.0"}}},
                "fields": "userEnteredFormat(horizontalAlignment,numberFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 200000,
                    "startColumnIndex": 24,
                    "endColumnIndex": 26,
                },
                "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT", "numberFormat": {"type": "NUMBER", "pattern": "0.0"}}},
                "fields": "userEnteredFormat(horizontalAlignment,numberFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 200000,
                    "startColumnIndex": 26,
                    "endColumnIndex": 27,
                },
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER", "numberFormat": {"type": "NUMBER", "pattern": "0"}}},
                "fields": "userEnteredFormat(horizontalAlignment,numberFormat)",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(TREND_HEADERS)},
                "properties": {"pixelSize": 92},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 7, "endIndex": 8},
                "properties": {"pixelSize": 250},
                "fields": "pixelSize",
            }
        },
    ]
    for symbol, color in (("▼", {"red": 0.85, "green": 0.05, "blue": 0.05}),
                          ("▲", {"red": 0.05, "green": 0.25, "blue": 0.85})):
        requests.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 200000,
                        "startColumnIndex": 23,
                        "endColumnIndex": 24,
                    }],
                    "booleanRule": {
                        "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": symbol}]},
                        "format": {"textFormat": {"bold": True, "foregroundColor": color}},
                    },
                },
                "index": 0,
            }
        })
    sheet_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()
    return sheet_id


def update_ad_trend_sheet(
    previous_rows: Iterable[dict[str, str]],
    current_rows: Iterable[dict[str, str]],
    now: datetime,
    *,
    credentials_file: str = "service_account.json",
) -> int:
    """오늘 표시조건군 행은 교체하고 과거 날짜 행은 그대로 누적한다."""
    spreadsheet_id = os.getenv(
        "SPREADSHEET_ID",
        "1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4",
    )
    sheet_service = _sheets_service(credentials_file)
    sheet_id = _ensure_trend_sheet(sheet_service, spreadsheet_id)

    header_result = sheet_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{TREND_SHEET_NAME}'!A1:AA1",
    ).execute()
    existing_header = header_result.get("values", [[]])[0]
    if existing_header and existing_header != TREND_HEADERS:
        raise ValueError(
            f"{TREND_SHEET_NAME} 헤더가 예상 구조와 다릅니다: {existing_header}"
        )
    if not existing_header:
        sheet_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{TREND_SHEET_NAME}'!A1:AA1",
            valueInputOption="RAW",
            body={"values": [TREND_HEADERS]},
        ).execute()

    today = now.strftime("%Y-%m-%d")
    date_result = sheet_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{TREND_SHEET_NAME}'!A2:A",
    ).execute()
    dates = [row[0] if row else "" for row in date_result.get("values", [])]
    matching_indexes = [index + 1 for index, value in enumerate(dates) if _safe_text(value) == today]
    if matching_indexes:
        intervals: list[tuple[int, int]] = []
        start = previous_index = matching_indexes[0]
        for index in matching_indexes[1:]:
            if index != previous_index + 1:
                intervals.append((start, previous_index + 1))
                start = index
            previous_index = index
        intervals.append((start, previous_index + 1))
        clear_ranges = [
            f"'{TREND_SHEET_NAME}'!A{start + 1}:AA{end}"
            for start, end in intervals
        ]
        sheet_service.spreadsheets().values().batchClear(
            spreadsheetId=spreadsheet_id,
            body={"ranges": clear_ranges},
        ).execute()

    trend_rows = build_ad_trend_rows(previous_rows, current_rows, now)
    if trend_rows:
        sheet_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{TREND_SHEET_NAME}'!A:AA",
            valueInputOption="RAW",
            insertDataOption="OVERWRITE",
            body={"values": trend_rows},
        ).execute()
    return len(trend_rows)


def _escape_drive_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_file(drive, folder_id: str, name: str) -> dict[str, Any] | None:
    query = (
        f"name = '{_escape_drive_query(name)}' and "
        f"'{folder_id}' in parents and trashed = false"
    )
    response = drive.files().list(
        q=query,
        fields="files(id,name,createdTime,modifiedTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="drive",
        driveId=SHARED_DRIVE_ID,
        pageSize=10,
    ).execute()
    files = response.get("files", [])
    return files[0] if files else None


def _upload_file(
    drive,
    local_path: Path,
    folder_id: str,
    remote_name: str,
    *,
    replace: bool,
    mime_type: str,
) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
    existing = _find_file(drive, folder_id, remote_name) if replace else None
    if existing:
        return drive.files().update(
            fileId=existing["id"],
            media_body=media,
            supportsAllDrives=True,
            fields="id,name,modifiedTime",
        ).execute()
    return drive.files().create(
        body={"name": remote_name, "parents": [folder_id]},
        media_body=media,
        supportsAllDrives=True,
        fields="id,name,createdTime",
    ).execute()


def _list_previous_official_files(drive, today: str) -> list[dict[str, Any]]:
    query = (
        f"'{OFFICIAL_FOLDER_ID}' in parents and trashed = false and "
        "name contains 'apgujeong_official_'"
    )
    response = drive.files().list(
        q=query,
        fields="files(id,name,createdTime,modifiedTime)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="drive",
        driveId=SHARED_DRIVE_ID,
        pageSize=1000,
    ).execute()
    candidates = []
    for item in response.get("files", []):
        match = re.fullmatch(r"apgujeong_official_(\d{4}-\d{2}-\d{2})\.csv\.gz", item.get("name", ""))
        if match and match.group(1) < today:
            item = dict(item)
            item["snapshot_date"] = match.group(1)
            candidates.append(item)
    return sorted(candidates, key=lambda item: item["snapshot_date"], reverse=True)


def _download_file(drive, file_id: str, destination: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with destination.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def _with_metadata(
    rows: Iterable[dict[str, str]],
    now: datetime,
    runner: str,
    run_type: str,
) -> list[dict[str, str]]:
    result = []
    for row in rows:
        enriched = {
            "수집일": now.strftime("%Y-%m-%d"),
            "수집시각": now.strftime("%H:%M:%S"),
            "러너": runner,
            "실행구분": run_type,
        }
        enriched.update(row)
        result.append(enriched)
    return result


def archive_sheet_values(
    values: list[list[Any]],
    *,
    grouped_count: int | None = None,
    now: datetime | None = None,
    credentials_file: str = "service_account.json",
) -> dict[str, Any]:
    """중복처리 전 시트 값을 공유 드라이브에 보관하고 요약을 반환한다."""
    now = now or datetime.now(KST)
    headers, rows = rows_to_dicts(values)
    current_rows = filter_apgujeong_rows(rows)
    if not headers or ARTICLE_KEY not in headers:
        raise ValueError("원본 헤더에서 매물번호 열을 찾지 못했습니다.")
    if not current_rows:
        raise ValueError("압구정 개별 광고 원본이 0건입니다.")

    runner = _sanitize_name(os.getenv("RUNNER_NAME") or os.getenv("COMPUTERNAME") or "runner")
    event_name = os.getenv("GITHUB_EVENT_NAME", "local")
    run_type = "예약" if event_name == "schedule" else ("수동" if event_name == "workflow_dispatch" else "로컬")
    run_id = _sanitize_name(os.getenv("GITHUB_RUN_ID", "local"))
    stamp = now.strftime("%Y-%m-%d_%H%M%S")
    today = now.strftime("%Y-%m-%d")

    drive = _drive_service(credentials_file)
    previous_rows: list[dict[str, str]] = []
    previous_name = ""

    with tempfile.TemporaryDirectory(prefix="naver_archive_") as tmp:
        tmp_dir = Path(tmp)
        previous_files = _list_previous_official_files(drive, today)
        if previous_files:
            previous_file = previous_files[0]
            previous_name = previous_file["name"]
            previous_path = tmp_dir / previous_name
            _download_file(drive, previous_file["id"], previous_path)
            previous_rows = _read_csv_gz(previous_path)

        metadata_rows = _with_metadata(current_rows, now, runner, run_type)
        archive_headers = META_HEADERS + headers
        raw_name = f"apgujeong_raw_{stamp}_{runner}_{run_id}.csv.gz"
        official_name = f"apgujeong_official_{today}.csv.gz"
        current_name = "apgujeong_current.csv.gz"
        changes_name = f"apgujeong_changes_{today}.csv.gz"
        log_name = f"run_{stamp}_{runner}_{run_id}.json"

        raw_path = tmp_dir / raw_name
        official_path = tmp_dir / official_name
        changes_path = tmp_dir / changes_name
        log_path = tmp_dir / log_name

        _write_csv_gz(raw_path, archive_headers, metadata_rows)
        _write_csv_gz(official_path, archive_headers, metadata_rows)

        change_rows = build_change_events(previous_rows, current_rows, now, previous_name)
        _write_csv_gz(changes_path, CHANGE_HEADERS, change_rows)

        raw_upload = _upload_file(
            drive, raw_path, RAW_FOLDER_ID, raw_name,
            replace=False, mime_type="application/gzip",
        )
        official_upload = _upload_file(
            drive, official_path, OFFICIAL_FOLDER_ID, official_name,
            replace=True, mime_type="application/gzip",
        )
        current_upload = _upload_file(
            drive, official_path, OFFICIAL_FOLDER_ID, current_name,
            replace=True, mime_type="application/gzip",
        )
        change_upload = _upload_file(
            drive, changes_path, CHANGE_FOLDER_ID, changes_name,
            replace=True, mime_type="application/gzip",
        )

        trend_count = update_ad_trend_sheet(
            previous_rows,
            current_rows,
            now,
            credentials_file=credentials_file,
        )

        event_counts: dict[str, int] = {}
        for event in change_rows:
            name = event.get("이벤트", "")
            event_counts[name] = event_counts.get(name, 0) + 1

        summary = {
            "archived_at": now.isoformat(),
            "runner": runner,
            "run_type": run_type,
            "raw_count": len(current_rows),
            "grouped_count": grouped_count,
            "previous_official": previous_name or None,
            "change_count": len(change_rows),
            "trend_group_count": trend_count,
            "event_counts": event_counts,
            "files": {
                "raw": {"id": raw_upload.get("id"), "name": raw_name},
                "official": {"id": official_upload.get("id"), "name": official_name},
                "current": {"id": current_upload.get("id"), "name": current_name},
                "changes": {"id": change_upload.get("id"), "name": changes_name},
            },
        }
        log_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        log_upload = _upload_file(
            drive, log_path, LOG_FOLDER_ID, log_name,
            replace=False, mime_type="application/json",
        )
        summary["files"]["log"] = {"id": log_upload.get("id"), "name": log_name}
        return summary
