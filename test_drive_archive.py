from datetime import datetime
from zoneinfo import ZoneInfo

from drive_archive import (
    build_ad_trend_rows,
    build_change_events,
    classify_price_change,
    display_group_key,
    filter_apgujeong_rows,
    normalize_floor_display,
    parse_price,
    rows_to_dicts,
)


def test_price_parsing_and_classification():
    assert parse_price("45억 9,000") == (459000, None)
    assert parse_price("10억/600만원") == (100000, 600)
    assert classify_price_change("65억", "63억") == "가격인하"
    assert classify_price_change("10억/600만원", "8억/550만원") == "가격인하"
    assert classify_price_change("10억/600만원", "8억/700만원") == "가격조건변경"


def test_filter_and_changes():
    values = [
        ["단지명", "거래구분", "가격", "중개업소", "매물번호"],
        ["현대1,2차", "매매", "65억", "A", "100"],
        ["트리마제", "매매", "40억", "B", "200"],
        ["현대1,2차", "매매", "65억", "A", "100"],
    ]
    _, rows = rows_to_dicts(values)
    filtered = filter_apgujeong_rows(rows)
    assert len(filtered) == 1
    assert filtered[0]["매물번호"] == "100"

    previous = [{
        "단지명": "현대1,2차", "거래구분": "매매", "가격": "65억",
        "중개업소": "A", "매물번호": "100",
    }]
    current = [
        {
            "단지명": "현대1,2차", "거래구분": "매매", "가격": "63억",
            "중개업소": "A", "매물번호": "100",
        },
        {
            "단지명": "현대1,2차", "거래구분": "매매", "가격": "70억",
            "중개업소": "C", "매물번호": "300",
        },
    ]
    events = build_change_events(
        previous,
        current,
        datetime(2026, 8, 1, tzinfo=ZoneInfo("Asia/Seoul")),
        "prev.csv.gz",
    )
    assert sorted(event["이벤트"] for event in events) == ["가격인하", "신규"]


def test_display_group_trends_do_not_merge_exact_and_middle_floors():
    assert normalize_floor_display("중/14") == "중층/14층"
    assert normalize_floor_display("중층 / 14층") == "중층/14층"
    assert normalize_floor_display("9/14") == "9층/14층"

    previous = [
        {
            "단지명": "신현대", "거래구분": "매매", "면적": "115/108m²",
            "동": "101동", "층수": "중/14", "가격": "70억",
            "중개업소": "A", "중개업소ID": "a", "매물번호": "100",
        },
        {
            "단지명": "신현대", "거래구분": "매매", "면적": "115/108m²",
            "동": "101동", "층수": "9/14", "가격": "69억",
            "중개업소": "B", "중개업소ID": "b", "매물번호": "200",
        },
    ]
    current = [
        {
            "단지명": "신현대", "거래구분": "매매", "면적": "115 / 108㎡",
            "동": "101", "층수": "중층/14층", "가격": "68억",
            "중개업소": "A", "중개업소ID": "a", "매물번호": "100",
            "집주인": "집주인",
        },
        {
            "단지명": "신현대", "거래구분": "매매", "면적": "115/108m²",
            "동": "101동", "층수": "중/14", "가격": "67억",
            "중개업소": "C", "중개업소ID": "c", "매물번호": "300",
        },
        {
            "단지명": "신현대", "거래구분": "매매", "면적": "115/108m²",
            "동": "101동", "층수": "9/14", "가격": "69억",
            "중개업소": "B", "중개업소ID": "b", "매물번호": "200",
        },
    ]

    middle_key = display_group_key(current[0])
    exact_key = display_group_key(current[2])
    assert middle_key != exact_key

    rows = build_ad_trend_rows(
        previous,
        current,
        datetime(2026, 8, 2, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert len(rows) == 2
    middle = next(row for row in rows if row[6] == "중층/14층")
    exact = next(row for row in rows if row[6] == "9층/14층")

    assert middle[8] == 2       # 현재 광고수
    assert middle[9] == 1       # 전일 광고수
    assert middle[12] == 1      # 신규광고
    assert middle[14] == 1      # 같은 광고번호의 가격인하
    assert middle[20] == 67.5   # 중앙가
    assert middle[22] == -2.5   # 전일 중앙가 대비
    assert middle[23] == "▼"
    assert middle[26] == 1      # 집주인광고

    assert exact[8] == 1
    assert exact[20] == 69.0
    assert exact[23] == "―"


def test_disappeared_display_group_is_recorded_once_with_zero_ads():
    previous = [{
        "단지명": "미성2차", "거래구분": "매매", "면적": "105/74m²",
        "동": "24동", "층수": "저/17", "가격": "44억",
        "중개업소": "A", "중개업소ID": "a", "매물번호": "400",
    }]
    rows = build_ad_trend_rows(
        previous,
        [],
        datetime(2026, 8, 2, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert len(rows) == 1
    assert rows[0][8] == 0
    assert rows[0][9] == 1
    assert rows[0][10] == -1
    assert rows[0][13] == 1
    assert rows[0][20] == ""
