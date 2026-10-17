$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) { py -3 -m venv .venv }
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required' }
& '.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& '.venv\Scripts\python.exe' app.py init
& '.venv\Scripts\python.exe' app.py doctor
if ($LASTEXITCODE -ne 0) { throw 'Installation checks failed' }
Write-Host 'Ready. Run Start-Windows.bat. Chrome is required for browser enrollment.'
