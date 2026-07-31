#!/usr/bin/env python3
"""기존 네이버 크롤러에 공유 드라이브 원본 보관 기능을 결합한 실행 진입점."""

from __future__ import annotations

import asyncio
import json
import traceback
from pathlib import Path

import main_github
from drive_archive import archive_sheet_values

SOURCE_HEADERS = [
    "단지명", "거래구분", "동", "층수", "면적", "가격",
    "가격변동", "중복업소", "중개업소", "중개업소ID",
    "등록일자", "방향", "특기사항", "제공", "집주인", "직거래",
    "사진 유무", "경도", "위도", "매물번호", "인증광고",
]

_ARCHIVE_SUMMARY: dict = {}
_CAPTURED_ROWS: list[list] = []
_ORIGINAL_PROCESS = main_github.process_real_estate_data
_ORIGINAL_FORMAT = main_github.format_property_data


def _format_and_capture(prop):
    """Google Sheets에 기록되기 전 개별 광고 행을 그대로 보관한다."""
    row = _ORIGINAL_FORMAT(prop)
    if row and len(row) == len(SOURCE_HEADERS):
        _CAPTURED_ROWS.append(list(row))
    return row


def _process_with_archive(spreadsheet, worksheet, sheet_service, spreadsheet_id):
    """기존 중복처리를 완료한 뒤 캡처한 개별 광고를 Drive에 보관한다."""
    global _ARCHIVE_SUMMARY

    # 기존 중복처리·서식·업소 등록 로직을 먼저 정상 완료한다.
    result = _ORIGINAL_PROCESS(spreadsheet, worksheet, sheet_service, spreadsheet_id)

    try:
        grouped_values = worksheet.get_all_values()
        grouped_count = max(0, len(grouped_values) - 1)

        # 정상적으로 캡처됐다면 시트의 날짜·숫자 자동변환을 거치지 않은 원본을 사용한다.
        # 예외적으로 캡처가 비어 있으면 현재 시트 값을 보조 수단으로 사용한다.
        raw_values = [SOURCE_HEADERS] + list(_CAPTURED_ROWS)
        if not _CAPTURED_ROWS:
            main_github.debug_log(
                "개별 광고 직접 캡처가 비어 있어 시트 원본을 대신 읽습니다.",
                "WARNING",
            )
            raw_values = worksheet.get_all_values()

        _ARCHIVE_SUMMARY = archive_sheet_values(
            raw_values,
            grouped_count=grouped_count,
            credentials_file="service_account.json",
        )
        main_github.debug_log(
            "공유 드라이브 원본 보관 완료: "
            f"개별 {_ARCHIVE_SUMMARY.get('raw_count', 0)}건, "
            f"변동 {_ARCHIVE_SUMMARY.get('change_count', 0)}건",
            "SUCCESS",
        )
    except Exception as exc:
        # 시트 갱신 결과는 보존하되, Actions에서는 보관 실패를 명확히 오류로 표시한다.
        _ARCHIVE_SUMMARY = {
            "status": "error",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        main_github.debug_log(f"공유 드라이브 원본 보관 실패: {exc}", "ERROR")
        main_github.debug_log(_ARCHIVE_SUMMARY["traceback"], "DEBUG")

    return result


def _append_archive_summary() -> None:
    Path("drive_archive_summary.json").write_text(
        json.dumps(_ARCHIVE_SUMMARY, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result_path = Path("crawling_results.json")
    if not result_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["drive_archive"] = _ARCHIVE_SUMMARY
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        main_github.debug_log(f"실행 결과에 Drive 요약 추가 실패: {exc}", "WARNING")


def main() -> None:
    main_github.format_property_data = _format_and_capture
    main_github.process_real_estate_data = _process_with_archive
    try:
        asyncio.run(main_github.main())
    finally:
        _append_archive_summary()

    if _ARCHIVE_SUMMARY.get("status") == "error":
        raise RuntimeError(
            "네이버 매물 시트 갱신은 완료됐지만 공유 드라이브 원본 보관에 실패했습니다: "
            + _ARCHIVE_SUMMARY.get("error", "알 수 없는 오류")
        )


if __name__ == "__main__":
    main()
