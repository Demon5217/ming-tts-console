# WSL 自检 / 自动重置脚本（Ming 控制台入口调用）
# 作用：WSL 无响应（HCS_E_CONNECTION_TIMEOUT / 命令卡住）时自动 wsl --shutdown 再唤醒
# 用法：powershell -File wsl-guard.ps1 -Distro Ubuntu-22.04
#       （不传 -Distro 时读取环境变量 MING_WSL_DISTRO，再退化到 Ubuntu-22.04）
param([string]$Distro = $env:MING_WSL_DISTRO)

$ErrorActionPreference = 'SilentlyContinue'
$wsl = 'C:\Windows\System32\wsl.exe'
if (-not $Distro) { $Distro = 'Ubuntu-22.04' }

Write-Host '  probing WSL ...'
$job = Start-Job -ScriptBlock {
    param($exe, $distro)
    & $exe -d $distro -e true
    exit $LASTEXITCODE
} -ArgumentList $wsl, $Distro

$finished = Wait-Job $job -Timeout 12
if (-not $finished) {
    Stop-Job $job -Force
    Remove-Job $job -Force
    Write-Host '  WSL not responding -> resetting VM (wsl --shutdown) ...'
    & $wsl --shutdown
    Start-Sleep -Seconds 6
} else {
    $code = (Receive-Job $job) | Out-Null
    Remove-Job $job -Force
}

# 再确认一次能不能唤醒
$t0 = Get-Date
& $wsl -d $Distro -e true
$elapsed = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
if ($LASTEXITCODE -ne 0) {
    Write-Host ("  WSL still broken (exit={0}, {1}s). Try rebooting the PC." -f $LASTEXITCODE, $elapsed)
} else {
    Write-Host ("  WSL ready ({0}s)" -f $elapsed)
}

# 钉住虚拟机，避免空闲被回收导致服务被杀
$pin = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
       Where-Object { $_.CommandLine -like '*sleep infinity*' }
if (-not $pin) {
    Start-Process -FilePath $wsl -ArgumentList '-d', $Distro, '-e', 'sleep infinity' -WindowStyle Hidden
    Write-Host '  keepalive started'
} else {
    Write-Host '  keepalive already running'
}
