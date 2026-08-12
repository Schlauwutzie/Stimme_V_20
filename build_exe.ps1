$ErrorActionPreference = "Stop"

Write-Host "=== SchlauWutzie K.I. Video Studio V20 FINAL ==="
Write-Host "Installiere/aktualisiere Build-Abhängigkeiten..."
py -3.13 -m pip install -r requirements.txt

Write-Host "Baue Onefile-EXE..."
py -3.13 -m PyInstaller --clean --noconfirm SchlauWutzie_V20_FINAL.spec

$exe = Join-Path $PSScriptRoot "dist\SchlauWutzie_V20_FINAL.exe"
if (!(Test-Path $exe)) {
    throw "Build fehlgeschlagen: EXE wurde nicht erzeugt."
}

Write-Host "FERTIG:" $exe
