# SchlauWutzie K.I. – Video Studio V20 FINAL

Windows-Video-Studio für 9:16-Videos mit **Microsoft OneCore / Microsoft Stefan** als fester deutscher Stimme. Kein weiblicher TTS-Fallback.

## Enthalten

- 1080 × 1920 / 9:16 Ausgabe
- internes Rendering 720 × 1280 bei 30 FPS
- festes OneCore-TTS für Microsoft Stefan
- TTS in einem separaten Prozess, damit ein hängender Windows-TTS-Aufruf die GUI nicht blockiert
- WAV/MP3/M4A/AAC/FLAC/OGG laden
- Audio-Normalisierung über FFmpeg
- animierte Sprach-/Partikelvisualisierung
- 8-Sekunden-Vorschau mit Audio
- MP4-Export mit H.264 + AAC
- FFmpeg über `imageio-ffmpeg` im Python-Build; die EXE braucht keine separate FFmpeg-Installation
- mitgeliefertes SchlauWutzie-K.I.-Standardbild
- GitHub Actions Workflow für eine Windows-Onefile-EXE

## Wichtig zur Stimme

Die App sucht ausschließlich nach einer deutschen Windows-OneCore-Stimme mit **Stefan** im Namen bzw. in der Voice-ID. Wenn Microsoft Stefan auf dem Windows-System nicht installiert ist, bricht die TTS-Erzeugung mit einer klaren Fehlermeldung ab. Es wird **keine Ersatzstimme** ausgewählt.

Das ist absichtlich so, damit nicht plötzlich Katja/Hedda o.ä. verwendet wird.

## Start aus Python

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 app.py
```

## Windows-EXE lokal bauen

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 -m PyInstaller --clean --noconfirm SchlauWutzie_V20_FINAL.spec
```

Danach liegt die fertige Datei hier:

`dist\SchlauWutzie_V20_FINAL.exe`

## GitHub Actions

Der Workflow unter `.github/workflows/build-windows.yml` baut die EXE auf `windows-latest`. Nach einem Push kann die EXE als Actions-Artifact heruntergeladen werden.

## GitHub Release

Für einen Release: Tag wie `v20.0.0` pushen. Der Workflow erzeugt dann zusätzlich eine GitHub-Release und hängt die EXE an.

## Hinweis

Microsoft Stefan ist eine Windows-Systemstimme. Die App bringt die Stimme nicht selbst mit und kann sie nicht auf anderen Betriebssystemen bereitstellen.
