import os
import sys
import ctypes
from ctypes import wintypes
import ctypes.util
import struct

# -----------------------------------------------------------------------------
# Robust Windows DLL & Opus Path Resolution (Runs in all spawned processes)
# -----------------------------------------------------------------------------
def _resolve_and_preload_opus():
    candidate_dirs = []

    # 1. PyInstaller single-file temp extraction dir
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(meipass):
        candidate_dirs.append(meipass)

    # 2. Executable parent directory (standard onedir / standalone)
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.isdir(exe_dir):
        candidate_dirs.append(exe_dir)

    # 3. PyInstaller 6+ "_internal" subdirectory
    internal_dir = os.path.join(exe_dir, "_internal")
    if os.path.isdir(internal_dir):
        candidate_dirs.append(internal_dir)

    # 4. Script directory (for raw Python development)
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.isdir(script_dir):
            candidate_dirs.append(script_dir)
    except NameError:
        pass

    # 5. Current working directory
    candidate_dirs.append(os.getcwd())

    unique_dirs = []
    for d in candidate_dirs:
        norm = os.path.normpath(d)
        if norm not in unique_dirs and os.path.isdir(norm):
            unique_dirs.append(norm)

    for d in unique_dirs:
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
        try:
            ctypes.windll.kernel32.SetDllDirectoryW(d)
        except Exception:
            pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    dll_names = ("opus.dll", "libopus-0.dll", "libopus.dll", "opus-0.dll")
    resolved_opus_path = None

    for directory in unique_dirs:
        for name in dll_names:
            full_path = os.path.join(directory, name)
            if os.path.isfile(full_path):
                resolved_opus_path = full_path
                break
        if resolved_opus_path:
            break

    _orig_find_library = ctypes.util.find_library

    if resolved_opus_path:
        try:
            ctypes.cdll.LoadLibrary(resolved_opus_path)
        except Exception:
            pass

        def _patched_find_library(name):
            if name in ("opus", "libopus-0", "libopus", "opus-0"):
                return resolved_opus_path
            return _orig_find_library(name)

        ctypes.util.find_library = _patched_find_library
    else:
        def _patched_find_library(name):
            if name in ("opus", "libopus-0", "libopus", "opus-0"):
                for d in unique_dirs:
                    for n in dll_names:
                        p = os.path.join(d, n)
                        if os.path.isfile(p):
                            return p
            return _orig_find_library(name)

        ctypes.util.find_library = _patched_find_library

_resolve_and_preload_opus()

# -----------------------------------------------------------------------------
# Core Imports & Process Priority Configuration
# -----------------------------------------------------------------------------
import traceback
import threading
import multiprocessing as mp
import queue
import socket
import subprocess
import time
import winreg
import tkinter as tk
from tkinter import ttk
import numpy as np
import opuslib
import pyaudiowpatch as pyaudio
import pystray
from PIL import Image, ImageDraw

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)

def apply_realtime_priority():
    try:
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00000080)  # HIGH_PRIORITY_CLASS
        winmm.timeBeginPeriod(1)
    except Exception:
        pass

APP_NAME = "AndroidAudioStreamer"
DEFAULT_AUDIO_PORT = 12345
TARGET_SAMPLE_RATE = 48000
OPUS_FRAME_DURATION_MS = 20  # 20ms = 960 samples @ 48kHz
SAMPLES_PER_FRAME = int(TARGET_SAMPLE_RATE * (OPUS_FRAME_DURATION_MS / 1000.0))
BYTES_PER_OPUS_FRAME = SAMPLES_PER_FRAME * 2 * 2  # Stereo 16-bit PCM = 3840 bytes
OPUS_BITRATE = 128000

# -----------------------------------------------------------------------------
# Crash & Diagnostic Modal Handler
# -----------------------------------------------------------------------------
def show_fatal_crash_dialog(title: str, error_msg: str):
    user32 = ctypes.windll.user32
    flags = 0x00000000 | 0x00000010 | 0x00010000
    user32.MessageBoxW(0, error_msg, title, flags)

def handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    full_traceback = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    diagnostic_report = (
        f"AN UNRECOVERABLE CRASH OCCURRED IN {APP_NAME}\n\n"
        f"Exception Type: {exc_type.__name__}\n"
        f"Exception Details: {exc_value}\n\n"
        f"--- Exhaustive Stack Trace ---\n"
        f"{full_traceback}\n"
        f"--- System Environment ---\n"
        f"Python: {sys.version}\n"
        f"Working Directory: {os.getcwd()}\n"
        f"Executable Path: {sys.executable}"
    )
    try:
        show_fatal_crash_dialog(f"{APP_NAME} - Fatal Crash Error", diagnostic_report)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_traceback)
    sys.exit(1)

sys.excepthook = handle_unhandled_exception

# -----------------------------------------------------------------------------
# Startup Registry Utilities
# -----------------------------------------------------------------------------
def is_startup_enabled() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False

def set_startup(enable: bool):
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    if enable:
        exe_path = sys.executable
        if getattr(sys, "frozen", False):
            command = f'"{exe_path}"'
        else:
            script_path = os.path.abspath(sys.argv[0])
            command = f'"{exe_path}" "{script_path}"'
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)

# -----------------------------------------------------------------------------
# Audio Engine Worker Process (Isolated from GUI / Message Loop)
# -----------------------------------------------------------------------------
def _set_status(shared_status, msg: str):
    shared_status.value = msg.encode("utf-8")[:255]

def _fast_soft_limiter(audio_samples: np.ndarray, threshold: float = 0.85) -> np.ndarray:
    abs_audio = np.abs(audio_samples)
    mask = abs_audio > threshold

    if not np.any(mask):
        return audio_samples

    out = np.copy(audio_samples)
    excess = abs_audio[mask] - threshold
    headroom = 1.0 - threshold

    compressed = threshold + headroom * np.tanh(excess / headroom)
    out[mask] = np.sign(audio_samples[mask]) * compressed
    return np.clip(out, -1.0, 1.0)

def _async_subnet_resolver(target_holder, stop_event):
    while not stop_event.is_set():
        subnets = set()
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                if not ip.startswith("127.") and not ip.startswith("169.254."):
                    parts = ip.split(".")
                    if len(parts) == 4:
                        subnets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
        except Exception:
            pass
        target_holder[0] = list(subnets) if subnets else ["255.255.255.255"]
        stop_event.wait(timeout=10.0)

def _find_default_loopback_device(p_audio):
    wasapi_info = p_audio.get_host_api_info_by_type(pyaudio.paWASAPI)
    default_output_index = wasapi_info.get("defaultOutputDevice", -1)
    if default_output_index == -1:
        raise RuntimeError("No active default WASAPI output device detected in Windows.")

    default_speakers = p_audio.get_device_info_by_index(default_output_index)
    loopback_device = None

    if not default_speakers.get("isLoopbackDevice", False):
        for loopback in p_audio.get_loopback_device_info_generator():
            if default_speakers["name"] in loopback["name"]:
                loopback_device = loopback
                break
        if not loopback_device:
            loopback_device = p_audio.get_default_wasapi_loopback()
    else:
        loopback_device = default_speakers

    if not loopback_device:
        raise RuntimeError(f"Could not initialize WASAPI loopback for '{default_speakers.get('name', 'Unknown')}'")

    return default_speakers, loopback_device

def audio_engine_worker(port, target_rate, shared_volume, shared_paused, shared_running, shared_status, stop_event, restart_event):
    apply_realtime_priority()

    try:
        subprocess.run(
            ["adb", "shell", "killall -9 audio_player 2>/dev/null; exit 0"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        pass

    try:
        encoder = opuslib.Encoder(target_rate, 2, opuslib.APPLICATION_AUDIO)
        encoder.bitrate = OPUS_BITRATE
    except Exception as e:
        _set_status(shared_status, f"Opus Init Error: {e}")
        return

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
    udp_sock.setblocking(False)

    raw_audio_queue = queue.Queue(maxsize=128)
    packet_queue = queue.Queue(maxsize=64)

    target_holder = [["255.255.255.255"]]
    resolver_thread = threading.Thread(
        target=_async_subnet_resolver, args=(target_holder, stop_event), daemon=True
    )
    resolver_thread.start()

    def network_sender_loop():
        while not stop_event.is_set():
            try:
                payload = packet_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if shared_paused.value:
                continue

            targets = target_holder[0]
            for target_ip in targets:
                try:
                    udp_sock.sendto(payload, (target_ip, port))
                except Exception:
                    pass

    sender_thread = threading.Thread(target=network_sender_loop, daemon=True)
    sender_thread.start()

    dsp_stop_event = threading.Event()

    def dsp_and_encoder_loop(in_channels, in_rate, downmix_matrix):
        pcm_accumulator = bytearray()
        phase_acc = 0.0
        prev_samples = np.zeros((1, 2), dtype=np.float32)
        sequence_number = 0

        while not dsp_stop_event.is_set() and not stop_event.is_set():
            try:
                raw_bytes = raw_audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if shared_paused.value:
                pcm_accumulator.clear()
                continue

            try:
                audio_data = np.frombuffer(raw_bytes, dtype=np.float32)
                if audio_data.size == 0:
                    continue

                audio_data = audio_data.reshape(-1, in_channels)

                if downmix_matrix is None:
                    stereo = audio_data
                elif downmix_matrix == "slice":
                    stereo = audio_data[:, :2]
                else:
                    stereo = audio_data @ downmix_matrix

                vol = float(shared_volume.value) * 0.95
                stereo = stereo * vol

                if in_rate != target_rate:
                    ext_stereo = np.vstack((prev_samples, stereo))
                    step = in_rate / target_rate
                    num_out = int(np.floor((len(stereo) - phase_acc) / step))

                    if num_out > 0:
                        indices = phase_acc + np.arange(num_out, dtype=np.float32) * step
                        i_floor = indices.astype(np.int32)
                        frac = (indices - i_floor).reshape(-1, 1)

                        stereo_resampled = (1.0 - frac) * ext_stereo[i_floor] + frac * ext_stereo[i_floor + 1]
                        phase_acc = float(indices[-1] + step - len(stereo))
                    else:
                        phase_acc -= len(stereo)
                        continue

                    prev_samples = ext_stereo[-1:]
                else:
                    stereo_resampled = stereo

                stereo_limited = _fast_soft_limiter(stereo_resampled, threshold=0.85)

                pcm_bytes = (stereo_limited * 32767.0).astype(np.int16).tobytes()
                pcm_accumulator.extend(pcm_bytes)

                while len(pcm_accumulator) >= BYTES_PER_OPUS_FRAME:
                    frame_bytes = bytes(pcm_accumulator[:BYTES_PER_OPUS_FRAME])
                    del pcm_accumulator[:BYTES_PER_OPUS_FRAME]

                    encoded_payload = encoder.encode(frame_bytes, SAMPLES_PER_FRAME)

                    # Prepend 2-byte big-endian sequence number (0-65535)
                    header = struct.pack("!H", sequence_number)
                    sequence_number = (sequence_number + 1) & 0xFFFF
                    packet = header + encoded_payload

                    try:
                        packet_queue.put_nowait(packet)
                    except queue.Full:
                        try:
                            packet_queue.get_nowait()
                            packet_queue.put_nowait(packet)
                        except (queue.Empty, queue.Full):
                            pass
            except Exception:
                pass

    while not stop_event.is_set():
        restart_event.clear()
        dsp_stop_event.clear()
        p_audio = None
        stream = None
        dsp_thread = None

        try:
            p_audio = pyaudio.PyAudio()
            default_speakers, loopback_device = _find_default_loopback_device(p_audio)
        except Exception:
            shared_running.value = False
            _set_status(shared_status, "Searching for audio device...")
            if p_audio:
                try:
                    p_audio.terminate()
                except Exception:
                    pass
            time.sleep(1.0)
            continue

        clean_name = default_speakers["name"].split("(")[0].strip()
        active_device_index = default_speakers["index"]
        in_rate = int(loopback_device["defaultSampleRate"])
        in_channels = loopback_device["maxInputChannels"]

        if in_channels == 1:
            downmix_matrix = np.array([[1.0, 1.0]], dtype=np.float32)
        elif in_channels == 2:
            downmix_matrix = None
        elif in_channels == 6:
            downmix_matrix = np.array([
                [0.35, 0.00], [0.00, 0.35], [0.25, 0.25],
                [0.15, 0.15], [0.25, 0.00], [0.00, 0.25]
            ], dtype=np.float32)
        elif in_channels == 8:
            downmix_matrix = np.array([
                [0.30, 0.00], [0.00, 0.30], [0.20, 0.20], [0.10, 0.10],
                [0.20, 0.00], [0.00, 0.20], [0.20, 0.00], [0.00, 0.20]
            ], dtype=np.float32)
        else:
            downmix_matrix = "slice"

        dsp_thread = threading.Thread(
            target=dsp_and_encoder_loop,
            args=(in_channels, in_rate, downmix_matrix),
            daemon=True,
        )
        dsp_thread.start()

        def audio_callback(in_data, frame_count, time_info, status):
            if stop_event.is_set() or restart_event.is_set():
                return (None, pyaudio.paAbort)

            try:
                raw_audio_queue.put_nowait(in_data)
            except queue.Full:
                try:
                    raw_audio_queue.get_nowait()
                    raw_audio_queue.put_nowait(in_data)
                except (queue.Empty, queue.Full):
                    pass

            return (None, pyaudio.paContinue)

        try:
            stream = p_audio.open(
                format=pyaudio.paFloat32,
                channels=in_channels,
                rate=in_rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=1024,
                stream_callback=audio_callback,
            )
            stream.start_stream()
            shared_running.value = True
            _set_status(shared_status, f"Broadcasting ({clean_name})")
        except Exception:
            _set_status(shared_status, "Device busy, retrying...")
            dsp_stop_event.set()
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if p_audio:
                try:
                    p_audio.terminate()
                except Exception:
                    pass
            time.sleep(0.8)
            continue

        while stream.is_active() and not stop_event.is_set() and not restart_event.is_set():
            try:
                wasapi_check = p_audio.get_host_api_info_by_type(pyaudio.paWASAPI)
                new_default_index = wasapi_check.get("defaultOutputDevice", -1)
                if new_default_index != -1 and new_default_index != active_device_index:
                    _set_status(shared_status, "Sound device changed, adapting...")
                    break
            except Exception:
                break

            if shared_paused.value:
                _set_status(shared_status, "Paused")
            else:
                _set_status(shared_status, f"Broadcasting ({clean_name})")

            time.sleep(0.6)

        dsp_stop_event.set()
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if p_audio:
            try:
                p_audio.terminate()
            except Exception:
                pass

        if dsp_thread and dsp_thread.is_alive():
            dsp_thread.join(timeout=0.5)

        while not raw_audio_queue.empty():
            try:
                raw_audio_queue.get_nowait()
            except queue.Empty:
                break

    shared_running.value = False
    _set_status(shared_status, "Stopped")
    try:
        udp_sock.close()
    except Exception:
        pass

# -----------------------------------------------------------------------------
# Controller & System Tray GUI (Host Process)
# -----------------------------------------------------------------------------
class AudioEngineManager:
    def __init__(self, port=DEFAULT_AUDIO_PORT, target_rate=TARGET_SAMPLE_RATE):
        self.port = port
        self.target_rate = target_rate

        self.shared_volume = mp.Value("d", 1.0)
        self.shared_paused = mp.Value("b", False)
        self.shared_running = mp.Value("b", False)
        self.shared_status = mp.Array("c", 256)
        self.stop_event = mp.Event()
        self.restart_event = mp.Event()

        self.process = None
        self.set_status("Initializing...")

    @property
    def volume(self) -> float:
        return float(self.shared_volume.value)

    @volume.setter
    def volume(self, val: float):
        self.shared_volume.value = max(0.0, min(1.0, float(val)))

    @property
    def is_paused(self) -> bool:
        return bool(self.shared_paused.value)

    @property
    def is_running(self) -> bool:
        return bool(self.shared_running.value)

    @property
    def status_text(self) -> str:
        try:
            return self.shared_status.value.decode("utf-8")
        except Exception:
            return "Unknown"

    def set_status(self, text: str):
        self.shared_status.value = text.encode("utf-8")[:255]

    def start(self):
        self.stop_event.clear()
        self.restart_event.clear()
        self.process = mp.Process(
            target=audio_engine_worker,
            args=(
                self.port,
                self.target_rate,
                self.shared_volume,
                self.shared_paused,
                self.shared_running,
                self.shared_status,
                self.stop_event,
                self.restart_event,
            ),
            daemon=True,
        )
        self.process.start()

    def restart(self):
        self.restart_event.set()

    def toggle_pause(self):
        self.shared_paused.value = not self.shared_paused.value

    def stop(self):
        self.stop_event.set()
        if self.process and self.process.is_alive():
            self.process.join(timeout=2.0)
            if self.process.is_alive():
                self.process.terminate()

class VolumePopup:
    def __init__(self, manager: AudioEngineManager):
        self.manager = manager
        self.root = None
        self._lock = threading.Lock()

    def show(self):
        with self._lock:
            if self.root is not None:
                try:
                    self.root.lift()
                    self.root.focus_force()
                    return
                except Exception:
                    self.root = None

            threading.Thread(target=self._run_ui, daemon=True).start()

    def _run_ui(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1E222D", highlightthickness=1, highlightbackground="#3D4457")

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))

        width, height = 240, 80
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        x = max(10, min(pt.x - width // 2, screen_w - width - 12))
        y = max(10, min(pt.y - height - 12, screen_h - height - 48))
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        current_pct = int(round(self.manager.volume * 100))
        label_var = tk.StringVar(value=f"Volume: {current_pct}%")
        lbl = tk.Label(
            self.root, textvariable=label_var, bg="#1E222D", fg="#E0E6ED",
            font=("Segoe UI", 9, "bold")
        )
        lbl.pack(pady=(10, 4))

        def on_slider(val):
            f_val = float(val)
            self.manager.volume = f_val / 100.0
            label_var.set(f"Volume: {int(round(f_val))}%")

        slider = ttk.Scale(
            self.root, from_=0, to=100, orient=tk.HORIZONTAL,
            value=current_pct, command=on_slider
        )
        slider.pack(fill=tk.X, padx=16, pady=(0, 10))

        def on_focus_out(event):
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None

        self.root.bind("<FocusOut>", on_focus_out)
        self.root.focus_force()
        self.root.mainloop()

def _render_icon(color: str) -> Image.Image:
    size = (64, 64)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colors = {"green": "#2ECC71", "yellow": "#F1C40F", "red": "#E74C3C"}
    fill = colors.get(color, "#2ECC71")

    draw.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(18, 22, 32, 255), outline=fill, width=3)
    draw.rectangle([12, 24, 22, 40], fill=fill)
    draw.polygon([(22, 24), (36, 13), (36, 51), (22, 40)], fill=fill)
    draw.arc([26, 21, 46, 43], start=-50, end=50, fill=fill, width=3)
    draw.arc([22, 13, 56, 51], start=-50, end=50, fill=fill, width=3)
    return image

CACHED_ICONS = {
    "green": _render_icon("green"),
    "yellow": _render_icon("yellow"),
    "red": _render_icon("red"),
}

class TrayApp:
    def __init__(self, manager: AudioEngineManager):
        self.manager = manager
        self.volume_popup = VolumePopup(manager)
        self.icon = None
        self._last_icon_color = None
        self._last_status_text = None

    def on_volume_popup(self, icon, item):
        self.volume_popup.show()

    def on_pause_toggle(self, icon, item):
        self.manager.toggle_pause()
        self.update_tray()

    def on_restart(self, icon, item):
        self.manager.restart()
        self.update_tray()

    def on_toggle_startup(self, icon, item):
        set_startup(not is_startup_enabled())

    def on_exit(self, icon, item):
        self.manager.stop()
        icon.stop()

    def update_tray(self):
        if not self.icon:
            return

        if not self.manager.is_running:
            color = "red"
        elif self.manager.is_paused:
            color = "yellow"
        else:
            color = "green"

        status = self.manager.status_text

        if color != self._last_icon_color:
            self.icon.icon = CACHED_ICONS[color]
            self._last_icon_color = color

        if status != self._last_status_text:
            self.icon.title = f"Android Audio Streamer ({status})"
            self._last_status_text = status

    def run(self):
        self.manager.start()

        menu = pystray.Menu(
            pystray.MenuItem("Adjust Volume", self.on_volume_popup, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda text: f"Status: {self.manager.status_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause Streaming", self.on_pause_toggle, checked=lambda item: self.manager.is_paused),
            pystray.MenuItem("Restart Stream", self.on_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start with Windows", self.on_toggle_startup, checked=lambda item: is_startup_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.on_exit),
        )

        self.icon = pystray.Icon(
            APP_NAME,
            icon=CACHED_ICONS["green"],
            title=f"Android Audio Streamer ({self.manager.status_text})",
            menu=menu,
        )

        def monitor_loop():
            while self.icon.visible:
                self.update_tray()
                time.sleep(0.5)

        threading.Thread(target=monitor_loop, daemon=True).start()
        self.icon.run()

# -----------------------------------------------------------------------------
# Main Application Entry Point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    mp.freeze_support()

    CreateMutexW = kernel32.CreateMutexW
    CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    CreateMutexW.restype = wintypes.HANDLE

    MUTEX_NAME = "Local\\AndroidAudioStreamer_CleanStream_9928"
    _SINGLE_INSTANCE_MUTEX = CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)

    apply_realtime_priority()

    engine_manager = AudioEngineManager()
    app = TrayApp(engine_manager)
    app.run()