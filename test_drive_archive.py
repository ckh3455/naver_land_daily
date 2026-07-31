from datetime import datetime
from zoneinfo import ZoneInfo

from drive_archive import (
    build_change_events,
    classify_price_change,
    filter_apgujeong_rows,
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
