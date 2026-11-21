# ---------------------------------------------------------------------------
# pico_esp_flasher 构建脚本
#   优先用 ../.toolchain 下的本地工具链（gcc / ninja / pico-sdk）
#   产物: firmware/build/esp-flasher.uf2
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$Here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root  = Split-Path -Parent $Here
$TC    = Join-Path $Root '.toolchain'
$Build = Join-Path $Here 'build'

function Fail($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }

# ---------- arm-none-eabi-gcc ----------
$gccDir = Get-ChildItem $TC -Directory -Filter 'xpack-arm-none-eabi-gcc-*' -ErrorAction SilentlyContinue |
          Select-Object -First 1
if ($gccDir) {
    $env:PATH = "$(Join-Path $gccDir.FullName 'bin');$env:PATH"
} elseif (-not (Get-Command arm-none-eabi-gcc -ErrorAction SilentlyContinue)) {
    Fail "找不到 arm-none-eabi-gcc，也没有 .toolchain/xpack-* 目录"
}
Write-Host ("gcc  : " + (& arm-none-eabi-gcc -dumpversion))

# ---------- ninja ----------
$ninja = Join-Path $TC 'ninja.exe'
$gen = 'MinGW Makefiles'
if (Test-Path $ninja) {
    $env:PATH = "$TC;$env:PATH"
    $gen = 'Ninja'
}
Write-Host ("gen  : " + $gen)

# ---------- cmake ----------
$cmakeExe = $null
$sys = Get-Command cmake -ErrorAction SilentlyContinue
if ($sys) { $cmakeExe = $sys.Source }
if (-not $cmakeExe) {
    $scripts = (& python -c "import sysconfig;print(sysconfig.get_path('scripts'))").Trim()
    $cand = Join-Path $scripts 'cmake.exe'
    if (Test-Path $cand) { $cmakeExe = $cand }
}
if (-not $cmakeExe) { Fail "找不到 cmake。执行: python -m pip install cmake" }
Write-Host ("cmake: " + $cmakeExe)

# ---------- pico-sdk ----------
$sdk = Join-Path $TC 'pico-sdk'
if (-not (Test-Path (Join-Path $sdk 'pico_sdk_init.cmake'))) {
    if ($env:PICO_SDK_PATH) { $sdk = $env:PICO_SDK_PATH }
    else { Fail "找不到 pico-sdk（应为 .toolchain/pico-sdk）" }
}
$env:PICO_SDK_PATH = $sdk
Write-Host ("sdk  : " + $sdk)

# ---------- configure ----------
& $cmakeExe -S $Here -B $Build -G $gen -DCMAKE_BUILD_TYPE=Release -DPICO_SDK_PATH="$sdk"
if ($LASTEXITCODE -ne 0) { Fail "cmake configure 失败" }

# ---------- build ----------
& $cmakeExe --build $Build -j 8
if ($LASTEXITCODE -ne 0) { Fail "编译失败" }

$uf2 = Join-Path $Build 'esp-flasher.uf2'
if (Test-Path $uf2) {
    $kb = [math]::Round((Get-Item $uf2).Length / 1KB, 1)
    Write-Host ""
    Write-Host "BUILD OK -> $uf2  ($kb KB)" -ForegroundColor Green
    Write-Host "烧录: 按住 BOOTSEL 插 USB -> RPI-RP2 盘 -> 把 uf2 拖进去"
} else {
    Fail "编译完成但没找到 esp-flasher.uf2"
}
