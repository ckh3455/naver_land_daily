#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$runnerServices = Get-Service -Name "actions.runner.*" -ErrorAction SilentlyContinue

if (-not $runnerServices) {
    Write-Host ""
    Write-Host "GitHub Actions Runner 서비스가 발견되지 않았습니다." -ForegroundColor Yellow
    Write-Host "Runner 설치 폴더에서 config.cmd를 다시 실행하고,"
    Write-Host "'Run the runner as a service?' 질문에 Y를 선택해야 합니다."
    Write-Host "권장 설치 폴더: C:\actions-runner"
    exit 1
}

foreach ($service in $runnerServices) {
    Write-Host "Runner 서비스 발견: $($service.Name)"
    Set-Service -Name $service.Name -StartupType Automatic

    if ($service.Status -ne "Running") {
        Start-Service -Name $service.Name
    }

    $updated = Get-Service -Name $service.Name
    Write-Host "상태: $($updated.Status) / 시작 유형: Automatic" -ForegroundColor Green
}

Write-Host ""
Write-Host "설정 완료: Windows가 시작되면 GitHub Runner도 자동으로 실행됩니다." -ForegroundColor Green
