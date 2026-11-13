# 打包 KeiHub 便携版（PyInstaller onedir）
# 输出：dist\KeiHub\KeiHub.exe + 外部 config.yaml/service/dsh-plugin
$ErrorActionPreference = "Stop"
$Base = $PSScriptRoot
Set-Location $Base
$Py = "D:\AI\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

Write-Host "== KeiHub portable build ==" -ForegroundColor Cyan
Write-Host "Python: $Py"
Write-Host "目录  : $Base"

& $Py -m pip install --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller

if (-not (Test-Path (Join-Path $Base "assets\keihub.ico"))) {
    & $Py (Join-Path $Base "make_icon.py")
}

$DistRoot = Join-Path $Base "dist"
$Dist = Join-Path $DistRoot "KeiHub"
if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }

& $Py -m PyInstaller `
    --noconfirm --clean --onedir --windowed `
    --name KeiHub `
    --distpath (Join-Path $Base "dist") `
    --workpath (Join-Path $Base "build") `
    --specpath $Base `
    --icon (Join-Path $Base "assets\keihub.ico") `
    --collect-all websockets `
    --collect-all yaml `
    --collect-all flask `
    --collect-all pystray `
    --collect-all PIL `
    (Join-Path $Base "launcher.py")

# 外部文件：配置、服务脚本、DSH 插件、说明
$CfgSrc = Join-Path $Base "config.yaml"
if (-not (Test-Path $CfgSrc)) { throw "config.yaml 不存在: $CfgSrc" }
Copy-Item -LiteralPath $CfgSrc -Destination (Join-Path $Dist "config.yaml") -Force
if (-not (Test-Path (Join-Path $Dist "config.yaml"))) { throw "config.yaml 复制失败" }
Copy-Item -LiteralPath (Join-Path $Base "README.md") -Destination (Join-Path $Dist "README.md") -Force
Copy-Item -LiteralPath (Join-Path $Base "service") -Destination $Dist -Recurse -Force
Copy-Item -LiteralPath (Join-Path $Base "dsh-plugin") -Destination $Dist -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $Dist "data"), (Join-Path $Dist "logs") | Out-Null
Write-Host "外部文件已复制（config.yaml / service / dsh-plugin）"

Get-ChildItem $Dist -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$Zip = Join-Path $DistRoot "KeiHub-portable.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $Dist "*") -DestinationPath $Zip -Force

Write-Host ""
Write-Host "完成：" -ForegroundColor Green
Write-Host "  EXE: $(Join-Path $Dist 'KeiHub.exe')"
Write-Host "  ZIP: $Zip"
Write-Host "目标机器不需要 Python；FatFish / SnowLuma / DSH 仍需各自安装。"
