$ErrorActionPreference = "Stop"

# Build a standalone Windows one-file executable for RazeCLI.
# Output: dist/razecli.exe

$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  Write-Host "Missing .venv. Create it first:"
  Write-Host "  py -3 -m venv .venv"
  Write-Host "  .venv\Scripts\python.exe -m pip install -U pip"
  Write-Host "  .venv\Scripts\python.exe -m pip install -e `".[detect,bundle]`""
  exit 1
}

& $Python -m pip install -e ".[detect,bundle]"
# Needed for curses-based TUI on Windows.
& $Python -m pip install windows-curses

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name razecli `
  --collect-all hid `
  --collect-submodules razecli.models `
  --collect-submodules razecli.backends `
  --collect-submodules razecli.ble `
  --hidden-import curses `
  razecli/__main__.py

Write-Host ""
Write-Host "Built binary: $RootDir\dist\razecli.exe"
Write-Host "Run it:"
Write-Host "  .\dist\razecli.exe --help"
