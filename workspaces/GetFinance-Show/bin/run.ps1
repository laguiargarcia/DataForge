$ProjectRoot = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
Set-Location $ProjectRoot

& ".venv\Scripts\Activate.ps1"

dataforge jobs run finance_daily --workspace GetFinance

Write-Host "Done."
