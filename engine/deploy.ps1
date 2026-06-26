# Деплой движка «Контур · Выезды» — ЧИСТЫЙ ssh/scp по ключу (без Posh-SSH).
#   powershell -ExecutionPolicy Bypass -File engine\deploy.ps1
#
# Несёт ТОЛЬКО КОД — реальных ПД на сервере нет.
# Хост/юзер — из ..\_infra\secrets.env (SRV2_HOST / SRV2_USER).
# Ключ — %USERPROFILE%\.ssh\kontur_deploy (переопределяется $env:KONTUR_KEY).
# Один раз поставить ключ на сервер (если ещё не стоит):
#   type $env:USERPROFILE\.ssh\kontur_deploy.pub | ssh root@<HOST> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$root = Split-Path $here -Parent
$sec  = Get-Content "$root\_infra\secrets.env" | ConvertFrom-StringData
$H = $sec.SRV2_HOST
$U = if ($sec.SRV2_USER) { $sec.SRV2_USER } else { 'root' }
$key = if ($env:KONTUR_KEY) { $env:KONTUR_KEY } else { "$env:USERPROFILE\.ssh\kontur_deploy" }
if (-not (Test-Path $key)) { throw "нет ключа $key — сгенерь: ssh-keygen -t ed25519 -f `"$key`"" }

$opts = @('-i', $key, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'LogLevel=ERROR',
          '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=20')
$tgz = Join-Path $env:TEMP 'kontur-engine.tgz'

Write-Host "-> пакую engine/ (только код) ..."
# tar.exe есть в Windows 10+ (bsdtar). Исключаем кэши/локальные артефакты.
& tar -czf $tgz -C $root --exclude='*__pycache__*' --exclude='*.pyc' --exclude='engine/.venv' --exclude='*.tgz' engine
if ($LASTEXITCODE -ne 0) { throw "tar failed ($LASTEXITCODE)" }

Write-Host "-> заливаю на $U@$H ..."
& ssh @opts "$U@$H" "mkdir -p /opt/kontur"
if ($LASTEXITCODE -ne 0) { throw "ssh mkdir failed ($LASTEXITCODE)" }
& scp @opts $tgz "${U}@${H}:/opt/kontur/engine.tgz"
if ($LASTEXITCODE -ne 0) { throw "scp failed ($LASTEXITCODE)" }

Write-Host "-> распаковка + зависимости + рестарт ..."
& ssh @opts "$U@$H" "cd /opt/kontur && tar xzf engine.tgz && rm -f engine.tgz && bash engine/_remote_setup.sh"
if ($LASTEXITCODE -ne 0) { throw "remote setup failed ($LASTEXITCODE)" }

Write-Host "-> внешняя проверка ..."
try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 "https://kontur.$H.nip.io/healthz").Content } catch { Write-Warning $_ }
Remove-Item $tgz -ErrorAction SilentlyContinue
Write-Host "OK: деплой завершён."
