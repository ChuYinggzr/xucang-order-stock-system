$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '无法创建 Python 环境，请确认已安装 Python 3.10 或更新版本。' }
    & $projectPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重试。' }
}
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
& $projectPython -m flask --app app db upgrade
if ($LASTEXITCODE -ne 0) { throw '数据库初始化失败，请检查 .env 配置；不会清空原有数据库。' }
& $projectPython -m flask --app app seed-demo
if ($LASTEXITCODE -ne 0) { throw '演示数据初始化失败。' }
Write-Host ''
Write-Host 'Open http://127.0.0.1:5010 in your browser.'
Write-Host 'Demo accounts are saved in demo_accounts.txt.'
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
Write-Host ''
& $projectPython -m flask --app app run --host 127.0.0.1 --port 5010
