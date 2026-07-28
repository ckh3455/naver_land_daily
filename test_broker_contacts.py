import importlib.util
import sys
import types
import unittest
from pathlib import Path


def _install_dependency_stubs():
    playwright = types.ModuleType("playwright")
    playwright_async = types.ModuleType("playwright.async_api")
    playwright_async.async_playwright = object()
    playwright.async_api = playwright_async
    sys.modules["playwright"] = playwright
    sys.modules["playwright.async_api"] = playwright_async

    gspread = types.ModuleType("gspread")
    gspread.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
    sys.modules["gspread"] = gspread

    google = types.ModuleType("google")
    google_oauth2 = types.ModuleType("google.oauth2")
    google_service_account = types.ModuleType("google.oauth2.service_account")
    google_oauth2.service_account = google_service_account
    google.oauth2 = google_oauth2
    sys.modules["google"] = google
    sys.modules["google.oauth2"] = google_oauth2
    sys.modules["google.oauth2.service_account"] = google_service_account

    googleapiclient = types.ModuleType("googleapiclient")
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = object()
    errors = types.ModuleType("googleapiclient.errors")
    errors.HttpError = type("HttpError", (Exception,), {})
    sys.modules["googleapiclient"] = googleapiclient
    sys.modules["googleapiclient.discovery"] = discovery
    sys.modules["googleapiclient.errors"] = errors


_install_dependency_stubs()
spec = importlib.util.spec_from_file_location(
    "main_github",
    Path(__file__).with_name("main_github.py")
)
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


class _ValuesApi:
    def __init__(self, initial):
        self.initial = initial
        self.updated = None

    def get(self, **_kwargs):
        return self

    def update(self, **kwargs):
        self.updated = kwargs
        return self

    def clear(self, **_kwargs):
        return self

    def execute(self):
        if self.updated is not None:
            return {"updatedRows": len(self.updated["body"]["values"])}
        return {"values": self.initial}


class _SheetsApi:
    def __init__(self, values_api):
        self.values_api = values_api

    def values(self):
        return self.values_api

    def get(self, **_kwargs):
        return self

    def batchUpdate(self, **_kwargs):
        return self

    def execute(self):
        return {
            "sheets": [{
                "properties": {
                    "sheetId": 123,
                    "title": main.ALIAS_SHEET_NAME
                }
            }]
        }


class _FakeService:
    def __init__(self, initial):
        self.values_api = _ValuesApi(initial)
        self.sheets_api = _SheetsApi(self.values_api)

    def spreadsheets(self):
        return self.sheets_api


class BrokerContactTests(unittest.TestCase):
    def test_extracts_realtor_contact_fields(self):
        payload = {
            "articleRealtor": {
                "realtorId": "office-1",
                "realtorName": "압구정원공인중개사",
                "address": "서울특별시 강남구 압구정로 1",
                "representativeTelNo": "02-111-2222",
                "cellPhoneNo": "010-3333-4444",
            }
        }
        detail = main._extract_broker_detail(payload)
        self.assertEqual(detail["id"], "office-1")
        self.assertEqual(detail["name"], "압구정원공인중개사")
        self.assertEqual(detail["office_phone"], "02-111-2222")
        self.assertEqual(detail["mobile_phone"], "010-3333-4444")

    def test_same_address_rows_share_existing_canonical_name(self):
        initial = [
            ["ID", "중개업소명", "실제상호", "주소", "연락처", "연락처"],
            ["id-1", "상호A", "미-대표1"],
            ["id-2", "상호B", "미-대표2"],
        ]
        details = [
            {
                "id": "id-1",
                "name": "상호A",
                "address": "서울특별시 강남구 압구정로 1",
                "office_phone": "02-111-2222",
                "mobile_phone": "",
            },
            {
                "id": "id-2",
                "name": "상호B",
                "address": "서울 강남구 압구정로 1",
                "office_phone": "",
                "mobile_phone": "010-3333-4444",
            },
        ]
        service = _FakeService(initial)
        main.update_brokerage_contact_sheet(service, "sheet-id", details)
        output = service.values_api.updated["body"]["values"]

        self.assertEqual(output[0], [
            "ID", "중개업소명", "실제상호", "주소",
            "사무실번호", "휴대전화", "구분"
        ])
        self.assertEqual(len(output), 3)
        self.assertEqual(output[1][2], "미-대표1")
        self.assertEqual(output[2][2], "미-대표1")
        self.assertEqual(output[1][4], "02-111-2222")
        self.assertEqual(output[2][4], "02-111-2222")
        self.assertEqual(output[1][5], "010-3333-4444")
        self.assertEqual(output[2][5], "010-3333-4444")
        self.assertEqual(output[1][6], "압구정업소")
        self.assertEqual(output[2][6], "압구정업소")

    def test_unknown_broker_is_recorded_as_external(self):
        initial = [
            ["ID", "중개업소명", "실제상호", "주소", "사무실번호", "휴대전화", "구분"],
            ["known-1", "기존업소", "미-기존", "", "", "", "압구정업소"],
        ]
        details = [{
            "id": "outside-1",
            "name": "외부중개업소",
            "address": "서울 서초구 외부로 10",
            "office_phone": "02-555-1111",
            "mobile_phone": "010-5555-1111",
        }]
        service = _FakeService(initial)
        main.update_brokerage_contact_sheet(service, "sheet-id", details)
        output = service.values_api.updated["body"]["values"]

        self.assertEqual(len(output), 3)
        self.assertEqual(output[2][0], "outside-1")
        self.assertEqual(output[2][1], "외부중개업소")
        self.assertEqual(output[2][6], "외부업소")

    def test_same_phone_groups_rows_and_preserves_bad_type(self):
        initial = [
            ["ID", "중개업소명", "실제상호", "주소", "사무실번호", "휴대전화", "구분"],
            ["id-1", "상호A", "주의업소", "", "02-111-2222", "", "양아치업소"],
            ["id-2", "상호B", "다른상호", "", "02-111-2222", "", "외부업소"],
        ]
        service = _FakeService(initial)
        main.update_brokerage_contact_sheet(
            service,
            "sheet-id",
            [{"id": "id-2", "name": "상호B"}]
        )
        output = service.values_api.updated["body"]["values"]
        self.assertEqual(output[1][2], "주의업소")
        self.assertEqual(output[2][2], "주의업소")
        self.assertEqual(output[1][6], "양아치업소")
        self.assertEqual(output[2][6], "양아치업소")

    def test_unknown_broker_without_contact_is_not_appended(self):
        initial = [
            ["ID", "중개업소명", "실제상호", "주소", "사무실번호", "휴대전화", "구분"],
            ["known-1", "기존업소", "미-기존", "", "", "", "압구정업소"],
        ]
        service = _FakeService(initial)
        main.update_brokerage_contact_sheet(
            service,
            "sheet-id",
            [{"id": "empty-1", "name": "연락처없는외부업소"}]
        )
        output = service.values_api.updated["body"]["values"]
        self.assertEqual(len(output), 2)

    def test_external_with_five_ads_is_promoted_to_bad(self):
        initial = [
            ["ID", "중개업소명", "실제상호", "주소", "사무실번호", "휴대전화", "구분"],
            ["outside-1", "외부상호", "외부대표", "서울 강남구", "02-111-2222", "", "외부업소"],
            ["inside-1", "내부상호", "압-내부", "서울 강남구 2", "02-333-4444", "", "압구정업소"],
        ]
        service = _FakeService(initial)
        promoted = main.promote_frequent_external_brokers(
            service,
            "sheet-id",
            {"outside-1": 5, "inside-1": 10},
            {},
            threshold=5
        )
        output = service.values_api.updated["body"]["values"]
        self.assertEqual(promoted, 1)
        self.assertEqual(output[1][6], "양아치업소")
        self.assertEqual(output[2][6], "압구정업소")


if __name__ == "__main__":
    unittest.main()
