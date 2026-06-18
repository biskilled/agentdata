# AgentData relational connector - Windows installer (PowerShell).
#
# Prerequisites (one-time):
#   * Python 3.10+   ->  https://www.python.org/downloads/  (tick "Add python.exe to PATH")
#                        or:  winget install Python.Python.3.12
#   * For SQL Server only: install "ODBC Driver 18 for SQL Server" from Microsoft.
#   (PostgreSQL / MySQL / SQLite need no extra system packages.)
#
# Run it:  right-click -> "Run with PowerShell", or in a terminal:
#            powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
# What it does: creates a local venv, installs deps, checks your .env, starts the connector.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = "python"
if (-not (Get-Command $py -ErrorAction SilentlyContinue)) {
  Write-Error "python not found. Install Python 3.10+ (https://www.python.org/downloads/, tick 'Add to PATH') and re-run."
  exit 1
}
$ok = & $py -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; if ($LASTEXITCODE -ne 0) {
  Write-Error "Python 3.10+ required."; exit 1
}

Write-Host "- creating virtual environment (.venv) ..."
& $py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
Write-Host "- installing dependencies ..."
& .\.venv\Scripts\pip.exe install --quiet -r requirements.txt

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  Write-Host ""
  Write-Host "  >> Created .env from the template. Open it and fill in:"
  Write-Host "       AGENT_TOKEN           (from the AgentData UI when you registered this connector)"
  Write-Host "       SOURCE_DATABASE_URL   (a READ-ONLY user - only if this connector reads a data source)"
  Write-Host "       STAGING_DATABASE_URL  (a WRITE/admin user - only if this connector backs a staging DB)"
  Write-Host "     then re-run .\install-windows.ps1"
  exit 0
}

Write-Host "- starting connector (Ctrl-C to stop) ..."
& .\.venv\Scripts\python.exe agent.py
