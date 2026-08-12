$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== SchlauWutzie K.I. – Video Studio V20 FINAL ==="
if (!(Test-Path "app.py")) { throw "app.py fehlt." }
if (!(Test-Path "SchlauWutzie_V20_FINAL.spec")) { throw "SPEC fehlt." }
if (!(Test-Path "assets\schlawutzie.png")) { throw "assets\schlawutzie.png fehlt." }

Write-Host "Installiere Build-Abhängigkeiten ..."
py -3.13 -m pip install --upgrade pip
py -3.13 -m pip install -r requirements.txt

Write-Host "Prüfe PyWinRT-Module ..."
py -3.13 -c "import winrt.windows.foundation; import winrt.windows.foundation.collections; import winrt.windows.media.speechsynthesis; import winrt.windows.storage; import winrt.windows.storage.streams; from winrt.windows.media.speechsynthesis import SpeechSynthesizer; from winrt.windows.storage.streams import DataReader; print('PyWinRT OK:', SpeechSynthesizer, DataReader)"

Write-Host "Prüfe Standardbild ..."
py -3.13 -c "from PIL import Image; im=Image.open('assets\schlawutzie.png'); print('Asset:', im.size, im.mode); assert im.width > 500 and im.height > 800"

Write-Host "Bereinige alten Build ..."
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist

Write-Host "Baue Onefile-EXE ..."
py -3.13 -m PyInstaller --clean --noconfirm SchlauWutzie_V20_FINAL.spec

$exe = Join-Path $PSScriptRoot "dist\SchlauWutzie_V20_FINAL.exe"
if (!(Test-Path $exe)) { throw "Build fehlgeschlagen: EXE wurde nicht erzeugt." }
$size = (Get-Item $exe).Length
if ($size -lt 1000000) { throw "Build fehlgeschlagen: EXE ist unerwartet klein." }

Write-Host "FERTIG: $exe"
Write-Host "Die EXE enthält das Standardbild und die PyWinRT-Module."
