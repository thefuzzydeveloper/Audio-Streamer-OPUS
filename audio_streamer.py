import os
import sys

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

# ---------------------------------------------------------
# Single Instance Guard (Win32 Named Mutex)
# ---------------------------------------------------------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CreateMutexW = kernel32.CreateMutexW
CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
CreateMutexW.restype = wintypes.HANDLE

MUTEX_NAME = "Local\\AndroidAudioStreamer_SingleInstance_Mutex_9921"
_SINGLE_INSTANCE_MUTEX = CreateMutexW(None, False, MUTEX_NAME)
if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

import queue
import re
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
PORT = 12345
TARGET_SAMPLE_RATE = 48000
OPUS_FRAME_DURATION_MS = 20  # 20ms = 960 samples @ 48kHz
SAMPLES_PER_FRAME = int(TARGET_SAMPLE_RATE * (OPUS_FRAME_DURATION_MS / 1000.0))
BYTES_PER_OPUS_FRAME = SAMPLES_PER_FRAME * 2 * 2  # 960 frames * 2 channels * 2 bytes
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


def get_android_wifi_ip() -> str:
    try:
        res = subprocess.run(
            ["adb", "shell", "ip route"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        for line in res.stdout.splitlines():
            if "wlan" in line and "src" in line:
                parts = line.split()
                if "src" in parts:
                    return parts[parts.index("src") + 1]

        res = subprocess.run(
            ["adb", "shell", "ip addr show wlan0"],
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", res.stdout)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


class AudioStreamer:
    def __init__(self, port=PORT, target_rate=TARGET_SAMPLE_RATE):
        self.port = port
        self.target_rate = target_rate
        self.is_paused = False
        self.is_running = False
        self.status_text = "Idle"

        self._stop_event = threading.Event()
        self._worker_thread = None
        self._sender_thread = None
        self._packet_queue = queue.Queue(maxsize=16)

        self.player_proc = None
        self.udp_sock = None
        self.audio_interface = None
        self.stream = None
        self.encoder = None

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
        self._cleanup()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
        if self._sender_thread and self._sender_thread.is_alive():
            self._sender_thread.join(timeout=1.0)
        self.status_text = "Stopped"

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self.status_text = "Paused" if self.is_paused else "Streaming"

    def _cleanup(self):
        self.is_running = False

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

        if self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:
                pass
            self.udp_sock = None

        if self.player_proc:
            try:
                self.player_proc.kill()
            except Exception:
                pass
            self.player_proc = None

        while not self._packet_queue.empty():
            try:
                self._packet_queue.get_nowait()
            except queue.Empty:
                break

        subprocess.run(
            ["adb", "shell", "killall -9 audio_player 2>/dev/null; exit 0"],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def _network_sender_loop(self, target_ip):
        while not self._stop_event.is_set():
            try:
                payload = self._packet_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                if self.udp_sock:
                    self.udp_sock.sendto(payload, (target_ip, self.port))
            except Exception:
                pass

    def _run_streamer(self):
        try:
            self.status_text = "Locating Phone..."
            phone_ip = get_android_wifi_ip()
            if not phone_ip:
                self.status_text = "Error: Wi-Fi IP not found"
                return

            self.status_text = "Starting Engine..."
            subprocess.run(
                ["adb", "shell", "killall -9 audio_player 2>/dev/null; exit 0"],
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            self.player_proc = subprocess.Popen(
                ["adb", "shell", f"/data/local/tmp/audio_player {self.port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            time.sleep(0.4)

            self.encoder = opuslib.Encoder(
                self.target_rate, 2, opuslib.APPLICATION_AUDIO
            )
            self.encoder.bitrate = OPUS_BITRATE

            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)

            self._sender_thread = threading.Thread(
                target=self._network_sender_loop, args=(phone_ip,), daemon=True
            )
            self._sender_thread.start()

            self.audio_interface = pyaudio.PyAudio()
            wasapi_info = self.audio_interface.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = self.audio_interface.get_device_info_by_index(
                wasapi_info["defaultOutputDevice"]
            )

            loopback_device = None
            if not default_speakers["isLoopbackDevice"]:
                for loopback in self.audio_interface.get_loopback_device_info_generator():
                    if default_speakers["name"] in loopback["name"]:
                        loopback_device = loopback
                        break
                if not loopback_device:
                    loopback_device = self.audio_interface.get_default_wasapi_loopback()
            else:
                loopback_device = default_speakers

            in_rate = int(loopback_device["defaultSampleRate"])
            in_channels = loopback_device["maxInputChannels"]

            # Vectorized Matrix Setup for Multichannel Downmixing
            if in_channels == 1:
                downmix_matrix = np.array([[1.0, 1.0]], dtype=np.float32)
            elif in_channels == 2:
                downmix_matrix = None  # Direct stereo pass-through
            elif in_channels == 6:
                # 5.1 Downmixing Matrix
                downmix_matrix = np.array([
                    [0.5,    0.0],     # FL
                    [0.0,    0.5],     # FR
                    [0.3535, 0.3535],  # FC (0.5 * 0.707)
                    [0.5,    0.5],     # LFE
                    [0.3535, 0.0],     # SL
                    [0.0,    0.3535],  # SR
                ], dtype=np.float32)
            elif in_channels == 8:
                # 7.1 Downmixing Matrix
                downmix_matrix = np.array([
                    [0.45,   0.0],     # FL
                    [0.0,    0.45],    # FR
                    [0.318,  0.318],   # FC (0.45 * 0.707)
                    [0.45,   0.45],    # LFE
                    [0.318,  0.0],     # SL
                    [0.0,    0.318],   # SR
                    [0.318,  0.0],     # BL
                    [0.0,    0.318],   # BR
                ], dtype=np.float32)
            else:
                downmix_matrix = "slice"

            pcm_accumulator = bytearray()
            phase_acc = 0.0
            prev_samples = np.zeros((1, 2), dtype=np.float32)

            def audio_callback(in_data, frame_count, time_info, status):
                nonlocal phase_acc, prev_samples, pcm_accumulator

                if self._stop_event.is_set():
                    return (None, pyaudio.paAbort)

                if self.is_paused:
                    return (None, pyaudio.paContinue)

                try:
                    audio_data = np.frombuffer(in_data, dtype=np.float32)
                    if audio_data.size == 0:
                        return (None, pyaudio.paContinue)

                    audio_data = audio_data.reshape(-1, in_channels)

                    # 1. Fast Vectorized Downmix
                    if downmix_matrix is None:
                        stereo = audio_data
                    elif downmix_matrix == "slice":
                        stereo = audio_data[:, :2]
                    else:
                        stereo = audio_data @ downmix_matrix

                    # 2. Resampling (Bypassed if native 48kHz)
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

                    # 3. Direct Fast Vectorized PCM Conversion
                    stereo_clamped = np.clip(stereo_resampled, -1.0, 1.0)
                    pcm_bytes = (stereo_clamped * 32767.0).astype(np.int16).tobytes()

                    # 4. Opus Encoding & Frame Dispatch
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
                    return (None, pyaudio.paAbort)

                return (None, pyaudio.paContinue)

            self.stream = self.audio_interface.open(
                format=pyaudio.paFloat32,
                channels=in_channels,
                rate=in_rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=1024,
                stream_callback=audio_callback,
            )

            self.is_running = True
            self.status_text = f"Streaming to {phone_ip}"
            self.stream.start_stream()

            while self.stream.is_active() and not self._stop_event.is_set():
                self._stop_event.wait(timeout=0.5)

        except Exception as e:
            self.status_text = f"Error: {e}"
        finally:
            self._cleanup()


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

    # Outer badge frame
    draw.rounded_rectangle([2, 2, 62, 62], radius=14, fill=(18, 22, 32, 255), outline=fill, width=3)

    # Mini speaker geometry
    draw.rectangle([12, 24, 22, 40], fill=fill)
    draw.polygon([(22, 24), (36, 13), (36, 51), (22, 40)], fill=fill)

    # Radiating acoustic arcs
    draw.arc([26, 21, 46, 43], start=-50, end=50, fill=fill, width=3)
    draw.arc([22, 13, 56, 51], start=-50, end=50, fill=fill, width=3)
    return image


# Pre-render icons at startup to avoid runtime draw calls
CACHED_ICONS = {
    "green": _render_icon("green"),
    "yellow": _render_icon("yellow"),
    "red": _render_icon("red"),
}


class TrayApp:
    def __init__(self, streamer: AudioStreamer):
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

        # Update only on actual state transition
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
    streamer = AudioStreamer()
    app = TrayApp(streamer)
    app.run()