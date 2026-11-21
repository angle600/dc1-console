# ---------------------------------------------------------------------------
# flash.ps1 - 等待 RPI-RP2 盘出现，把 uf2 写进去，然后验证两个 COM 口
#
# 用法：
#   1) 按住 Pico 的 BOOTSEL 键，插上 USB
#   2) 运行 .\flash.ps1
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$uf2  = Join-Path $Here 'build\esp-flasher.uf2'

if (-not (Test-Path $uf2)) {
    Write-Host "没有 uf2，先执行 .\build.ps1" -ForegroundColor Red
    exit 1
}
$kb = [math]::Round((Get-Item $uf2).Length / 1KB, 1)
Write-Host "uf2: $uf2 ($kb KB)"

Write-Host "等待 RPI-RP2 盘（按住 BOOTSEL 插 USB）..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(180)
$vol = $null
while ((Get-Date) -lt $deadline) {
    $vol = Get-Volume -ErrorAction SilentlyContinue |
           Where-Object { $_.FileSystemLabel -eq 'RPI-RP2' -and $_.DriveLetter } |
           Select-Object -First 1
    if ($vol) { break }
    Start-Sleep -Milliseconds 400
}
if (-not $vol) {
    Write-Host "超时：没看到 RPI-RP2 盘。" -ForegroundColor Red
    Write-Host "检查：是否按住 BOOTSEL 再插线；换一根能传数据的 USB 线。"
    exit 1
}

$letter = $vol.DriveLetter
Write-Host "找到 $letter`:  写入中 ..." -ForegroundColor Cyan
try {
    Copy-Item $uf2 "$letter`:\esp-flasher.uf2" -Force
} catch {
    # 写入过程中卷会消失，属正常
}

Write-Host "等待设备重启 ..."
$deadline = (Get-Date).AddSeconds(40)
while ((Get-Date) -lt $deadline) {
    if (-not (Test-Path "$letter`:\")) { break }
    Start-Sleep -Milliseconds 200
}

Write-Host "等待两个 COM 口枚举 ..." -ForegroundColor Cyan
Start-Sleep -Seconds 3
$deadline = (Get-Date).AddSeconds(30)
$ports = @()
while ((Get-Date) -lt $deadline) {
    $ports = @(Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
               Where-Object { $_.PNPDeviceID -like 'USB\VID_2E8A&PID_1001*' -and $_.Name -match '\(COM\d+\)' })
    if ($ports.Count -ge 2) { break }
    Start-Sleep -Milliseconds 500
}

if ($ports.Count -ge 2) {
    Write-Host ""
    Write-Host "SUCCESS: 枚举到 $($ports.Count) 个串口" -ForegroundColor Green
    $ports | ForEach-Object { Write-Host "  $($_.Name)" }
    Write-Host ""
    Write-Host "谁是谁："
    Write-Host "  * 上电打印 'PICO-ESP-FLASHER ... ready' 的是控制口(CDC1)"
    Write-Host "  * 另一个是数据口(CDC0)，给 esptool"
    Write-Host "  运行 host\picoflash.py --list 可自动识别"
} else {
    Write-Host "只枚举到 $($ports.Count) 个串口，检查设备管理器" -ForegroundColor Yellow
    $ports | ForEach-Object { Write-Host "  $($_.Name)" }
}
