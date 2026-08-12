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
from PIL import Image, ImageDraw, ImageFilter, ImageTk

try:
    from imageio_ffmpeg import get_ffmpeg_exe
except Exception:
    get_ffmpeg_exe = None

# IMPORTANT:
# Windows OneCore is the ONLY internal TTS path in V15.
# There is deliberately NO pyttsx3 fallback, so a female voice can never
# silently replace StefanM.
try:
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


def make_frame(background, amplitude, time_s):
    frame = background.copy().convert("RGBA")

    # Bottom darkening for a restrained, readable visualizer area.
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for y in range(H - 430, H):
        rel = (y - (H - 430)) / 430.0
        alpha = int(145 * (rel ** 1.7))
        draw.line((0, y, W, y), fill=(0, 0, 0, alpha))

    frame = Image.alpha_composite(frame, overlay)

    visual = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(visual)

    base_y = H - 205

    # Flat glass/liquid surface: no large waves.
    surface_strength = 4 + int(16 * amplitude)
    points = []

    for x in range(0, W + 1, 8):
        wave = math.sin(x * 0.035 + time_s * 2.0) * surface_strength
        wave += math.sin(x * 0.012 - time_s * 1.3) * surface_strength * 0.35
        points.append((x, base_y + wave))

    d.line(points, fill=(220, 240, 255, 105), width=2)

    # Fine light trail reacts to speech volume.
    trail_y = base_y + 55
    d.line(
        (70, trail_y, W - 70, trail_y),
        fill=(225, 245, 255, int(65 + 140 * amplitude)),
        width=max(2, int(3 + 8 * amplitude)),
    )

    # Small particles; restrained rather than an equalizer/bar graph.
    for i in range(58):
        x = ((i * 137.17) + time_s * (8 + i % 5) * (0.2 + amplitude))
        x = x % (W - 60) + 30

        y0 = base_y + 35 + ((i * 53.7) % 145)
        rise = (20 + 95 * amplitude) * (((i * 17) % 100) / 100.0)
        y = y0 - rise

        radius = 1 + int(2.5 * amplitude * (0.4 + ((i * 7) % 10) / 10))
        alpha = int(40 + 170 * amplitude)

        d.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(230, 246, 255, alpha),
        )

    visual = visual.filter(ImageFilter.GaussianBlur(4 + int(3 * amplitude)))
    return Image.alpha_composite(frame, visual).convert("RGB")


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
