# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

project = Path(SPECPATH)

# imageio-ffmpeg contains the FFmpeg binary as package data.
ff_datas, ff_binaries, ff_hidden = collect_all("imageio_ffmpeg")

# PyWinRT namespace packages use native extension modules. Collect the
# namespaces explicitly so the one-file EXE can start the TTS helper.
winrt_hidden = []
for package in (
    "winrt.windows.media.speechsynthesis",
    "winrt.windows.storage.streams",
):
    winrt_hidden.extend(collect_submodules(package))

analysis = Analysis(
    [str(project / "app.py")],
    pathex=[str(project)],
    binaries=ff_binaries,
    datas=ff_datas + [(str(project / "assets" / "schlawutzie.png"), "assets")],
    hiddenimports=ff_hidden + winrt_hidden + [
        "winrt.windows.media.speechsynthesis",
        "winrt.windows.storage.streams",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="SchlauWutzie_V20_FINAL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)
