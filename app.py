import asyncio
import ctypes
import math
import re
import os
import sys
import time
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

try:
    from imageio_ffmpeg import get_ffmpeg_exe
except Exception:
    get_ffmpeg_exe = None

# IMPORTANT:
# Windows OneCore is the ONLY internal TTS path in V15.
# There is deliberately NO pyttsx3 fallback, so a female voice can never
# silently replace StefanM.
try:
    # Import the dependency namespaces explicitly. PyWinRT distributes each
    # Windows SDK namespace as its own package; Foundation and Collections
    # are needed by the generated SpeechSynthesis bindings.
    import winrt.windows.foundation
    import winrt.windows.foundation.collections
    import winrt.windows.storage
    from winrt.windows.media.speechsynthesis import SpeechSynthesizer
    from winrt.windows.storage.streams import DataReader
    WINRT_TTS_AVAILABLE = True
except Exception:
    SpeechSynthesizer = None
    DataReader = None
    WINRT_TTS_AVAILABLE = False

APP_NAME = "SchlauWutzie K.I. – Video Studio V20 FINAL"

# Internal render size from the V16 concept.
W, H = 720, 1280
OUT_W, OUT_H = 1080, 1920
FPS = 30


def ffmpeg_path():
    if get_ffmpeg_exe is not None:
        try:
            return get_ffmpeg_exe()
        except Exception:
            pass
    path = shutil.which("ffmpeg")
    if path:
        return path
    raise RuntimeError(
        "FFmpeg wurde nicht gefunden. In der EXE sollte imageio-ffmpeg "
        "mitgeliefert werden."
    )


# ---------------------------------------------------------------------------
# Windows OneCore / StefanM V19
# ---------------------------------------------------------------------------
# V19 does NOT access WinRT TTS from a Tkinter worker thread. Windows
# OneCore runs in a completely separate helper process. If WinRT blocks,
# only that helper is affected; the GUI process stays alive.


def _all_winrt_voices_direct():
    """Read installed OneCore voices in the dedicated helper process."""
    if not WINRT_TTS_AVAILABLE:
        return []
    value = getattr(SpeechSynthesizer, "all_voices", None)
    if value is None:
        value = getattr(SpeechSynthesizer, "AllVoices", None)
    if value is None:
        raise RuntimeError("SpeechSynthesizer.AllVoices ist in PyWinRT nicht verfügbar.")
    voices = value() if callable(value) else value
    return list(voices)


def _is_stefan_voice(name, voice_id="", language=""):
    """Match only the German Microsoft Stefan voice; never use a fallback."""
    info = f"{name} {voice_id}".strip().lower()
    lang = str(language or "").lower().replace("_", "-")
    has_stefan = bool(re.search(r"(?<![a-z])stefan(?![a-z])", info))
    return has_stefan and ("de-de" in info or lang in ("de", "de-de"))


def _find_stefan_voice_direct():
    voices = _all_winrt_voices_direct()
    items = []
    for voice in voices:
        name = str(getattr(voice, "display_name", "") or "")
        vid = str(getattr(voice, "id", "") or "")
        lang = str(getattr(voice, "language", "") or "")
        gender = str(getattr(voice, "gender", "") or "")
        items.append((name, vid, lang, gender, voice))

    matches = [item for item in items if _is_stefan_voice(item[0], item[1], item[2])]
    if matches:
        matches.sort(key=lambda item: (item[0].lower(), item[1].lower()))
        return matches[0], items
    return None, items


def _status_write(path, message):
    """Atomically publish helper status without relying on stdout/console."""
    if not path:
        return
    try:
        tmp = f"{path}.tmp"
        Path(tmp).write_text(str(message), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


async def _tts_helper_async(text, output_path, status_path):
    if not WINRT_TTS_AVAILABLE:
        raise RuntimeError(
            "Windows OneCore TTS ist nicht verfügbar. "
            "winrt-Windows.Media.SpeechSynthesis fehlt."
        )

    _status_write(status_path, "Windows-OneCore wird geprüft …")

    # This is a fresh Python/EXE process, not Tkinter and not a worker thread.
    # This matches the clean `py -3.13 -c ...` environment in which the
    # installed Microsoft voices were already visible on the user's PC.
    chosen, all_items = _find_stefan_voice_direct()
    if chosen is None:
        names = [item[0] for item in all_items]
        found = ", ".join(names[:20]) if names else "keine OneCore-Stimmen"
        raise RuntimeError(
            "Microsoft Stefan wurde von Windows nicht gefunden.\n\n"
            "Es wird ABSICHTLICH keine Ersatzstimme verwendet.\n\n"
            f"Von Windows gemeldete Stimmen: {found}"
        )

    name, vid, lang, gender, voice = chosen
    _status_write(status_path, f"StefanM gefunden: {name} ({lang})")
    _status_write(status_path, "Sprachsynthese wird gestartet …")

    synth = SpeechSynthesizer()
    try:
        synth.voice = voice
        stream = await synth.synthesize_text_to_stream_async(text)
        _status_write(status_path, "StefanM-Audio wird als WAV gespeichert …")

        input_stream = stream.get_input_stream_at(0)
        reader = DataReader(input_stream)
        try:
            with open(output_path, "wb") as output:
                while True:
                    count = await reader.load_async(65536)
                    if not count:
                        break
                    data = bytearray(count)
                    reader.read_bytes(data)
                    output.write(data)
        finally:
            try:
                reader.close()
            except Exception:
                pass
    finally:
        try:
            synth.close()
        except Exception:
            pass

    with wave.open(output_path, "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        frames = wf.getnframes()
    if channels < 1 or rate < 1 or frames < 1:
        raise RuntimeError("Die erzeugte StefanM-WAV ist leer oder ungültig.")

    _status_write(status_path, "StefanM-Audio fertig.")


def _run_tts_helper_process(input_path, output_path, status_path):
    """Entry point used by the standalone V19 helper process."""
    try:
        text = Path(input_path).read_text(encoding="utf-8")
        if not text.strip():
            raise RuntimeError("Der zu sprechende Text ist leer.")
        asyncio.run(_tts_helper_async(text, output_path, status_path))
        return 0
    except Exception as exc:
        _status_write(status_path, "FEHLER: " + str(exc))
        return 1


def _helper_command(input_path, output_path, status_path):
    # In a PyInstaller one-file build sys.executable is the EXE itself. In a
    # source run it is the active Python interpreter. Both invoke this file
    # in helper mode without needing another script in the EXE.
    return [
        sys.executable,
        "--tts-helper",
        str(input_path),
        str(output_path),
        str(status_path),
    ]


def synthesize_stefan(text, timeout_seconds=90, status_callback=None):
    """
    V19: synthesize StefanM in a separate process.

    The parent process never calls SpeechSynthesizer. If Windows OneCore
    blocks, only the helper process is terminated after the timeout.
    """
    if os.name != "nt":
        raise RuntimeError("Windows OneCore TTS funktioniert nur unter Windows.")
    if not text.strip():
        raise RuntimeError("Der zu sprechende Text ist leer.")

    temp_dir = Path(tempfile.mkdtemp(prefix="SchlauWutzie_V19_TTS_"))
    input_path = temp_dir / "input.txt"
    output_path = temp_dir / "StefanM.wav"
    status_path = temp_dir / "status.txt"
    input_path.write_text(text, encoding="utf-8")
    _status_write(status_path, "StefanM-Hilfsprozess wird gestartet …")

    process = None
    start = time.monotonic()
    last_status = ""
    try:
        try:
            process = subprocess.Popen(
                _helper_command(input_path, output_path, status_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            raise RuntimeError(
                "Der StefanM-Hilfsprozess konnte nicht gestartet werden.\n\n"
                f"Details: {exc}"
            ) from exc

        while True:
            if status_path.exists():
                try:
                    status = status_path.read_text(encoding="utf-8").strip()
                except Exception:
                    status = ""
                if status and status != last_status:
                    last_status = status
                    if status_callback:
                        status_callback(status)

            return_code = process.poll()
            if return_code is not None:
                if return_code != 0:
                    detail = last_status or "Der StefanM-Hilfsprozess wurde ohne Ergebnis beendet."
                    if detail.startswith("FEHLER:"):
                        detail = detail[7:].strip()
                    raise RuntimeError(detail)
                break

            if time.monotonic() - start >= timeout_seconds:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception:
                    pass
                raise RuntimeError(
                    "StefanM wurde nach 90 Sekunden abgebrochen.\n\n"
                    f"Letzter Status: {last_status or 'unbekannt'}"
                )
            time.sleep(0.10)

        if not output_path.exists() or output_path.stat().st_size < 100:
            raise RuntimeError(
                "Der StefanM-Hilfsprozess meldete Erfolg, aber keine gültige WAV-Datei wurde erzeugt."
            )

        with wave.open(str(output_path), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            frames = wf.getnframes()
        if channels < 1 or rate < 1 or frames < 1:
            raise RuntimeError("Die erzeugte StefanM-WAV ist leer oder ungültig.")

        final_fd, final_path = tempfile.mkstemp(prefix="StefanM_", suffix=".wav")
        os.close(final_fd)
        shutil.copy2(output_path, final_path)
        if status_callback:
            status_callback("StefanM-Audio fertig.")
        return final_path
    finally:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Standalone helper dispatch
# ---------------------------------------------------------------------------
# Keep this BEFORE importing tkinter below. The helper process must remain a
# clean Python process so Windows OneCore is not touched from Tkinter.
if __name__ == "__main__" and len(sys.argv) == 5 and sys.argv[1] == "--tts-helper":
    raise SystemExit(_run_tts_helper_process(sys.argv[2], sys.argv[3], sys.argv[4]))

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def audio_to_wav(source):
    """Convert any supported audio format to mono 16 kHz PCM WAV."""
    ff = ffmpeg_path()
    fd, out_path = tempfile.mkstemp(prefix="audio_", suffix=".wav")
    os.close(fd)

    cmd = [
        ff, "-y", "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
        out_path,
    ]
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise RuntimeError(
            "Audio konnte nicht gelesen werden.\n\n"
            + result.stderr.decode(errors="ignore")[-1800:]
        )
    return out_path


def read_audio_pcm(wav_path):
    with wave.open(wav_path, "rb") as wf:
        rate = wf.getframerate()
        frames = wf.getnframes()
        raw = wf.readframes(frames)

    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if data.size == 0:
        data = np.zeros(1, dtype=np.float32)
    return rate, data


def amplitude_curve(wav_path, frame_count):
    rate, data = read_audio_pcm(wav_path)
    frame_count = max(1, int(frame_count))

    positions = np.linspace(
        0, len(data), frame_count, endpoint=False
    ).astype(np.int64)

    window = max(1, int(rate * 0.035))
    amps = np.zeros(frame_count, dtype=np.float32)

    for i, pos in enumerate(positions):
        start = max(0, pos - window // 2)
        end = min(len(data), pos + window // 2)
        chunk = data[start:end]
        rms = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
        amps[i] = min(1.0, rms * 5.0)

    if frame_count >= 7:
        kernel = np.ones(7, dtype=np.float32) / 7.0
        amps = np.convolve(amps, kernel, mode="same")

    return len(data) / rate, amps


# ---------------------------------------------------------------------------
# Visual
# ---------------------------------------------------------------------------

def fit_cover(image, size):
    image = image.convert("RGB")
    sw, sh = image.size
    tw, th = size

    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale), int(sh * scale)

    image = image.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2

    return image.crop((left, top, left + tw, top + th))


def _font(size, bold=False):
    """Use Windows Segoe UI when available; fall back to Pillow's font."""
    candidates = []
    if os.name == "nt":
        candidates.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / ("segoeuib.ttf" if bold else "segoeui.ttf"))
    candidates.extend([
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ])
    for candidate in candidates:
        try:
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _rounded_panel(draw, box, radius=22, fill=(8, 18, 31, 185), outline=(70, 190, 255, 150), width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _draw_brain_orb(draw, cx, cy, radius, amplitude, time_s):
    """Small glowing neural orb that pulses with the voice."""
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    pulse = 1.0 + 0.10 * amplitude + 0.03 * math.sin(time_s * 5.0)
    r = int(radius * pulse)
    for k in range(5, 0, -1):
        rr = r + k * 10
        alpha = max(8, int(26 * amplitude + 5))
        gd.ellipse((cx-rr, cy-rr, cx+rr, cy+rr), fill=(35, 160, 255, alpha))
    for i in range(16):
        a = time_s * (0.35 + i * 0.01) + i * math.pi / 8
        rr = r * (0.45 + 0.05 * math.sin(time_s + i))
        x = cx + math.cos(a) * rr
        y = cy + math.sin(a) * rr
        gd.ellipse((x-3, y-3, x+3, y+3), fill=(255, 196, 80, 170))
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    base.alpha_composite(glow)
    bd = ImageDraw.Draw(base)
    bd.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(7, 24, 42, 225), outline=(65, 190, 255, 210), width=3)
    for j in range(5):
        yy = cy - r + 14 + j * (2*r-28)/4
        bd.arc((cx-r+10, yy-12, cx+r-10, yy+12), 195, 345, fill=(80, 205, 255, 160), width=2)
    for j in range(4):
        xx = cx-r + 16 + j * (2*r-32)/3
        bd.arc((xx-12, cy-r+8, xx+12, cy+r-8), 100, 260, fill=(255, 196, 80, 120), width=2)
    return base


def _draw_waveform(draw, x0, y0, x1, height, amplitude, time_s):
    points = []
    width = max(1, x1 - x0)
    for x in range(x0, x1 + 1, 5):
        t = (x - x0) / width
        envelope = 0.22 + 0.78 * (math.sin(math.pi * t) ** 0.65)
        wave = (
            math.sin(t * 38 + time_s * 8.0) * 0.42
            + math.sin(t * 83 - time_s * 5.0) * 0.20
            + math.sin(t * 141 + time_s * 3.0) * 0.10
        )
        y = y0 - wave * height * amplitude * envelope
        points.append((x, y))
    if len(points) > 1:
        draw.line(points, fill=(75, 190, 255, 220), width=max(2, int(2 + amplitude * 3)))
        glow = [(x, y+2) for x, y in points]
        draw.line(glow, fill=(255, 190, 75, 90), width=1)


def make_frame(background, amplitude, time_s):
    """Render the 9:16 SchlauWutzie scene plus a transparent glass K.I. HUD."""
    frame = background.copy().convert("RGBA")

    # A subtle dark gradient protects the lower HUD without hiding the artwork.
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(H - 500, H):
        rel = (y - (H - 500)) / 500.0
        alpha = int(155 * (rel ** 1.55))
        draw.line((0, y, W, y), fill=(0, 0, 0, alpha))
    frame = Image.alpha_composite(frame, overlay)

    hud = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(hud)

    # Main glass HUD area.
    panel = (24, H - 430, W - 24, H - 34)
    _rounded_panel(d, panel, radius=26, fill=(4, 14, 26, 192), outline=(55, 170, 235, 170), width=2)
    d.line((40, H - 430, W - 40, H - 430), fill=(255, 195, 80, 155), width=2)

    title_font = _font(24, True)
    small_font = _font(13, False)
    bold_font = _font(15, True)
    tiny_font = _font(11, False)

    d.text((48, H - 414), "SchlauWutzie K.I. – VOICE & VISION", font=title_font, fill=(255, 208, 104, 245))
    d.text((W - 155, H - 410), "● REC", font=bold_font, fill=(255, 90, 80, 235))
    d.text((W - 156, H - 390), "1920×1080 • 30 FPS", font=tiny_font, fill=(200, 220, 235, 190))

    # Left: active neural core.
    left_box = (42, H - 360, 178, H - 72)
    _rounded_panel(d, left_box, radius=18, fill=(5, 20, 34, 170), outline=(40, 140, 210, 120), width=1)
    orb_layer = _draw_brain_orb(d, 110, H - 250, 52, amplitude, time_s)
    hud.alpha_composite(orb_layer)
    d.text((66, H - 150), "K.I. AKTIV", font=bold_font, fill=(255, 204, 90, 245))
    d.text((78, H - 126), "● ONLINE", font=small_font, fill=(70, 235, 135, 240))

    # Center: voice waveform / text panel.
    center_box = (196, H - 360, 520, H - 72)
    _rounded_panel(d, center_box, radius=18, fill=(4, 15, 28, 150), outline=(40, 140, 210, 100), width=1)
    d.text((216, H - 340), "Microsoft OneCore • StefanM", font=bold_font, fill=(220, 235, 250, 235))
    _draw_waveform(d, 218, H - 236, 498, 92, amplitude, time_s)
    d.line((218, H - 160, 498, H - 160), fill=(70, 180, 255, 85), width=1)
    # Speech-reactive particles.
    for i in range(34):
        x = 220 + ((i * 47.0 + time_s * (18 + i % 4)) % 270)
        y = H - 185 - ((i * 31) % 55) - amplitude * ((i * 7) % 40)
        r = 1 + int(amplitude * 2)
        d.ellipse((x-r, y-r, x+r, y+r), fill=(95, 205, 255, int(70 + 130*amplitude)))
    d.text((218, H - 136), "StefanM spricht • präzise • klar • ohne Fallback", font=small_font, fill=(190, 215, 235, 220))

    # Right: system analysis.
    right_box = (538, H - 360, W - 42, H - 72)
    _rounded_panel(d, right_box, radius=18, fill=(4, 15, 28, 150), outline=(40, 140, 210, 100), width=1)
    d.text((558, H - 340), "SYSTEM-ANALYSE", font=bold_font, fill=(255, 208, 104, 235))
    rows = [
        ("TTS", "StefanM", True),
        ("SPRACHE", "OneCore", True),
        ("AUDIO", "48 kHz", True),
        ("VIDEO", "1080×1920", True),
        ("RENDER", "GPU/CPU", True),
        ("STATUS", "OPTIMAL", True),
    ]
    yy = H - 310
    for label, value, ok in rows:
        d.ellipse((558, yy+2, 566, yy+10), fill=(70, 235, 135, 230) if ok else (255, 90, 80, 230))
        d.text((574, yy-2), f"{label}: {value}", font=tiny_font, fill=(215, 230, 242, 225))
        yy += 28

    # Bottom meters and network line.
    d.text((48, H - 58), "KEIN WEIBLICHER FALLBACK", font=tiny_font, fill=(205, 220, 235, 190))
    d.text((W - 250, H - 58), "WELT-NETZWERK • VERBUNDEN", font=tiny_font, fill=(80, 230, 150, 220))
    for i in range(22):
        x = 250 + i * 16
        bar = 3 + int((7 + 20 * amplitude) * (0.35 + 0.65 * abs(math.sin(time_s*2 + i*0.7))))
        d.line((x, H-62, x, H-62-bar), fill=(70, 185, 255, 130), width=3)

    # Composite HUD and a fine animated glow line.
    frame = Image.alpha_composite(frame, hud)
    glow = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    y = H - 436 + math.sin(time_s * 2.5) * (2 + 5 * amplitude)
    gd.line((24, y, W - 24, y), fill=(255, 190, 80, int(70 + 100*amplitude)), width=2)
    glow = glow.filter(ImageFilter.GaussianBlur(5))
    return Image.alpha_composite(frame, glow).convert("RGB")


# ---------------------------------------------------------------------------
# Video export
# ---------------------------------------------------------------------------

def render_video(background_path, audio_path, output_path, progress=None):
    background = fit_cover(Image.open(background_path), (W, H))

    with wave.open(audio_path, "rb") as wf:
        rate = wf.getframerate()
        frames = wf.getnframes()
        duration = frames / rate if rate else 0

    frame_count = max(1, int(math.ceil(duration * FPS)))
    _duration, amplitudes = amplitude_curve(audio_path, frame_count)

    workdir = Path(tempfile.mkdtemp(prefix="V17_frames_"))

    try:
        pattern = str(workdir / "frame_%06d.jpg")

        for i in range(frame_count):
            frame = make_frame(
                background,
                float(amplitudes[i]),
                i / FPS,
            )
            frame.save(
                pattern % (i + 1),
                "JPEG",
                quality=88,
                optimize=True,
            )

            if progress and (i % 10 == 0 or i == frame_count - 1):
                progress(i + 1, frame_count)

        ff = ffmpeg_path()

        # Internal 720x1280 -> fixed final 1080x1920.
        cmd = [
            ff, "-y",
            "-framerate", str(FPS),
            "-i", pattern,
            "-i", str(audio_path),
            "-vf", f"scale={OUT_W}:{OUT_H}:flags=lanczos",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        if result.returncode:
            raise RuntimeError(
                "MP4-Export fehlgeschlagen.\n\n"
                + result.stderr.decode(errors="ignore")[-3000:]
            )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import winsound
except Exception:
    winsound = None


def resource_path(relative_path):
    """Resolve bundled assets both from source and from a PyInstaller EXE."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


DEFAULT_IMAGE = resource_path("assets/schlawutzie.png")


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1080x780")
        self.minsize(920, 680)
        self.configure(bg="#101114")

        self.background_path = None
        self.audio_path = None
        self.generated_audio = None
        self.preview_frames = []
        self.preview_index = 0
        self.busy = False

        self._build_ui()
        self._load_default_image()

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TButton", padding=8)
        style.configure(
            "TLabel",
            background="#101114",
            foreground="#e8e8e8",
        )
        style.configure(
            "Header.TLabel",
            background="#101114",
            foreground="#f0f0f0",
            font=("Segoe UI", 17, "bold"),
        )

        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        ttk.Label(
            root, text=APP_NAME, style="Header.TLabel"
        ).pack(anchor="w")

        ttk.Label(
            root,
            text="1080 × 1920 • 9:16 • interne Rendergröße 720 × 1280",
        ).pack(anchor="w", pady=(2, 14))

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 16))

        right = ttk.Frame(body)
        right.pack(side="right", fill="y")

        ttk.Label(left, text="Text").pack(anchor="w")

        self.text_box = tk.Text(
            left,
            height=12,
            wrap="word",
            bg="#181a1f",
            fg="#f2f2f2",
            insertbackground="white",
            relief="flat",
            padx=12,
            pady=12,
        )
        self.text_box.pack(fill="x", pady=(6, 12))
        self.text_box.insert(
            "1.0",
            "Hallo! Dies ist ein Test mit der Windows-OneCore-Stimme StefanM.",
        )

        ttk.Label(left, text="Hintergrundbild").pack(anchor="w")
        self.image_label = ttk.Label(
            left, text="Noch kein Bild gewählt."
        )
        self.image_label.pack(anchor="w", pady=4)

        ttk.Button(
            left, text="BILD LADEN", command=self.load_image
        ).pack(anchor="w", pady=(0, 12))

        ttk.Label(left, text="Audio").pack(anchor="w")
        self.audio_label = ttk.Label(
            left, text="Kein Audio geladen."
        )
        self.audio_label.pack(anchor="w", pady=4)

        audio_row = ttk.Frame(left)
        audio_row.pack(anchor="w", pady=(0, 10))

        ttk.Button(
            audio_row,
            text="STEFANM ERZEUGEN",
            command=self.generate_voice,
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            audio_row,
            text="WAV/MP3 LADEN",
            command=self.load_audio,
        ).pack(side="left")

        self.voice_status = ttk.Label(
            left,
            text="Interne Stimme: Windows OneCore / Microsoft Stefan • separater TTS-Prozess • kein Fallback",
        )
        self.voice_status.pack(anchor="w", pady=(0, 12))

        actions = ttk.Frame(left)
        actions.pack(anchor="w", pady=(8, 0))

        ttk.Button(
            actions, text="VORSCHAU", command=self.preview
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            actions, text="MP4 SPEICHERN", command=self.export
        ).pack(side="left")

        self.progress = ttk.Progressbar(
            left, mode="determinate"
        )
        self.progress.pack(fill="x", pady=(14, 4))

        self.status = ttk.Label(left, text="Bereit.")
        self.status.pack(anchor="w")

        ttk.Label(right, text="Vorschau").pack(anchor="w")

        self.preview_canvas = tk.Canvas(
            right,
            width=360,
            height=640,
            bg="black",
            highlightthickness=0,
        )
        self.preview_canvas.pack(pady=6)

        self.preview_photo = None

    def _load_default_image(self):
        """Load the bundled SchlauWutzie K.I. background when available."""
        try:
            if DEFAULT_IMAGE.exists():
                self.background_path = str(DEFAULT_IMAGE)
                self.image_label.config(text="Standardbild: SchlauWutzie K.I.")
                self.show_static_preview()
        except Exception:
            self.background_path = None

    def set_status(self, text):
        self.status.config(text=text)
        self.update_idletasks()

    def load_image(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("Bilder", "*.png *.jpg *.jpeg *.webp"),
                ("Alle Dateien", "*.*"),
            ]
        )
        if not path:
            return

        self.background_path = path
        self.image_label.config(text=os.path.basename(path))
        self.show_static_preview()

    def load_audio(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("Audio", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg"),
                ("Alle Dateien", "*.*"),
            ]
        )
        if not path:
            return

        try:
            wav = audio_to_wav(path)
            self.audio_path = wav
            self.audio_label.config(
                text=f"Audio: {os.path.basename(path)}"
            )
            self.set_status("Audio geladen.")
        except Exception as exc:
            messagebox.showerror("Audio", str(exc))

    def generate_voice(self):
        if self.busy:
            return

        text = self.text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning(
                "Text fehlt",
                "Bitte zuerst Text eingeben.",
            )
            return

        self.busy = True
        self.set_status("StefanM wird erzeugt …")

        threading.Thread(
            target=self._generate_voice_worker,
            args=(text,),
            daemon=True,
        ).start()

    def _generate_voice_worker(self, text):
        try:
            def status_callback(message):
                self.after(
                    0,
                    lambda m=message: self.set_status(m),
                )

            path = synthesize_stefan(
                text,
                status_callback=status_callback,
            )
            self.after(0, lambda: self._voice_done(path))
        except Exception as exc:
            self.after(
                0,
                lambda: self._voice_error(str(exc)),
            )

    def _voice_done(self, path):
        self.busy = False
        self.generated_audio = path
        self.audio_path = path
        self.audio_label.config(text="Audio: StefanM.wav")
        self.set_status("StefanM-Audio fertig.")

    def _voice_error(self, message):
        self.busy = False
        self.set_status("StefanM nicht verfügbar.")
        messagebox.showerror("StefanM", message)

    def show_static_preview(self):
        if not self.background_path:
            return

        image = fit_cover(
            Image.open(self.background_path),
            (360, 640),
        )

        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(
            180, 320, image=self.preview_photo
        )

    def preview(self):
        if not self.background_path:
            messagebox.showwarning(
                "Bild fehlt",
                "Bitte zuerst ein Hintergrundbild laden.",
            )
            return

        if not self.audio_path:
            messagebox.showwarning(
                "Audio fehlt",
                "Bitte StefanM erzeugen oder WAV/MP3 laden.",
            )
            return

        self.set_status("Vorschau wird vorbereitet …")

        threading.Thread(
            target=self._preview_worker,
            daemon=True,
        ).start()

    def _preview_worker(self):
        try:
            bg = fit_cover(
                Image.open(self.background_path),
                (W, H),
            )

            # Short preview: first up to 8 seconds.
            rate, data = read_audio_pcm(self.audio_path)
            duration = min(8.0, len(data) / rate)
            count = max(1, int(duration * FPS))
            _, amps = amplitude_curve(
                self.audio_path,
                count,
            )

            frames = []
            for i in range(count):
                frame = make_frame(
                    bg,
                    float(amps[i]),
                    i / FPS,
                )
                frame = frame.resize(
                    (360, 640),
                    Image.Resampling.LANCZOS,
                )
                frames.append(frame)

            self.after(
                0,
                lambda: self._start_preview(frames),
            )

        except Exception as exc:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Vorschau", str(exc)
                ),
            )

    def _start_preview(self, frames):
        self.preview_frames = frames
        self.preview_index = 0

        if winsound and self.audio_path:
            try:
                winsound.PlaySound(
                    self.audio_path,
                    winsound.SND_FILENAME | winsound.SND_ASYNC,
                )
            except Exception:
                pass

        self.set_status("Vorschau läuft …")
        self._animate_preview()

    def _animate_preview(self):
        if not self.preview_frames:
            return

        frame = self.preview_frames[self.preview_index]
        self.preview_photo = ImageTk.PhotoImage(frame)

        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(
            180, 320, image=self.preview_photo
        )

        self.preview_index += 1

        if self.preview_index < len(self.preview_frames):
            self.after(
                1000 // FPS,
                self._animate_preview,
            )
        else:
            if winsound:
                try:
                    winsound.PlaySound(None, 0)
                except Exception:
                    pass
            self.set_status("Vorschau fertig.")

    def export(self):
        if self.busy:
            return

        if not self.background_path:
            messagebox.showwarning(
                "Bild fehlt",
                "Bitte zuerst ein Hintergrundbild laden.",
            )
            return

        if not self.audio_path:
            messagebox.showwarning(
                "Audio fehlt",
                "Bitte StefanM erzeugen oder WAV/MP3 laden.",
            )
            return

        output = filedialog.asksaveasfilename(
            defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4")],
            initialfile="SchlauWutzie_V20_FINAL.mp4",
        )

        if not output:
            return

        self.busy = True
        self.progress["value"] = 0
        self.set_status("MP4 wird gerendert …")

        threading.Thread(
            target=self._export_worker,
            args=(output,),
            daemon=True,
        ).start()

    def _export_worker(self, output):
        try:
            def progress(done, total):
                value = 100.0 * done / max(1, total)
                self.after(
                    0,
                    lambda v=value: self.progress.configure(
                        value=v
                    ),
                )

            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            render_video(
                self.background_path,
                self.audio_path,
                output,
                progress,
            )
            if not output_path.exists() or output_path.stat().st_size < 1024:
                raise RuntimeError("FFmpeg meldete Erfolg, aber die MP4-Datei ist nicht gültig.")

            self.after(
                0,
                lambda: self._export_done(output),
            )

        except Exception as exc:
            self.after(
                0,
                lambda: self._export_error(str(exc)),
            )

    def _export_done(self, output):
        self.busy = False
        self.progress["value"] = 100
        self.set_status("MP4 fertig.")
        messagebox.showinfo(
            "Fertig",
            f"MP4 gespeichert:\n{output}",
        )

    def _export_error(self, message):
        self.busy = False
        self.set_status("Export fehlgeschlagen.")
        messagebox.showerror("MP4-Export", message)


if __name__ == "__main__":
    App().mainloop()
