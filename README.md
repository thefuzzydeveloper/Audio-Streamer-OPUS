

# Audio Streamer (Opus)

Stream your Windows PC sound directly to your Android device over Wi-Fi with ultra-low delay. Ideal for watching videos, gaming, or using your phone as wireless headphones without annoying audio lag. 

## Quick Start (Recommended)

Get up and running in less than two minutes without dealing with command lines or developer tools.

### Step 1: Download the Apps

- **Windows Host:** [Download AudioStreamer_Setup.exe](https://github.com/thefuzzydeveloper/Audio-Streamer-OPUS/releases/download/v1.5/AudioStreamer_Setup.exe)
- **Android Receiver:** [Download OpusPlayer.apk](https://github.com/thefuzzydeveloper/Audio-Streamer-OPUS/releases/download/v1.5/OpusPlayer.apk)
   
### Step 2: Install & Connect

1. Install and open the **OpusPlayer app** on your Android phone.
2. Install and launch **Audio Streamer** on your Windows PC.
3. Make sure both devices are on the **same Wi-Fi network** (in case wifi is not available, connect your PC with android phone hotspot).
4. In the app, click on start receiver and it connects automatically and starts streaming your desktop sound immediately.

## Alternative Setup: Standalone ADB Mode

Use this method if you prefer not to install an APK and want to run the lightweight receiver directly via ADB.

### 1. Prepare Your Android Phone

1. Open **Settings** > **About Phone**.
2. Tap **Build Number** 7 times until you see the _"You are now a developer!"_ message.
3. Go to **Settings** > **Developer Options** and turn **USB Debugging** to **ON**.

### 2. Connect Your Phone to PC

**Via USB Cable (Easiest)**

1. Connect your phone to your PC via USB and allow USB debugging when prompted.
2. Open Command Prompt (`cmd`) on your PC and run:

    DOS

    ```
    adb tcpip 5555
    ```

3. Disconnect the USB cable.
4. Find your phone's Wi-Fi IP address in **Settings** > **Wi-Fi** > your network details.
5. Connect wirelessly:

    DOS

    ```
    adb connect <YOUR_PHONE_IP>:5555
    ```

**Via Wireless Debugging (Android 11+)**

1. In **Developer Options**, enable **Wireless Debugging** and tap **Pair device with pairing code**.
2. Run the pairing command shown on screen:

    DOS

    ```
    adb pair <IP>:<PORT>
    ```

3. Connect using the main address shown on the Wireless Debugging screen:

    DOS

    ```
    adb connect <IP>:<PORT>
    ```

### 3. Send the Audio Player to Your Phone

Open Command Prompt in your streamer folder and run:

DOS

```
adb push audio_player /data/local/tmp/audio_player
adb shell "chmod 755 /data/local/tmp/audio_player"
```

### 4. Launch the Streamer

Run `Audio Streamer.exe` on your PC. It will minimize to the **System Tray** (near your Windows clock):

- **Green Icon:** Streaming actively.
- **Yellow Icon:** Stream is paused.
- **Red Icon:** Connection dropped (check Wi-Fi or ADB connection).

Right-click the tray icon to pause, restart, or set the app to open automatically when Windows starts.

## For Developers: Building from Source

To customize or compile the binaries yourself:

### 1. Prerequisites

- **Android NDK** (`r23b` or newer)
- **Python 3.10+** (64-bit)
- Python packages: `pip install numpy pyaudiowpatch pystray pillow opuslib pyinstaller`
- Opus source code (`opus-1.5.2`) extracted into the project root directory
- A 64-bit `opus.dll` placed in the project folder

### 2. Build & Deploy

1. **Compile for Android & Push via ADB:**

    DOS

    ```
    python deploy.py
    ```

2. **Package the Windows Executable:**

    DOS

    ```
    pyinstaller --noconfirm --onedir --windowed --add-binary "opus.dll;." --name "Audio Streamer" audio_streamer.py
    copy opus.dll "dist\Audio Streamer\opus.dll"
    ```

## Key Features

- **Ultra-Low Latency:** Optimized for sub-50 ms delay, keeping audio perfectly in sync with video and games.
- **Zero Dropouts:** Powered by the Opus audio codec over UDP to ensure smooth playback even on busy Wi-Fi networks.
- **Smart Audio Conversion:** Automatically converts multi-channel surround sound (5.1/7.1) into clean, high-quality stereo audio with full bass preservation.
- **Low Resource Usage:** Runs quietly in the background without eating up your CPU or draining your phone battery.
- **System Tray Controls:** Easy one-click access to pause, restart, or enable auto-start on Windows boot.
- **Extremely light-weight** Android package is few Kbs, and highly optimized
- **Better alternative of other apps in same category** - No license verification, payment, or data collection, full privacy!
- **Better than Bluetooth headsets** - Lower latency than unreliable Bluetooth network

--> Expect audio lag for first few minutes till audio buffer builds up! Audio should clear up as the buffer builds up
