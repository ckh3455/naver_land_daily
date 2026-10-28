"""listing_match 단위 테스트 (python -m unittest test_listing_match)."""

import unittest

from listing_match import (
    STATUS_INACTIVE, STATUS_PRICE_DIFF, STATUS_REGISTERED, STATUS_UNREGISTERED,
    build_broker_alias, build_results, floors_compatible, parse_naver_floor, parse_source_floor,
    parse_source_price_man,
)


def ad(dong, floor, price, broker, no, area="111/105m²", complex_name="미성1차", date="2026.10.20"):
    return {
        "단지명": complex_name, "거래구분": "매매", "동": dong, "층수": floor,
        "면적": area, "가격": price, "중개업소": broker, "중개업소ID": broker,
        "등록일자": date, "집주인": "", "매물번호": no,
    }


def src(dong, unit, band, price, status="활성", complex_name="미성1차", pyeong="34평"):
    return {"단지명": complex_name, "평형": pyeong, "동": dong, "층/호": unit,
            "층수": band, "가격": price, "상태": status}


class ParsingTests(unittest.TestCase):
    def test_floor_parsing(self):
        self.assertEqual(parse_naver_floor("4/14").exact, 4)
        self.assertEqual(parse_naver_floor("저/14").band, "저")
        self.assertEqual(parse_source_floor("405", "저").exact, 4)
        self.assertEqual(parse_source_floor("12층", "").exact, 12)
        self.assertEqual(parse_source_floor("고층", "고").band, "고")

    def test_floor_compatibility(self):
        self.assertTrue(floors_compatible(parse_naver_floor("4/14"), parse_source_floor("405", "저")))
        self.assertFalse(floors_compatible(parse_naver_floor("4/14"), parse_source_floor("705", "중")))
        self.assertTrue(floors_compatible(parse_naver_floor("저/14"), parse_source_floor("405", "저")))
        self.assertFalse(floors_compatible(parse_naver_floor("고/14"), parse_source_floor("2층", "저")))

    def test_source_price(self):
        self.assertEqual(parse_source_price_man("46"), 460000)
        self.assertEqual(parse_source_price_man("65/66"), 650000)
        self.assertEqual(parse_source_price_man("57~8"), 570000)
        self.assertEqual(parse_source_price_man("50억"), 500000)
        self.assertEqual(parse_source_price_man("41.2"), 412000)


class MatchingTests(unittest.TestCase):
    def test_single_broker_is_not_genuine(self):
        rows = [ad("3동", "4/14", "46억", "A", "1")]
        self.assertEqual(build_results(rows, []), [])

    def test_band_and_exact_ads_merge_into_one_genuine(self):
        rows = [
            ad("3동", "4/14", "46억", "A", "1"),
            ad("3동", "저/14", "46억", "B", "2"),
            ad("3동", "저/14", "46억 5,000", "C", "3"),
        ]
        result = build_results(rows, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], STATUS_UNREGISTERED)
        self.assertEqual(result[0][8], 3)  # 업소수

    def test_different_exact_floors_do_not_merge(self):
        rows = [
            ad("3동", "4/14", "46억", "A", "1"),
            ad("3동", "7/14", "46억", "B", "2"),
        ]
        self.assertEqual(build_results(rows, []), [])

    def test_registered_by_exact_unit(self):
        rows = [ad("3동", "4/14", "45억", "A", "1"), ad("3동", "4/14", "45억", "B", "2")]
        result = build_results(rows, [src("3", "405", "저", "46")])
        self.assertEqual(result[0][0], STATUS_REGISTERED)

    def test_band_with_far_price_is_price_diff(self):
        rows = [ad("3동", "저/14", "40억", "A", "1"), ad("3동", "저/14", "40억", "B", "2")]
        result = build_results(rows, [src("3", "405", "저", "46")])
        self.assertEqual(result[0][0], STATUS_PRICE_DIFF)

    def test_only_closed_listing_is_inactive(self):
        rows = [ad("3동", "2/14", "50억", "A", "1"), ad("3동", "2/14", "50억", "B", "2")]
        result = build_results(rows, [src("3", "207", "저", "50", status="거래완료")])
        self.assertEqual(result[0][0], STATUS_INACTIVE)

    def test_band_ads_stay_separate_when_two_exact_floors_fit(self):
        # 고층 45억 광고(4곳)는 12층 46억·13층 45억 중 어느 집인지 알 수 없다 → 따로 둔다
        rows = [
            ad("3동", "13/14", "45억", "A", "1"),
            ad("3동", "12/14", "46억", "B", "2"),
            ad("3동", "고/14", "45억", "C", "3"),
            ad("3동", "고/14", "45억", "D", "4"),
            ad("3동", "고/14", "45억", "E", "5"),
            ad("3동", "고/14", "45억", "F", "6"),
        ]
        result = build_results(rows, [])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][5], "고층(4)/14")
        self.assertEqual(result[0][8], 4)

    def test_floor_breakdown_label(self):
        rows = [ad("3동", "4/14", "46억", "A", "1"), ad("3동", "저/14", "46억", "B", "2"),
                ad("3동", "저/14", "46억", "C", "3")]
        self.assertEqual(build_results(rows, [])[0][5], "4층(1)+저층(2)/14")

    def test_broker_alias_maps_and_counts_once(self):
        alias = build_broker_alias([
            {"ID": "A1", "중개업소명": "(주)압구정본점부동산중개법인", "실제상호": "본점"},
            {"ID": "A2", "중개업소명": "주식회사압구정본점부동산중개법인", "실제상호": "본점"},
        ])
        rows = [ad("3동", "4/14", "46억", "A1", "1"), ad("3동", "4/14", "46억", "A2", "2")]
        self.assertEqual(build_results(rows, [], broker_alias=alias), [])  # 같은 업소 → 1곳
        rows.append(ad("3동", "4/14", "46억", "X", "3"))
        result = build_results(rows, [], broker_alias=alias)
        self.assertEqual(result[0][8], 2)
        self.assertIn("본점", result[0][13])

    def test_single_type_dong_adopts_source_pyeong(self):
        # 96㎡ → 환산 29평이지만 매물장 205동은 32평 하나뿐 → 32평으로 보고 등록 판정
        rows = [
            ad("205동", "12/15", "54억", "A", "1", area="96/82m²", complex_name="현대10,13,14차"),
            ad("205동", "12/15", "54억", "B", "2", area="96/82m²", complex_name="현대10,13,14차"),
        ]
        result = build_results(rows, [src("205", "1203", "고", "55", complex_name="현대14차", pyeong="32평")])
        self.assertEqual(result[0][0], STATUS_REGISTERED)
        self.assertEqual(result[0][3], "32평")

    def test_complex_alias(self):
        rows = [
            ad("206동", "10/15", "55억", "A", "1", area="106/82m²", complex_name="현대10,13,14차"),
            ad("206동", "10/15", "55억", "B", "2", area="106/82m²", complex_name="현대10,13,14차"),
        ]
        result = build_results(rows, [src("206", "1004", "고", "55", complex_name="현대14차", pyeong="32평")])
        self.assertEqual(result[0][0], STATUS_REGISTERED)


if __name__ == "__main__":
    unittest.main()
