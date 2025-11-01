def setup_google_sheets():
    """구글 시트 설정"""
    try:
        credentials_file = 'service_account.json'
        if not os.path.exists(credentials_file):
            print(f"❌ 서비스 계정 파일이 없습니다: {credentials_file}")
            return None
        
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        gc = gspread.authorize(credentials)

        # ✅ 새 스프레드시트 ID로 기본값 교체 (환경변수 우선)
        spreadsheet_id = os.environ.get(
            'SPREADSHEET_ID',
            '1FfeV5dkq7MTe443iMIYjztueWcUkv8ngsrDQmEzeTA4'  # 새 ID
        )
        spreadsheet = gc.open_by_key(spreadsheet_id)

        try:
            worksheet = spreadsheet.worksheet("네이버 매물분석")
            print("✅ 구글 시트 연결 성공")
            return worksheet
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title="네이버 매물분석", rows=1000, cols=20)
            print("✅ 구글 시트 탭 생성 완료")
            return worksheet
    except Exception as e:
        print(f"❌ 구글 시트 설정 실패: {e}")
        return None
