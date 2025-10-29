# 🏠 네이버 부동산 매물 크롤링 자동화 (GitHub Actions)

이 프로젝트는 Playwright와 Python을 사용하여 네이버 부동산의 지정된 단지 매물 정보를 주기적으로 크롤링하고, 그 결과를 **Google Sheets**에 자동 기록하는 GitHub Actions 워크플로우입니다.

기존 Google Cloud Scheduler 환경에서 GitHub Actions 환경으로 전환하여, 별도의 서버 관리 없이 스케줄링된 작업을 수행합니다.

## ⚙️ 설정 및 배포 전 준비 사항

이 워크플로우를 성공적으로 실행하기 위해서는 **Google Sheets 연동 인증** 설정이 필수입니다.

### 1. Google Sheets 및 서비스 계정 준비

1.  **서비스 계정 키 (JSON) 준비:** Google Cloud에서 서비스 계정을 생성하고, 키를 **JSON 형식**으로 다운로드합니다. 이 파일이 **`service_account.json`**에 해당합니다.
2.  **스프레드시트 공유:** 데이터를 기록할 Google Sheets에 서비스 계정 이메일 주소(`[계정명]@[프로젝트-id].iam.gserviceaccount.com`)를 **편집자** 권한으로 공유합니다.
3.  **스프레드시트 ID 확인:** Google Sheets URL에서 ID를 복사합니다. (예: `https://docs.google.com/spreadsheets/d/`**`1QP56lm5kPB...`**`/edit`)

### 2. GitHub Secrets 설정

다운로드한 JSON 키와 스프레드시트 ID를 GitHub 저장소의 Secret으로 등록해야 합니다.

1.  저장소 **Settings** $\rightarrow$ **Security** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions**로 이동합니다.
2.  다음 이름으로 **New repository secret**을 생성합니다.

| Secret 이름 | 값 내용 | 설명 |
| :--- | :--- | :--- |
| `GCP_SERVICE_ACCOUNT_KEY` | `service_account.json` 파일의 **전체 내용** (JSON 문자열) | Google Sheets 인증에 사용 |
| `SPREADSHEET_ID` | 데이터를 기록할 Google Sheets의 **스프레드시트 ID** | 크롤링 결과가 기록될 대상 시트의 ID |

---

## 🛠️ 핵심 파일 수정 (매우 중요)

GitHub Actions에서 스크립트 실행을 위해 **`main.py`** 파일의 코드를 반드시 수정해야 합니다.

### `main.py` 수정 내용

`main.py` 파일의 가장 하단에 있던 `@functions_framework.http` 데코레이터가 붙은 Cloud Function 진입점 함수를 제거하고, 스크립트 직접 실행을 위한 코드로 대체해야 합니다.

**✅ 수정 요약:**

1.  `functions_framework` 관련 모든 코드를 제거합니다.
2.  Cloud Function 내부의 `action == 'start'` 로직을 분리하여 `execute_crawling_and_record()`와 같은 독립적인 함수로 만듭니다. (이전 답변에서 제공된 수정 가이드 참고)
3.  파일 최하단에 스크립트 실행 진입점을 추가합니다.

```python
# main.py 파일 최하단에 추가 (또는 기존 Cloud Function Entry Point를 대체)

def execute_crawling_and_record():
    # ... (기존 크롤링 및 구글 시트 기록 로직) ...
    pass 

if __name__ == "__main__":
    # 요청을 처리하는 로직 대신, 크롤링 실행 함수를 직접 호출
    print("=== GitHub Actions 크롤링 스크립트 시작 ===")
    execute_crawling_and_record()
    print("=== GitHub Actions 크롤링 스크립트 완료 ===")
