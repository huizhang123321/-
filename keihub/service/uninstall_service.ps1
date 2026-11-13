# KeiHub Windows 服务卸载（管理员）
$ErrorActionPreference = "Continue"
$Py = "D:\AI\python.exe"
$Base = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path $Py)) { $Py = "python" }

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "需要管理员权限，正在重新启动..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    exit
}

& sc.exe stop KeiHub
Start-Sleep -Seconds 2
& $Py (Join-Path $Base "service\win_service.py") remove

$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "KeiHub Tray.lnk"
if (Test-Path $lnk) { Remove-Item $lnk -Force }

Write-Host "KeiHub 服务与托盘自启已移除。" -ForegroundColor Green
