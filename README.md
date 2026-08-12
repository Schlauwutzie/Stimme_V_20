# SchlauWutzie K.I. – Video Studio V20 FINAL

Die finale Windows-Version für **9:16 / 1080×1920 / 30 FPS** mit deinem SchlauWutzie-K.I.-Bild, einer transparent wirkenden, sprachreaktiven K.I.-HUD-Animation und **Microsoft OneCore / Microsoft Stefan** als einziger interner TTS-Stimme.

## Was in V20 FINAL enthalten ist

- dein 9:16-Standardbild `assets/schlawutzie.png`
- animierte transparente Glas-HUD im unteren Bildbereich
- K.I.-Kern, Sprach-Waveform, Partikel und Systemanalyse reagieren auf die Audio-Lautstärke
- Microsoft OneCore / Microsoft Stefan – **kein weiblicher TTS-Fallback**
- StefanM-TTS in einem separaten Hilfsprozess, damit die GUI bei Windows-TTS-Problemen nicht einfriert
- WAV/MP3/M4A/AAC/FLAC/OGG laden
- FFmpeg über `imageio-ffmpeg` gebündelt
- Vorschau mit Audio
- MP4-Export H.264 + AAC
- GitHub Actions baut eine Windows-x64-Onefile-EXE
- PyWinRT Foundation, Foundation.Collections, Storage und Storage.Streams ausdrücklich eingebunden
- Standardbild wird vor dem Build geprüft, damit der frühere `assets/schlawutzie.png`-Fehler sofort und verständlich auffällt

## StefanM ist absichtlich fest

Die App sucht auf Windows ausschließlich nach einer deutschen OneCore-Stimme mit **Stefan** im Namen bzw. in der Voice-ID. Wird Microsoft Stefan auf dem jeweiligen Windows-PC nicht gefunden, zeigt die App eine Fehlermeldung und verwendet **keine Ersatzstimme**.

Die Stimme selbst kann nicht legal/technisch in die EXE kopiert werden; sie kommt aus den auf Windows installierten OneCore-Stimmen. Die PyWinRT-Programmmodule werden dagegen in der EXE berücksichtigt. PyPI stellt `winrt-Windows.Media.SpeechSynthesis` als `winrt.windows.media.speechsynthesis` bereit und bietet Wheels für CPython 3.13 unter Windows x64.

## GitHub – der fertige Ablauf

1. Den Inhalt dieses Pakets in dein lokales `Stimme_V_20`-Repository übernehmen.
2. Sicherstellen, dass `assets/schlawutzie.png` vorhanden ist.
3. `PUSH_FINAL.ps1` aus dem Repository starten – oder normal committen und pushen.
4. Auf GitHub **Actions → Build Windows EXE** öffnen.
5. Der erfolgreiche Lauf enthält unter **Artifacts**:
   `SchlauWutzie_V20_FINAL-Windows-x64`
6. Dort liegt die fertige `SchlauWutzie_V20_FINAL.exe`.

## Lokal bauen

PowerShell im Projektordner:

```powershell
.\build_exe.ps1
```

Die EXE liegt danach unter:

```text
dist\SchlauWutzie_V20_FINAL.exe
```

## Technischer Packaging-Fix

Die vorherige EXE meldete:

```text
No module named 'winrt.windows.foundation'
```

V20 FINAL behandelt PyWinRT jetzt anhand der **Import-Namen** und sammelt die benötigten Namespaces mit PyInstaller ein. Die PyInstaller-Dokumentation beschreibt `collect_all()` für importierbare Packages/Module; die V20-SPEC verwendet deshalb `winrt.windows.foundation`, `winrt.windows.foundation.collections`, `winrt.windows.media.speechsynthesis` und die benötigten Storage-Namespaces.

Außerdem prüft GitHub Actions vor dem PyInstaller-Lauf ausdrücklich:

```text
assets/schlawutzie.png
```

Damit wird der Fehler aus dem vorherigen Lauf nicht mehr als unverständlicher PyInstaller-Abbruch versteckt.

## Hinweis zur EXE

Ich kann hier die Windows-PyInstaller-Buildumgebung nicht selbst ausführen. Deshalb ist die **GitHub-Action der reproduzierbare Windows-Build**; sie installiert Python 3.13 x64, PyWinRT und PyInstaller auf `windows-latest`, prüft die Imports und erzeugt anschließend die Onefile-EXE.
