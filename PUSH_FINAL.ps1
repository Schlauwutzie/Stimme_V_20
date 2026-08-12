$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== SchlauWutzie V20 – GitHub Final Upload ===" -ForegroundColor Cyan

if (!(Test-Path ".git")) {
    throw "Dieser Ordner ist kein Git-Repository. Öffne PowerShell direkt im lokalen Stimme_V_20-Repository und kopiere den Inhalt dieses Pakets dorthin."
}

if (!(Test-Path "app.py")) { throw "app.py fehlt." }
if (!(Test-Path "assets\schlawutzie.png")) { throw "assets\schlawutzie.png fehlt." }
if (!(Test-Path ".github\workflows\build-windows.yml")) { throw "GitHub-Workflow fehlt." }

git add -A

git status --short

$msg = "V20 FINAL: StefanM OneCore + 9:16 K.I. HUD + Windows EXE build"
git commit -m $msg

git push origin main

Write-Host "" 
Write-Host "FERTIG: main wurde zu GitHub gepusht." -ForegroundColor Green
Write-Host "Danach auf GitHub: Actions -> Build Windows EXE -> neuesten Lauf öffnen." -ForegroundColor Yellow
