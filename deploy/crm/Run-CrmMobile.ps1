param([Parameter(Mandatory=$true)][string]$Config)
$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath (Resolve-Path -LiteralPath $Config).Path -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($field in @('source','python','home','assets','sessionKey','tlsCertificate','tlsKey')) {
    $value = [string]$settings.$field
    if (-not [IO.Path]::IsPathRooted($value)) { throw "An absolute $field path is required" }
    if ($field -ne 'home' -and -not (Test-Path -LiteralPath $value)) { throw "Missing $field" }
}
if ($settings.primary -ne 'https://hard.rimborso.ru' -or $settings.crm -ne 'https://hard.rimborso.ru/crm-pilot') { throw 'Untrusted CRM service address' }
$state = [IO.Path]::GetFullPath($settings.home)
New-Item -ItemType Directory -Force -Path $state | Out-Null
$env:PYTHONPATH = (Join-Path $settings.source 'server') + ';' + $settings.source
$env:PYTHONIOENCODING = 'utf-8'
$arguments = @('-X','utf8','-m','crm.mobile_server','--home',$state,'--primary',$settings.primary,
    '--crm',$settings.crm,'--session-key-file',$settings.sessionKey,'--native-executor',
    '--public-origin','https://hard.rimborso.ru','--assets',$settings.assets,
    '--host',$settings.host,'--port',[string]$settings.port,'--tls-cert',$settings.tlsCertificate,'--tls-key',$settings.tlsKey)
foreach ($root in $settings.sourceRoots) { $arguments += @('--source-root',[string]$root) }
Set-Location -LiteralPath $settings.source
$log = Join-Path $state 'mobile-runtime.log'
if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 10MB) { Move-Item -LiteralPath $log -Destination ($log+'.previous') -Force }
& $settings.python @arguments >> $log 2>&1
exit $LASTEXITCODE
