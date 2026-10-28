#!/usr/bin/env python3
"""진성 매물(2곳 이상 광고) 중 매물장에 없는 것을 '미등록 진성매물' 탭에 기록한다.

입력
- 공유 드라이브의 최신 개별 광고 원본(apgujeong_current.csv.gz)
  또는 직접 넘긴 행(파이프라인 통합 시)
- 원부동산 매물장 '매매물건 목록'
출력
- 메인 시트의 '미등록 진성매물' 탭(판정별 색, 미등록이 맨 위)
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import drive_archive as da
from listing_match import (
    RESULT_HEADERS, STATUS_INACTIVE, STATUS_PRICE_DIFF, STATUS_REGISTERED,
    STATUS_UNREGISTERED, build_broker_alias, build_results,
)

REPORT_SHEET_NAME = "미등록 진성매물"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4")

STATUS_COLORS = {
    STATUS_UNREGISTERED: {"red": 0.988, "green": 0.800, "blue": 0.800},
    STATUS_PRICE_DIFF: {"red": 1.0, "green": 0.902, "blue": 0.749},
    STATUS_INACTIVE: {"red": 0.929, "green": 0.929, "blue": 0.929},
    STATUS_REGISTERED: {"red": 1.0, "green": 1.0, "blue": 1.0},
}
HEADER_COLOR = {"red": 0.2, "green": 0.2, "blue": 0.2}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}


def load_current_rows(credentials_file: str = "service_account.json") -> tuple[list[dict[str, str]], str]:
    drive = da._drive_service(credentials_file)
    item = da._find_file(drive, da.OFFICIAL_FOLDER_ID, "apgujeong_current.csv.gz")
    if not item:
        raise RuntimeError("공유 드라이브에서 apgujeong_current.csv.gz를 찾지 못했습니다.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "current.csv.gz"
        da._download_file(drive, item["id"], path)
        rows = da._read_csv_gz(path)
    collected = rows[0].get("수집일", "") + " " + rows[0].get("수집시각", "") if rows else ""
    return rows, collected.strip()


def load_source_rows(credentials_file: str = "service_account.json") -> list[dict[str, str]]:
    service = da._sheets_service(credentials_file)
    values = service.spreadsheets().values().get(
        spreadsheetId=da.SOURCE_LISTING_SPREADSHEET_ID,
        range="'매매물건 목록'!A:P",
    ).execute().get("values", [])
    _, rows = da.rows_to_dicts(values)
    return rows


BROKER_ALIAS_SHEET = "압구정 중개업소 2"


def load_broker_alias(credentials_file: str = "service_account.json",
                      spreadsheet_id: str = SPREADSHEET_ID) -> dict[str, dict[str, str]]:
    """'압구정 중개업소 2' 탭의 ID·중개업소명 → 실제상호 매핑."""
    service = da._sheets_service(credentials_file)
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{BROKER_ALIAS_SHEET}'!A:G",
    ).execute().get("values", [])
    _, rows = da.rows_to_dicts(values)
    return build_broker_alias(rows)


def _ensure_sheet(service, spreadsheet_id: str, title: str, cols: int) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == title:
            return sheet["properties"]["sheetId"]
    reply = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {
            "title": title, "index": 1,
            "gridProperties": {"rowCount": 1000, "columnCount": cols, "frozenRowCount": 2},
        }}}]},
    ).execute()
    return reply["replies"][0]["addSheet"]["properties"]["sheetId"]


def write_report(results: list[list[Any]], collected: str,
                 credentials_file: str = "service_account.json",
                 spreadsheet_id: str = SPREADSHEET_ID) -> dict[str, Any]:
    service = da._sheets_service(credentials_file)
    cols = len(RESULT_HEADERS)
    sheet_id = _ensure_sheet(service, spreadsheet_id, REPORT_SHEET_NAME, cols)
    counts = Counter(row[0] for row in results)
    now = datetime.now(da.KST).strftime("%Y-%m-%d %H:%M")
    summary = (
        f"갱신 {now} · 광고수집 {collected} · 진성(업소 2곳+) {len(results)}건 · "
        f"{STATUS_UNREGISTERED} {counts.get(STATUS_UNREGISTERED, 0)} · "
        f"{STATUS_PRICE_DIFF} {counts.get(STATUS_PRICE_DIFF, 0)} · "
        f"{STATUS_INACTIVE} {counts.get(STATUS_INACTIVE, 0)} · "
        f"{STATUS_REGISTERED} {counts.get(STATUS_REGISTERED, 0)}"
    )
    values = [[summary] + [""] * (cols - 1), RESULT_HEADERS] + [
        [("" if v is None else v) for v in row] for row in results
    ]
    total_rows = len(values)

    requests: list[dict[str, Any]] = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {
                "rowCount": max(total_rows + 50, 200), "columnCount": cols, "frozenRowCount": 2}},
            "fields": "gridProperties(rowCount,columnCount,frozenRowCount)"}},
        {"clearBasicFilter": {"sheetId": sheet_id}},
        {"repeatCell": {"range": {"sheetId": sheet_id},
                        "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}},
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{REPORT_SHEET_NAME}'!A:Z").execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{REPORT_SHEET_NAME}'!A1",
        valueInputOption="RAW", body={"values": values}).execute()

    fmt: list[dict[str, Any]] = [
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 11}}},
            "fields": "userEnteredFormat.textFormat"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2},
            "cell": {"userEnteredFormat": {
                "backgroundColor": HEADER_COLOR,
                "textFormat": {"bold": True, "foregroundColor": WHITE},
                "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
        {"setBasicFilter": {"filter": {"range": {
            "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": total_rows,
            "startColumnIndex": 0, "endColumnIndex": cols}}}},
    ]
    # 같은 판정이 이어지는 구간별로 배경색을 칠한다(요청 수 최소화).
    start = 2
    for index in range(2, total_rows + 1):
        current = values[index][0] if index < total_rows else None
        if index == total_rows or current != values[start][0]:
            status = values[start][0]
            color = STATUS_COLORS.get(status, WHITE)
            cell: dict[str, Any] = {"backgroundColor": color}
            fields = "userEnteredFormat.backgroundColor"
            if status == STATUS_UNREGISTERED:
                cell["textFormat"] = {"bold": True}
                fields = "userEnteredFormat(backgroundColor,textFormat)"
            fmt.append({"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": start, "endRowIndex": index},
                "cell": {"userEnteredFormat": cell}, "fields": fields}})
            start = index
    widths = [90, 50, 120, 50, 60, 120, 70, 90, 50, 50, 45, 85, 85, 240, 260, 90, 200]
    for i, width in enumerate(widths[:cols]):
        fmt.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize"}})
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": fmt}).execute()
    return {"summary": summary, "counts": dict(counts), "rows": len(results)}


def run_report(rows: list[dict[str, str]], collected: str,
               credentials_file: str = "service_account.json") -> dict[str, Any]:
    """개별 광고 행으로 리포트를 만들어 탭에 쓰고 요약을 반환한다(크롤링 파이프라인용)."""
    source_rows = load_source_rows(credentials_file)
    alias = load_broker_alias(credentials_file)
    results = build_results(rows, source_rows, broker_alias=alias)
    info = write_report(results, collected, credentials_file)
    info.update({"ad_rows": len(rows), "source_rows": len(source_rows),
                 "alias_ids": len(alias["byId"]), "alias_names": len(alias["byName"])})
    Path("genuine_report_summary.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def main() -> None:
    """수동 실행: 공유 드라이브의 최신 원본으로 리포트를 다시 만든다."""
    rows, collected = load_current_rows()
    print(json.dumps(run_report(rows, collected), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
