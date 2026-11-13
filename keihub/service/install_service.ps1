# KeiHub Windows 服务安装（管理员）
# 用法：右键“使用 PowerShell 运行”，或在管理员 PowerShell 里执行本文件。

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $PSScriptRoot
$Py = "D:\AI\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "需要管理员权限，正在重新启动..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`""
    exit
}

Write-Host "== KeiHub 服务安装 ==" -ForegroundColor Cyan
Write-Host "Python: $Py"
Write-Host "目录  : $Base"

# 依赖
& $Py -m pip install --disable-pip-version-check -r (Join-Path $Base "requirements.txt")

# 安装/更新服务
& $Py (Join-Path $Base "service\win_service.py") install --startup auto

# 描述
& sc.exe description KeiHub "KeiHub: FatFish / SnowLuma / momoi_bot / DSH 中间层"

# 启动
& sc.exe start KeiHub

# 托盘开机自启
$startup = [Environment]::GetFolderPath("Startup")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $startup "KeiHub Tray.lnk"))
$lnk.TargetPath = $Py
$lnk.Arguments = "`"$Base\service\tray.py`""
$lnk.WorkingDirectory = $Base
$lnk.Description = "KeiHub 托盘"
$lnk.Save()

Write-Host ""
Write-Host "完成。" -ForegroundColor Green
Write-Host "控制台: http://127.0.0.1:8765/"
Write-Host "托盘会在下次登录时自动启动；现在可手动运行：$Base\start_tray.bat"
Write-Host "如果服务读不到 FatFish 数据库，在 services.msc 里把 KeiHub 的登录账号改成当前用户。"
