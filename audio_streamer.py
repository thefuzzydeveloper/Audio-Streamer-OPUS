import os, sys

_BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if hasattr(os, "add_dll_directory") and os.path.isdir(_BASE_DIR):
    try:
        os.add_dll_directory(_BASE_DIR)
    except Exception:
        pass
os.environ["PATH"] = _BASE_DIR + os.pathsep + os.environ.get("PATH", "")

import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CreateMutexW = kernel32.CreateMutexW
CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
CreateMutexW.restype = wintypes.HANDLE

MUTEX_NAME = "Local\\AndroidAudioStreamer_Clean_9927"
_SINGLE_INSTANCE_MUTEX = CreateMutexW(None, False, MUTEX_NAME)
if kernel32.GetLastError() == 183:
    sys.exit(0)

import queue, socket, subprocess,threading, time, winreg, numpy as np, opuslib, pyaudiowpatch as pyaudio, pystray
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
    """Identifies the single best subnet broadcast address to eliminate packet multiplication/echo."""
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
        self._packet_queue = queue.Queue(maxsize=8)

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
        self.status_text = "Paused" if self.is_paused else "Broadcasting"

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

    def _run_streamer(self):
        try:
            # Prevent double-audio: kill any rogue standalone binary on USB devices
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
            self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
            self.udp_sock.setblocking(False)

            self._sender_thread = threading.Thread(
                target=self._network_sender_loop, daemon=True
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

            def audio_callback(in_data, frame_count, time_info, status):
                nonlocal phase_acc, prev_samples, pcm_accumulator

                if self._stop_event.is_set():
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
            self.status_text = "Broadcasting"
            self.stream.start_stream()

            while self.stream.is_active() and not self._stop_event.is_set():
                if self.is_paused:
                    self.status_text = "Paused"
                else:
                    self.status_text = "Broadcasting"
                self._stop_event.wait(timeout=0.8)

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