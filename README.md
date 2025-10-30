# 🏢 네이버 부동산 크롤러 (GitHub Actions)

매일 자동으로 23개 아파트 단지의 매물 정보를 수집하여 Google Sheets에 기록하는 크롤러입니다.

## 📋 기능

- 🤖 **자동 스케줄링**: 매일 오전 9시(한국 시간) 자동 실행
- 🖱️ **수동 실행**: GitHub Actions 페이지에서 버튼 클릭으로 즉시 실행
- 📊 **Google Sheets 연동**: 크롤링 결과를 자동으로 스프레드시트에 기록
- 🏘️ **23개 단지 지원**: 미성, 현대, 한양, 대림 등 23개 아파트 단지
- 💾 **결과 저장**: JSON 형태로 크롤링 결과 아티팩트 저장

## 🚀 설정 방법

### 1️⃣ Google Cloud 설정

#### 서비스 계정 생성
1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. 프로젝트 생성 또는 선택
3. **IAM 및 관리자** → **서비스 계정** 이동
4. **서비스 계정 만들기** 클릭
5. 서비스 계정 이름 입력 (예: `naver-crawler`)
6. **완료** 클릭

#### 서비스 계정 키 생성
1. 생성된 서비스 계정 클릭
2. **키** 탭 → **키 추가** → **새 키 만들기**
3. **JSON** 선택 후 **만들기**
4. 다운로드된 JSON 파일 내용 복사 (나중에 사용)

#### Google Sheets API 활성화
1. **API 및 서비스** → **라이브러리** 이동
2. "Google Sheets API" 검색 후 **사용 설정** 클릭

### 2️⃣ Google Sheets 준비

1. [Google Sheets](https://sheets.google.com) 에서 새 스프레드시트 생성
2. 스프레드시트 URL에서 ID 복사
   - URL: `https://docs.google.com/spreadsheets/d/[SPREADSHEET_ID]/edit`
   - `[SPREADSHEET_ID]` 부분이 스프레드시트 ID
3. **공유** 버튼 클릭
4. 서비스 계정 이메일 주소를 **편집자** 권한으로 추가
   - 이메일: `your-service-account@project-id.iam.gserviceaccount.com`

### 3️⃣ GitHub Repository 설정

#### Secrets 추가
1. GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret** 클릭
3. 다음 2개의 secret 추가:

**SECRET 1: SERVICE_ACCOUNT_JSON**
- Name: `SERVICE_ACCOUNT_JSON`
- Value: 다운로드한 서비스 계정 JSON 파일 전체 내용
```json
{
  "type": "service_account",
  "project_id": "your-project",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
  ...
}
```

**SECRET 2: SPREADSHEET_ID**
- Name: `SPREADSHEET_ID`
- Value: Google Sheets 스프레드시트 ID
```
1QP56lm5kPBdsUhrgcgY2U-JdmukXIkKCSxefd1QExKE
```

### 4️⃣ 파일 구조

```
.
├── .github/
│   └── workflows/
│       └── naver-crawling.yml    # GitHub Actions 워크플로우
├── main_github.py                # 메인 크롤링 스크립트
├── requirements.txt              # Python 의존성
└── README.md                     # 이 파일
```

## ▶️ 실행 방법

### 자동 실행
- 매일 오전 9시(한국 시간)에 자동으로 실행됩니다.

### 수동 실행
1. GitHub 저장소 → **Actions** 탭
2. 왼쪽에서 **네이버 부동산 크롤링** 선택
3. **Run workflow** 버튼 클릭
4. **Run workflow** 확인

### 로컬 실행 (테스트용)
```bash
# 의존성 설치
pip install -r requirements.txt
playwright install chromium

# 서비스 계정 키 파일 생성
echo '{ ... }' > service_account.json

# 환경 변수 설정
export SPREADSHEET_ID="your-spreadsheet-id"

# 실행
python main_github.py
```

## 📊 결과 확인

### Google Sheets
- 설정한 스프레드시트의 "네이버 매물분석" 탭에서 확인
- 자동으로 헤더와 데이터가 추가됩니다

### GitHub Actions
- **Actions** 탭에서 실행 로그 확인
- 완료된 워크플로우에서 `crawling-results` 아티팩트 다운로드 가능

## 📝 수집 데이터 항목

| 항목 | 설명 |
|------|------|
| 단지명 | 아파트 단지 이름 |
| 거래구분 | 매매/전세/월세 |
| 동 | 동 번호 |
| 층수 | 층 정보 |
| 면적 | 전용/공급 면적 |
| 가격 | 거래 가격 |
| 중개업소 | 등록 부동산 |
| 등록일자 | 매물 등록일 |
| 특기사항 | 방향, 태그 등 |
| 제공 | 정보 제공처 |
| 매물번호 | 네이버 매물 번호 |

## 🏘️ 지원 단지 목록

총 23개 단지:
- 미성1차, 미성2차
- 신현대
- 현대1~8차, 현대10,13,14차, 현대65동, 현대빌라트
- 한양1~8차
- 대림빌라트
- 트리마제
- 메이플자이

## ⚠️ 주의사항

1. **Rate Limit**: 네이버 API 호출 제한으로 일부 단지가 실패할 수 있습니다.
2. **실행 시간**: 전체 크롤링은 약 10~15분 소요됩니다.
3. **GitHub Actions 제한**: 
   - Public 저장소: 무제한
   - Private 저장소: 월 2,000분 무료
4. **서비스 계정 키 보안**: 
   - 절대 코드에 직접 포함하지 마세요
   - GitHub Secrets만 사용하세요

## 🔧 문제 해결

### "구글 시트 연결 실패"
- Secrets이 올바르게 설정되었는지 확인
- 서비스 계정에 스프레드시트 편집 권한이 있는지 확인

### "Playwright 브라우저 실행 실패"
- workflow 파일의 `playwright install-deps` 명령이 있는지 확인

### "Rate exceeded" 오류
- 네이버 API 제한으로 정상적인 현상입니다
- 다음 실행 시 다시 시도됩니다

## 📜 라이선스

MIT License

## 🤝 기여

이슈와 PR은 언제나 환영합니다!

---

**제작**: [Your Name]  
**최종 수정**: 2025-10-30
