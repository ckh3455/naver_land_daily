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

    def execute(self):
        if self.updated is not None:
            return {"updatedRows": len(self.updated["body"]["values"])}
        return {"values": self.initial}


class _SheetsApi:
    def __init__(self, values_api):
        self.values_api = values_api

    def values(self):
        return self.values_api


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
            "ID", "중개업소명", "실제상호", "주소", "사무실번호", "휴대전화"
        ])
        self.assertEqual(len(output), 3)
        self.assertEqual(output[1][2], "미-대표1")
        self.assertEqual(output[2][2], "미-대표1")
        self.assertEqual(output[1][4], "02-111-2222")
        self.assertEqual(output[2][4], "02-111-2222")
        self.assertEqual(output[1][5], "010-3333-4444")
        self.assertEqual(output[2][5], "010-3333-4444")


if __name__ == "__main__":
    unittest.main()
