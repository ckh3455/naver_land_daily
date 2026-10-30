from datetime import datetime

import main_github as m


def test_complex_info_row_formats_date_parking_and_heat():
    row = m.format_complex_info_row(
        {"id": "498", "name": "한양4차"},
        {
            "totalHouseHoldCount": 286,
            "totalDongCount": 4,
            "useApproveYmd": "19781228",
            "parkingPossibleCount": 143,
            "parkingCountByHousehold": 0.5,
            "heatMethodTypeCode": "HT002",
        },
    )
    assert row[:8] == ["한양4차", "498", "286", "4", "1978.12.28", "143", "0.5", "중앙난방"]


def test_complex_info_row_skips_empty_overview():
    assert m.format_complex_info_row({"id": "1", "name": "x"}, {}) is None


def test_flatten_finds_nested_parking():
    overview = dict(m._flatten_scalars({"a": {"b": [{"parkingPossibleCount": 7}]}}))
    assert overview["parkingPossibleCount"] == 7


def test_complex_info_collected_only_on_sunday(monkeypatch):
    monkeypatch.delenv("COLLECT_COMPLEX_INFO", raising=False)
    assert m.should_collect_complex_info(datetime(2026, 11, 1))  # 일요일
    assert not m.should_collect_complex_info(datetime(2026, 11, 2))
    monkeypatch.setenv("COLLECT_COMPLEX_INFO", "1")
    assert m.should_collect_complex_info(datetime(2026, 11, 2))


def test_filter_detail_headers_drops_unsendable():
    headers = m._filter_detail_headers(
        {"authorization": "Bearer x", "host": "a", ":path": "/", "cookie": "c"}
    )
    assert headers == {"authorization": "Bearer x"}
