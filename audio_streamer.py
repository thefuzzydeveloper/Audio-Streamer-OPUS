import os
import sys
import traceback, threading

# Register DLL directory for both standard Python and PyInstaller onefile bundles
_BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if hasattr(os, "add_dll_directory") and os.path.isdir(_BASE_DIR):
    try:
        os.add_dll_directory(_BASE_DIR)
    except Exception:
        pass
os.environ["PATH"] = _BASE_DIR + os.pathsep + os.environ.get("PATH", "")

import ctypes
from ctypes import wintypes

# -----------------------------------------------------------------------------
# Crash & Diagnostic Modal Handler
# -----------------------------------------------------------------------------
def show_fatal_crash_dialog(title: str, error_msg: str):
    """Displays a native, modal Windows error dialog with the exhaustive stack trace."""
    user32 = ctypes.windll.user32
    # MB_OK (0x0) | MB_ICONERROR (0x10) | MB_SYSTEMMODAL (0x1000) | MB_SETFOREGROUND (0x10000)
    flags = 0x00000000 | 0x00000010 | 0x00001000 | 0x00010000
    user32.MessageBoxW(0, error_msg, title, flags)


def handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    """Global exception hook catching all uncaught crashes across main and worker threads."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
    full_traceback = "".join(tb_lines)

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
if hasattr(threading, "excepthook"):
    threading.excepthook = lambda args: handle_unhandled_exception(
        args.exc_type, args.exc_value, args.exc_traceback
    )

# -----------------------------------------------------------------------------
# Single Instance Guard (Win32 Named Mutex)
# -----------------------------------------------------------------------------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CreateMutexW = kernel32.CreateMutexW
CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
CreateMutexW.restype = wintypes.HANDLE

MUTEX_NAME = "Local\\AndroidAudioStreamer_CleanStream_9928"
_SINGLE_INSTANCE_MUTEX = CreateMutexW(None, False, MUTEX_NAME)
if kernel32.GetLastError() == 183:
    sys.exit(0)

import queue
import socket
import subprocess
import threading
import time
import winreg
import numpy as np
import opuslib
import pyaudiowpatch as pyaudio
import pystray
from PIL import Image, ImageDraw

APP_NAME = "AndroidAudioStreamer"
DEFAULT_AUDIO_PORT = 12345
TARGET_SAMPLE_RATE = 48000
OPUS_FRAME_DURATION_MS = 20  # 20ms = 960 samples @ 48kHz
SAMPLES_PER_FRAME = int(TARGET_SAMPLE_RATE * (OPUS_FRAME_DURATION_MS / 1000.0))
BYTES_PER_OPUS_FRAME = SAMPLES_PER_FRAME * 2 * 2
OPUS_BITRATE = 128000


def is_startup_enabled() -> bool:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False


def set_startup(enable: bool):
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    if enable:
        exe_path = sys.executable
        script_path = os.path.abspath(sys.argv[0])
        command = f'"{exe_path}" "{script_path}"'
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    else:
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


def get_single_broadcast_target():
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
    return list(subnets) if subnets else ["255.255.255.255"]


class MultiDeviceAudioStreamer:
    def __init__(self, port=DEFAULT_AUDIO_PORT, target_rate=TARGET_SAMPLE_RATE):
        self.port = port
        self.target_rate = target_rate
        self.is_paused = False
        self.is_running = False
        self.status_text = "Idle"

        self._stop_event = threading.Event()
        self._worker_thread = None
        self._sender_thread = None
        self._packet_queue = queue.Queue(maxsize=32)

        self.udp_sock = None
        self.audio_interface = None
        self.stream = None
        self.encoder = None

        self._current_device_name = ""

    def start(self):
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._run_streamer, daemon=True)
        self._worker_thread.start()

    def restart(self):
        self.stop()
        time.sleep(0.3)
        self.start()

    def stop(self):
        self._stop_event.set()
        self._cleanup_audio_session()
        self._cleanup_network()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
        if self._sender_thread and self._sender_thread.is_alive():
            self._sender_thread.join(timeout=1.0)
        self.status_text = "Stopped"

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self.status_text = "Paused" if self.is_paused else f"Broadcasting ({self._current_device_name})"

    def _cleanup_audio_session(self):
        """Cleanly releases sound capture handles so new devices can bind immediately."""
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if self.audio_interface:
            try:
                self.audio_interface.terminate()
            except Exception:
                pass
            self.audio_interface = None

    def _cleanup_network(self):
        self.is_running = False
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:
                pass
            self.udp_sock = None

        while not self._packet_queue.empty():
            try:
                self._packet_queue.get_nowait()
            except queue.Empty:
                break

    def _network_sender_loop(self):
        targets = get_single_broadcast_target()
        last_refresh = time.time()

        while not self._stop_event.is_set():
            try:
                payload = self._packet_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if self.is_paused or not self.udp_sock:
                continue

            if time.time() - last_refresh > 10.0:
                targets = get_single_broadcast_target()
                last_refresh = time.time()

            for target_ip in targets:
                try:
                    self.udp_sock.sendto(payload, (target_ip, self.port))
                except Exception:
                    pass

    def _find_default_loopback_device(self, p_audio):
        """Resolves the currently active Windows default WASAPI playback device and its loopback."""
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

    def _run_streamer(self):
        try:
            # Terminate any conflicting standalone binary on USB devices
            subprocess.run(
                ["adb", "shell", "killall -9 audio_player 2>/dev/null; exit 0"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            self.encoder = opuslib.Encoder(
                self.target_rate, 2, opuslib.APPLICATION_AUDIO
            )
            self.encoder.bitrate = OPUS_BITRATE

            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            self.udp_sock.setblocking(False)

            self._sender_thread = threading.Thread(
                target=self._network_sender_loop, daemon=True
            )
            self._sender_thread.start()

            # -----------------------------------------------------------------
            # Self-Recovery Loop: Automatically adapts to sound device changes
            # -----------------------------------------------------------------
            while not self._stop_event.is_set():
                self._cleanup_audio_session()

                try:
                    self.audio_interface = pyaudio.PyAudio()
                    default_speakers, loopback_device = self._find_default_loopback_device(self.audio_interface)
                except Exception as dev_err:
                    self.status_text = "Searching for audio device..."
                    self._cleanup_audio_session()
                    time.sleep(1.0)
                    continue

                clean_name = default_speakers["name"].split("(")[0].strip()
                self._current_device_name = clean_name
                active_device_index = default_speakers["index"]

                in_rate = int(loopback_device["defaultSampleRate"])
                in_channels = loopback_device["maxInputChannels"]

                # Vectorized Matrix Setup for Multichannel Downmixing
                if in_channels == 1:
                    downmix_matrix = np.array([[1.0, 1.0]], dtype=np.float32)
                elif in_channels == 2:
                    downmix_matrix = None
                elif in_channels == 6:
                    downmix_matrix = np.array([
                        [0.5,    0.0],
                        [0.0,    0.5],
                        [0.3535, 0.3535],
                        [0.5,    0.5],
                        [0.3535, 0.0],
                        [0.0,    0.3535],
                    ], dtype=np.float32)
                elif in_channels == 8:
                    downmix_matrix = np.array([
                        [0.45,   0.0],
                        [0.0,    0.45],
                        [0.318,  0.318],
                        [0.45,   0.45],
                        [0.318,  0.0],
                        [0.0,    0.318],
                        [0.318,  0.0],
                        [0.0,    0.318],
                    ], dtype=np.float32)
                else:
                    downmix_matrix = "slice"

                pcm_accumulator = bytearray()
                phase_acc = 0.0
                prev_samples = np.zeros((1, 2), dtype=np.float32)
                stream_failed_event = threading.Event()

                def audio_callback(in_data, frame_count, time_info, status):
                    nonlocal phase_acc, prev_samples, pcm_accumulator

                    if self._stop_event.is_set() or stream_failed_event.is_set():
                        return (None, pyaudio.paAbort)

                    if self.is_paused:
                        if len(pcm_accumulator) > 0:
                            pcm_accumulator.clear()
                        return (None, pyaudio.paContinue)

                    try:
                        audio_data = np.frombuffer(in_data, dtype=np.float32)
                        if audio_data.size == 0:
                            return (None, pyaudio.paContinue)

                        audio_data = audio_data.reshape(-1, in_channels)

                        if downmix_matrix is None:
                            stereo = audio_data
                        elif downmix_matrix == "slice":
                            stereo = audio_data[:, :2]
                        else:
                            stereo = audio_data @ downmix_matrix

                        if in_rate != self.target_rate:
                            ext_stereo = np.vstack((prev_samples, stereo))
                            step = in_rate / self.target_rate
                            num_out = int(np.floor((len(stereo) - phase_acc) / step))

                            if num_out > 0:
                                indices = phase_acc + np.arange(num_out, dtype=np.float32) * step
                                i_floor = indices.astype(np.int32)
                                frac = (indices - i_floor).reshape(-1, 1)

                                stereo_resampled = (1.0 - frac) * ext_stereo[i_floor] + frac * ext_stereo[i_floor + 1]
                                phase_acc = float(indices[-1] + step - len(stereo))
                            else:
                                phase_acc -= len(stereo)
                                return (None, pyaudio.paContinue)

                            prev_samples = ext_stereo[-1:]
                        else:
                            stereo_resampled = stereo

                        stereo_clamped = np.clip(stereo_resampled, -1.0, 1.0)
                        pcm_bytes = (stereo_clamped * 32767.0).astype(np.int16).tobytes()
                        pcm_accumulator.extend(pcm_bytes)

                        while len(pcm_accumulator) >= BYTES_PER_OPUS_FRAME:
                            frame_bytes = bytes(pcm_accumulator[:BYTES_PER_OPUS_FRAME])
                            del pcm_accumulator[:BYTES_PER_OPUS_FRAME]

                            encoded_packet = self.encoder.encode(frame_bytes, SAMPLES_PER_FRAME)

                            try:
                                self._packet_queue.put_nowait(encoded_packet)
                            except queue.Full:
                                try:
                                    self._packet_queue.get_nowait()
                                    self._packet_queue.put_nowait(encoded_packet)
                                except (queue.Empty, queue.Full):
                                    pass

                    except Exception:
                        stream_failed_event.set()
                        return (None, pyaudio.paAbort)

                    return (None, pyaudio.paContinue)

                try:
                    self.stream = self.audio_interface.open(
                        format=pyaudio.paFloat32,
                        channels=in_channels,
                        rate=in_rate,
                        input=True,
                        input_device_index=loopback_device["index"],
                        frames_per_buffer=1024,
                        stream_callback=audio_callback,
                    )
                except Exception as open_err:
                    self.status_text = "Device busy, switching..."
                    time.sleep(0.8)
                    continue

                self.is_running = True
                self.status_text = f"Broadcasting ({clean_name})"
                self.stream.start_stream()

                # Stream Monitor & Hot-Plug Watchdog Loop
                while self.stream.is_active() and not self._stop_event.is_set() and not stream_failed_event.is_set():
                    # Periodically check if Windows changed default audio output device
                    try:
                        wasapi_check = self.audio_interface.get_host_api_info_by_type(pyaudio.paWASAPI)
                        new_default_index = wasapi_check.get("defaultOutputDevice", -1)
                        if new_default_index != -1 and new_default_index != active_device_index:
                            self.status_text = "Sound device changed, adapting..."
                            break  # Trigger clean recovery and switch to new device
                    except Exception:
                        break

                    if self.is_paused:
                        self.status_text = "Paused"
                    else:
                        self.status_text = f"Broadcasting ({clean_name})"

                    self._stop_event.wait(timeout=0.8)

        except Exception:
            # Let uncaught exceptions bubble up to show_fatal_crash_dialog
            raise
        finally:
            self._cleanup_audio_session()
            self._cleanup_network()


def _render_icon(color: str) -> Image.Image:
    size = (64, 64)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colors = {
        "green": "#2ECC71",
        "yellow": "#F1C40F",
        "red": "#E74C3C",
    }
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
    def __init__(self, streamer: MultiDeviceAudioStreamer):
        self.streamer = streamer
        self.icon = None
        self._last_icon_color = None
        self._last_status_text = None

    def on_pause_toggle(self, icon, item):
        self.streamer.toggle_pause()
        self.update_tray()

    def on_restart(self, icon, item):
        self.streamer.restart()
        self.update_tray()

    def on_toggle_startup(self, icon, item):
        set_startup(not is_startup_enabled())

    def on_exit(self, icon, item):
        self.streamer.stop()
        icon.stop()

    def update_tray(self):
        if not self.icon:
            return

        if not self.streamer.is_running:
            color = "red"
        elif self.streamer.is_paused:
            color = "yellow"
        else:
            color = "green"

        status = self.streamer.status_text

        if color != self._last_icon_color:
            self.icon.icon = CACHED_ICONS[color]
            self._last_icon_color = color

        if status != self._last_status_text:
            self.icon.title = f"Android Audio Streamer ({status})"
            self._last_status_text = status

    def run(self):
        self.streamer.start()

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda text: f"Status: {self.streamer.status_text}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Pause Streaming",
                self.on_pause_toggle,
                checked=lambda item: self.streamer.is_paused,
            ),
            pystray.MenuItem("Restart Stream", self.on_restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows",
                self.on_toggle_startup,
                checked=lambda item: is_startup_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.on_exit),
        )

        self.icon = pystray.Icon(
            APP_NAME,
            icon=CACHED_ICONS["green"],
            title=f"Android Audio Streamer ({self.streamer.status_text})",
            menu=menu,
        )

        def monitor_loop():
            while self.icon.visible:
                self.update_tray()
                time.sleep(1.0)

        threading.Thread(target=monitor_loop, daemon=True).start()
        self.icon.run()


if __name__ == "__main__":
    streamer = MultiDeviceAudioStreamer()
    app = TrayApp(streamer)
    app.run()