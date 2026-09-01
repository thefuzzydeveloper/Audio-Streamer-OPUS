**Project Overview**

This project is an ultra-low-latency, headless wireless audio streaming solution designed to capture Windows desktop audio and stream it seamlessly to an Android device over a local Wi-Fi network. By combining native C execution via Android NDK and a decoupled Python host service, the architecture eliminates the high latency, jitter stalls, and TCP head-of-line blocking typical of standard streaming apps.

---

**Core Architecture & Components**

* **Windows Host Streamer (`audio_streamer.py`)**:
* **WASAPI Loopback Capture:** Captures high-resolution system audio directly from the active Windows output endpoint using `pyaudiowpatch`.


* **Downmixing & Resampling:** Performs real-time surround sound folddown (5.1/7.1 to stereo) while preserving the low-frequency effects (LFE/bass) channel, alongside phase-continuous linear resampling to 48 kHz.


* **Opus Compression:** Encodes 20 ms PCM audio frames into high-efficiency Opus packets (~128 kbps), reducing network bandwidth consumption by over 90%.
* **Non-Blocking UDP Transmission:** Employs a producer-consumer threaded queue that discards stale frames during network congestion to maintain real-time synchronization.
* **System Tray Management:** Features background system tray controls for stream pausing, restarting, state monitoring, and automated Windows startup registration.




* **Native Android Audio Engine (`audio_player.c`)**:
* **Direct NDK Execution:** Operates as a lightweight, standalone native C binary executed via ADB (`/data/local/tmp`), bypassing standard Android application framework (APK/JVM) overhead.


* **OpenSL ES Playback:** Interfaces directly with hardware audio buffers via OpenSL ES double-buffering callbacks for deterministic, low-latency playback.


* **Opus Decompression:** Decodes incoming UDP payloads on the fly into 16-bit stereo PCM.
* **Jitter Ring Buffer:** Features a lock-protected circular buffer to absorb network latency fluctuations and prevent audio underruns.




* **Automated Build & Deployment Pipeline (`deploy.py`)**:
* Cross-compiles native C source files targeting ARM64 (`aarch64-linux-android`) using LLVM Clang with `-O3` optimizations and stripped symbols.
* Automatically handles target discovery, remote process termination, binary pushing over ADB, and execution permission provisioning.



---

**Key Technical Capabilities**

* **Ultra-Low Latency:** Optimized for sub-50 ms end-to-end delay, making it suitable for gaming, video synchronization, and real-time monitoring.
* **Network Stability:** Uses UDP and packet-loss-resilient Opus decoding to prevent buffer bloat and audio dropouts across wireless environments.
* **Headless Integration:** Runs entirely in the background on both the PC host and the target Android device without requiring GUI rendering.

# Follow along GUIDE
Deployment of the binary to the phone **is still required**, but **compilation is not** if you already have the pre-built `audio_player` binary file. The PC application executes `adb shell /data/local/tmp/audio_player <port>` in the background; therefore, the compiled `audio_player` ELF binary must physically reside inside the Android device's `/data/local/tmp/` directory.

---

### OPTION 1: COMPLETE END-USER SETUP & USAGE (RUNNING PRE-COMPILED BINARIES)

Use this method to run the pre-built application without installing the Android NDK, C compilers, or building Opus from source.

---

**Phase 1: Android Device Preparation**

1. **Unlock Developer Mode:**
* Open **Settings** on your Android device.
* Scroll down and tap **About Phone** (on some devices: **System** > **About Phone**).
* Find **Build Number** and tap it **7 times** in rapid succession until a toast notification appears: *"You are now a developer!"*


2. **Enable USB Debugging:**
* Return to the main **Settings** menu.
* Navigate to **System** > **Developer Options** (or **Additional Settings** > **Developer Options**).
* Scroll down to the **Debugging** section.
* Toggle **USB Debugging** to **ON** and confirm the safety prompt.
* *(Optional for Android 11+)* Toggle **Wireless Debugging** to **ON**.



---

**Phase 2: Establishing the Wireless ADB Connection**

Choose **Method A** (easiest, requires a USB cable for 10 seconds) or **Method B** (completely wireless, Android 11+ required).

**Method A: Quick USB Handshake (Universal for all Android versions)**

1. Connect your Android device to your PC using a USB data cable.
2. An authorization dialog will appear on your phone screen: *"Allow USB debugging?"*
* Check the box: **"Always allow from this computer"**.
* Tap **Allow**.


3. Open Windows Command Prompt (`cmd`) on your PC and verify the connection:
```cmd
adb devices

```


*Expected output:*
```text
List of devices attached
R58M123456X    device

```


4. Instruct the ADB daemon on the Android device to listen for TCP/IP connections on port 5555:
```cmd
adb tcpip 5555

```


*Expected output:* `restarting in TCP mode port: 5555`
5. Unplug the USB cable.
6. Find your Android device's local Wi-Fi IP address:
* Go to **Settings** > **Network & internet** > **Wi-Fi** > Tap your active network > Scroll to **IP address** (e.g., `192.168.1.150`).


7. Connect to your phone over Wi-Fi from your PC terminal:
```cmd
adb connect 192.168.1.150:5555

```


*Expected output:* `connected to 192.168.1.150:5555`
8. Verify the active wireless link:
```cmd
adb devices

```


*Output must list `192.168.1.150:5555    device`.*

**Method B: Pure Wireless Pairing (Android 11+ / Cable-Free)**

1. Ensure your PC and Android device are connected to the exact same Wi-Fi network.
2. On your phone, go to **Settings** > **Developer Options** > **Wireless Debugging**.
3. Tap the text **Wireless Debugging** to open its settings page.
4. Tap **Pair device with pairing code**. A pop-up displays:
* **Wi-Fi pairing code** (e.g., `839201`)
* **IP address & Port** (e.g., `192.168.1.150:38421`)


5. Open Windows Command Prompt (`cmd`) and execute the pairing command:
```cmd
adb pair 192.168.1.150:38421

```


*When prompted, enter the 6-digit code `839201` and press Enter.*
6. Dismiss the pairing pop-up on the phone. Look at the main **Wireless Debugging** screen under **IP address & Port** for the *primary communication port* (which differs from the pairing port, e.g., `192.168.1.150:41255`).
7. Establish the primary connection:
```cmd
adb connect 192.168.1.150:41255

```


8. Confirm connectivity:
```cmd
adb devices

```



---

**Phase 3: Pushing Pre-Compiled Binaries to Android (One-Time Step)**

Before launching the host app, copy the pre-built `audio_player` binary to Android's temporary executable directory:

1. Open your terminal inside the folder containing your pre-compiled `audio_player` binary.
2. Push the binary to the phone:
```cmd
adb push audio_player /data/local/tmp/audio_player

```


3. Grant execution permissions:
```cmd
adb shell "chmod 755 /data/local/tmp/audio_player"

```


4. Verify the binary is present and executable:
```cmd
adb shell "ls -l /data/local/tmp/audio_player"

```


*Expected output:* `-rwxr-xr-x 1 shell shell ... /data/local/tmp/audio_player`

---

**Phase 4: Running the Streamer Application**

1. Navigate to your release directory:
```text
Audio Streamer\dist\Audio Streamer\

```


2. Launch **`Audio Streamer.exe`**.
3. The application runs headlessly and docks directly into the **Windows System Tray** (taskbar notification area near the clock).
4. Monitor the System Tray Icon:
* **Green Circle**: Actively capturing Windows audio, encoding to Opus, and transmitting UDP packets to the phone.
* **Yellow Circle**: Audio transmission is manually paused.


* **Red Circle**: Communication failure (e.g., phone dropped Wi-Fi or ADB disconnected). Hover over the icon to read the status tooltip.





---

**System Tray Controls**

* **Status**: Displays the current connection state and target IP address.
* **Pause Streaming**: Halts audio packet transmission while keeping the audio loopback device active.


* **Restart Stream**: Kills remote processes, rescans the Android Wi-Fi IP address, and establishes a fresh UDP session.


* **Start with Windows**: Adds a registry entry to run the streamer automatically on Windows startup.


* **Exit**: Terminates remote Android background audio tasks, frees WASAPI loopback hooks, closes sockets, and quits.



---

---

### OPTION 2: DEVELOPER PIPELINE (BUILDING & PACKAGING FROM SOURCE)

Use this method to modify the native C receiver engine, compile the Opus codec from source for ARM64, modify the Python capture logic, and compile a standalone Windows executable.

---

**Phase 1: Environment & Toolchain Setup**

1. **Install Android NDK:**
* Download Android NDK (version `r23b` or newer) via Android Studio SDK Manager or standalone `.zip`.
* Set your environment variable:
```cmd
setx ANDROID_HOME "C:\Users\<YourUsername>\AppData\Local\Android\Sdk"

```




2. **Download Opus Codec Source:**
* Download `opus-1.5.2.tar.gz` from the official Xiph.org website.
* Extract the archive directly into your project root so that the directory `opus-1.5.2` contains `include/`, `src/`, `celt/`, and `silk/`.


3. **Install Python Host Dependencies:**
* Ensure Python 64-bit (3.10 through 3.14) is installed.
* Install the necessary audio, image, and packaging modules:
```cmd
pip install numpy pyaudiowpatch pystray pillow opuslib pyinstaller

```




4. **Acquire `opus.dll`:**
* Obtain a 64-bit Windows build of `opus.dll` and place it in the project root directory alongside `audio_streamer.py`.



---

**Phase 2: Source Code Directory Structure**

Verify your project root is organized as follows:

```text
Audio Streamer/
│   audio_player.c                  # Native Android C code (OpenSL ES + Opus)
│   grab_event.c                    # Native input injector (optional)
│   audio_streamer.py               # Host PC audio capturer & streamer
│   deploy.py                       # Automated cross-compilation & deploy script
│   opus.dll                        # Windows 64-bit library for PyInstaller
│
└───opus-1.5.2/                     # Extracted Opus source code
    ├───include/
    ├───src/
    ├───celt/
    └───silk/

```

---

**Phase 3: Automated Compilation & Deployment (`deploy.py`)**

Run the automated deployment script with your phone connected via ADB (wired or wirelessly):

```cmd
python deploy.py

```

**Internal Operations Executed by `deploy.py`:**

1. Dynamically discovers your installed Android NDK LLVM Clang compiler (`aarch64-linux-android28-clang.cmd`) and archiver (`llvm-ar.exe`).
2. Isolates and cross-compiles all C sources inside `opus-1.5.2/src`, `opus-1.5.2/celt`, `opus-1.5.2/silk`, and `opus-1.5.2/silk/float` with optimizations:
`-O3 -fPIC -DOPUS_BUILD -DVAR_ARRAYS -DUSE_ALLOCA -DHAVE_LRINT -DHAVE_LRINTF`.
3. Packages all compiled object files into a single static library: `build/libopus.a`.
4. Compiles `audio_player.c`, statically linking `build/libopus.a`, Android OpenSL ES (`-lOpenSLES`), and the math library (`-lm`) into a stripped ARM64 ELF binary (`audio_player`).
5. Kills any stale processes on the Android target, pushes the new `audio_player` binary to `/data/local/tmp/audio_player`, and applies executable permissions (`chmod 755`).



---

**Phase 4: Packaging the Windows Executable**

To bundle the Python streamer into a single-folder distribution:

1. Open your terminal in the project root.
2. Run PyInstaller with Windows Subsystem settings (to prevent terminal popups):
```cmd
pyinstaller --noconfirm --onedir --windowed --add-binary "opus.dll;." --name "Audio Streamer" audio_streamer.py

```


3. Copy `opus.dll` directly to the output executable directory to ensure runtime loading:
```cmd
copy opus.dll "dist\Audio Streamer\opus.dll"

```


4. Your distributable build will now match the complete tree structure:
```text
dist\Audio Streamer\
│   Audio Streamer.exe
│   opus.dll
└───_internal\
        ...

```



---

**Phase 5: Low-Level Profiling & Terminal Debugging**

When writing custom DSP code, downmix algorithms, or tuning network buffers, run components directly in debug mode:

* **Inspect Android Receiver Output in Real Time:**
Run the native binary in foreground mode over ADB shell to monitor ring buffer behavior and Opus packet decoding:
```cmd
adb shell "/data/local/tmp/audio_player 12345"

```


* **Verify Android UDP Socket Allocation:**
Confirm the native receiver is listening on the assigned port:
```cmd
adb shell "netstat -uln | grep 12345"

```


*(Or on modern Android: `adb shell "ss -ulnp | grep 12345"`)*
* **Inspect Android Audio Framework Logs:**
Monitor OpenSL ES hardware buffer latency and underruns via logcat:
```cmd
adb logcat -s OpenSLES:V AudioTrack:V

```


* **Run Python Streamer in Console Mode:**
Execute the Python script directly to view WASAPI loopback device discovery, sample rate conversions, and network queue throughput:
```cmd
python audio_streamer.py

```