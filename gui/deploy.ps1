# 一键生成自包含发布目录 gui/release（含 Qt6Core.dll 等运行库）
#
# 用法（PowerShell，若被执行策略拦住加 -ExecutionPolicy Bypass）：
#   .\deploy.ps1
#   .\deploy.ps1 -Qt D:\Qt\Qt6.8.5MinGW64 -Mingw D:\Qt\winlibs-...\mingw64\bin
#
# 产物：gui\release\dc1console.exe 可直接双击，无需 Qt 在 PATH 里。

param(
    [string]$Qt = "D:\Qt\Qt6.8.5MinGW64",
    [string]$Mingw = ""
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $here

if (-not $Mingw) {
    $cand = Get-ChildItem "D:\Qt" -Directory -Filter "winlibs-*" -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($cand) { $Mingw = Join-Path $cand.FullName "mingw64\bin" }
}
if (-not (Test-Path "$Qt\bin\Qt6Core.dll")) { throw "Qt 路径不对：$Qt" }
if (-not (Test-Path "$Mingw\g++.exe"))     { throw "MinGW 路径不对：$Mingw" }

Write-Host "== 配置 & 编译 (Release) =="
& "$Mingw\cmake.exe" -S . -B build -G "MinGW Makefiles" `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_PREFIX_PATH="$Qt" `
    -DCMAKE_CXX_COMPILER="$Mingw\g++.exe" `
    -DCMAKE_MAKE_PROGRAM="$Mingw\mingw32-make.exe"
if ($LASTEXITCODE -ne 0) { throw "cmake 配置失败" }
& "$Mingw\cmake.exe" --build build -j 8
if ($LASTEXITCODE -ne 0) { throw "编译失败" }

Write-Host "== 部署到 release\ =="
if (Test-Path release) { Remove-Item release -Recurse -Force }
New-Item -ItemType Directory release | Out-Null
Copy-Item build\dc1console.exe release\

$env:PATH = "$Mingw;$Qt\bin;$env:PATH"
& "$Qt\bin\windeployqt.exe" --release --no-translations --qmldir qml release\dc1console.exe
if ($LASTEXITCODE -ne 0) { throw "windeployqt 失败" }

Write-Host "== 清理 debug 库与调试器插件 =="
# 只删"有同名 release 版本"的 *d.dll，避免误删 qdirect2d.dll 之类
Get-ChildItem release -Recurse -Filter *d.dll | ForEach-Object {
    $rel = $_.FullName -replace 'd\.dll$', '.dll'
    if (Test-Path $rel) { Remove-Item $_.FullName -Force }
}
foreach ($junk in @("qmltooling", "platforminputcontexts", "virtualkeyboard")) {
    if (Test-Path "release\$junk") { Remove-Item "release\$junk" -Recurse -Force }
}
foreach ($f in @("Qt6VirtualKeyboard.dll", "Qt6Quick3DUtils.dll")) {
    if (Test-Path "release\$f") { Remove-Item "release\$f" -Force }
}

$size = (Get-ChildItem release -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("== 完成：release\  {0:N0} MB ==" -f $size)
Write-Host "直接运行： .\release\dc1console.exe"
Pop-Location
