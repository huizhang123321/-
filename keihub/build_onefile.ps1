# 打包单文件 KeiHub.exe（PyInstaller onefile）
$ErrorActionPreference = "Stop"
$Base = $PSScriptRoot
Set-Location $Base
$Py = "D:\AI\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

Write-Host "== KeiHub onefile build ==" -ForegroundColor Cyan
& $Py -m pip install --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller

if (-not (Test-Path (Join-Path $Base "assets\keihub.ico"))) {
    & $Py (Join-Path $Base "make_icon.py")
}

$DistRoot = Join-Path $Base "dist_onefile"
$Exe = Join-Path $DistRoot "KeiHub.exe"
if (Test-Path $DistRoot) { Remove-Item $DistRoot -Recurse -Force }

& $Py -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name KeiHub `
    --distpath $DistRoot `
    --workpath (Join-Path $Base "build_onefile") `
    --specpath (Join-Path $Base "build_onefile") `
    --icon (Join-Path $Base "assets\keihub.ico") `
    --add-data "$Base\config.yaml;." `
    --add-data "$Base\README.md;." `
    --add-data "$Base\service;service" `
    --add-data "$Base\dsh-plugin;dsh-plugin" `
    --collect-all websockets `
    --collect-all yaml `
    --collect-all flask `
    --collect-all pystray `
    --collect-all PIL `
    (Join-Path $Base "launcher.py")

if (-not (Test-Path $Exe)) { throw "onefile exe 未生成: $Exe" }
Write-Host ""
Write-Host "完成：" -ForegroundColor Green
Write-Host "  EXE: $Exe"
Write-Host "  $(Get-Item $Exe | Select-Object -ExpandProperty Length) bytes"
Write-Host "首次运行会在 exe 旁边生成 config.yaml / service / dsh-plugin。"
