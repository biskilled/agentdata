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

# A venv hardcodes absolute paths, so a MOVED/renamed connector folder leaves a broken
# .venv (python/pip point at a path that no longer exists). Detect that and rebuild.
if (Test-Path .venv) {
  $venvOk = $false
  try { & .\.venv\Scripts\python.exe -c "import sys" 2>$null; $venvOk = ($LASTEXITCODE -eq 0) } catch { $venvOk = $false }
  if (-not $venvOk) {
    Write-Host "- existing .venv is broken (folder moved?) - rebuilding ..."
    Remove-Item -Recurse -Force .venv
  }
}
Write-Host "- creating virtual environment (.venv) ..."
& $py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
Write-Host "- installing dependencies ..."
# Use `python -m pip` (not the bare pip wrapper, whose shebang can be stale).
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
# SQL Server driver: best-effort (also needs a system ODBC driver at runtime, so a failure
# here must NOT block other databases). The connector's startup preflight prints the fix.
try { & .\.venv\Scripts\python.exe -m pip install --quiet pyodbc 2>$null } catch {}
if ($LASTEXITCODE -ne 0) { Write-Host "  - pyodbc not installed - only needed for SQL Server (connector will tell you what to add)" }

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
