import glob, os, shutil, subprocess, sys
from pathlib import Path
from pathlib import Path

# Enable ANSI colors on Windows console
os.system("")

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
YELLOW = "\033[93m"

sdk_dir = Path(os.environ.get("ANDROID_HOME", Path.home() / "AppData/Local/Android/Sdk"))
ndk_base = sdk_dir / "ndk"

# Finds the latest/available NDK version automatically
ndk_versions = sorted(ndk_base.glob("*"), reverse=True) if ndk_base.exists() else []

if ndk_versions:
    NDK_BIN_DIR = ndk_versions[0] / "toolchains/llvm/prebuilt/windows-x86_64/bin"
else:
    raise FileNotFoundError("Android NDK not found.")

NDK_CLANG = NDK_BIN_DIR / "aarch64-linux-android28-clang.cmd"
NDK_AR = NDK_BIN_DIR / "llvm-ar.exe"

PROJECT_ROOT = Path(__file__).resolve().parent
OPUS_SRC_DIR = PROJECT_ROOT / "opus-1.5.2"
BUILD_DIR = PROJECT_ROOT / "build"
REMOTE_DIR = "/data/local/tmp"


def log_info(msg: str):
    print(f"{CYAN}[INFO]{RESET} {msg}")


def log_success(msg: str):
    print(f"{GREEN}[OK]{RESET} {msg}")


def log_warn(msg: str):
    print(f"{YELLOW}[WARN]{RESET} {msg}")


def log_error(msg: str):
    print(f"{RED}{BOLD}[ERROR]{RESET} {msg}")


def run_command(command, description: str, critical: bool = True) -> bool:
    log_info(description)
    try:
        is_cmd = isinstance(command, list) and str(command[0]).endswith(".cmd")
        result = subprocess.run(
            command,
            shell=True if (isinstance(command, str) or is_cmd) else False,
            check=False,
        )
        if result.returncode != 0:
            if critical:
                log_error(f"Command failed with exit code {result.returncode}: {description}")
                sys.exit(1)
            else:
                log_warn(f"Non-critical command exited with code {result.returncode}")
                return False
        return True
    except Exception as e:
        if critical:
            log_error(f"Failed to execute command: {e}")
            sys.exit(1)
        log_warn(f"Failed non-critical execution: {e}")
        return False


def verify_environment():
    if not shutil.which("adb"):
        log_error("ADB executable not found in system PATH.")
        sys.exit(1)

    if not NDK_CLANG.exists():
        log_error(f"NDK Clang compiler not found at:\n{NDK_CLANG}")
        sys.exit(1)

    if not OPUS_SRC_DIR.exists():
        log_error(f"Opus source directory not found at:\n{OPUS_SRC_DIR}")
        sys.exit(1)

    adb_devices = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    lines = [line.strip() for line in adb_devices.stdout.strip().splitlines()[1:] if line.strip()]
    active_devices = [line for line in lines if "\tdevice" in line]

    if not active_devices:
        log_error("No authorized ADB device detected. Connect device and enable USB Debugging.")
        sys.exit(1)

    log_success(f"Connected ADB Target: {active_devices[0].split()[0]}")


def setup_include_tree():
    """Sets up build/include/opus so both <opus/opus.h> and <opus.h> resolve."""
    staged_include_opus = BUILD_DIR / "include" / "opus"
    staged_include_opus.mkdir(parents=True, exist_ok=True)

    for header in (OPUS_SRC_DIR / "include").glob("*.h"):
        shutil.copy2(header, staged_include_opus / header.name)
        shutil.copy2(header, BUILD_DIR / "include" / header.name)


def build_libopus() -> Path:
    lib_path = BUILD_DIR / "libopus.a"
    if lib_path.exists():
        log_info("Cached libopus.a found. Skipping Opus rebuild.")
        return lib_path

    log_info("Compiling Opus 1.5.2 for Android aarch64...")
    setup_include_tree()

    obj_dir = BUILD_DIR / "obj" / "opus"
    obj_dir.mkdir(parents=True, exist_ok=True)

    # Collect core Opus C sources
    opus_c_files = []
    opus_c_files.extend((OPUS_SRC_DIR / "src").glob("*.c"))
    opus_c_files.extend((OPUS_SRC_DIR / "celt").glob("*.c"))
    opus_c_files.extend((OPUS_SRC_DIR / "silk").glob("*.c"))
    opus_c_files.extend((OPUS_SRC_DIR / "silk" / "float").glob("*.c"))

    # Exclude standalone test/demo executables
    excludes = {
        "opus_demo.c",
        "opus_custom_demo.c",
        "opus_compare.c",
        "repacketizer_demo.c",
    }
    compile_files = [f for f in opus_c_files if f.name not in excludes]

    cflags = [
        "-O3",
        "-fPIC",
        "-DOPUS_BUILD",
        "-DVAR_ARRAYS",
        "-DUSE_ALLOCA",
        "-DHAVE_LRINT",
        "-DHAVE_LRINTF",
        f"-I{OPUS_SRC_DIR}/include",
        f"-I{OPUS_SRC_DIR}/celt",
        f"-I{OPUS_SRC_DIR}/silk",
        f"-I{OPUS_SRC_DIR}/silk/float",
    ]

    obj_files = []
    for src in compile_files:
        obj_file = obj_dir / f"{src.stem}.o"
        obj_files.append(obj_file)
        cmd = [str(NDK_CLANG), "-c", str(src), "-o", str(obj_file)] + cflags
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode != 0:
            log_error(f"Failed compiling {src.name}:\n{res.stderr}")
            sys.exit(1)

    # Archive object files into libopus.a
    ar_cmd = [str(NDK_AR), "rcs", str(lib_path)] + [str(o) for o in obj_files]
    run_command(ar_cmd, "Creating static library build/libopus.a...")
    log_success("libopus.a built successfully.")
    return lib_path


def main():
    verify_environment()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Terminate running instances
    run_command(
        'adb shell "killall -9 grab_event audio_player 2>/dev/null; pkill -9 -f grab_event 2>/dev/null; pkill -9 -f audio_player 2>/dev/null; exit 0"',
        "Terminating running instances on Android...",
        critical=False,
    )

    # 2. Build Opus static library
    setup_include_tree()
    libopus_a = build_libopus()

    # 3. Compile grab_event.c (if present)
    if Path("grab_event.c").exists():
        run_command(
            [str(NDK_CLANG), "grab_event.c", "-O3", "-s", "-o", "grab_event"],
            "Compiling grab_event.c...",
        )
        log_success("grab_event compiled successfully.")

    # 4. Compile audio_player.c with statically linked Opus and OpenSL ES
    if not Path("audio_player.c").exists():
        log_error("audio_player.c not found in current directory.")
        sys.exit(1)

    run_command(
        [
            str(NDK_CLANG),
            "audio_player.c",
            str(libopus_a),
            f"-I{BUILD_DIR}/include",
            "-O3",
            "-s",
            "-lOpenSLES",
            "-lm",
            "-o",
            "audio_player",
        ],
        "Compiling audio_player.c with OpenSL ES and static Opus...",
    )
    log_success("audio_player compiled successfully.")

    # 5. Push binaries to Android
    files_to_push = [f for f in ["grab_event", "audio_player"] if Path(f).exists()]
    for binary in files_to_push:
        run_command(
            ["adb", "push", binary, f"{REMOTE_DIR}/{binary}"],
            f"Pushing {binary} -> {REMOTE_DIR}/{binary}...",
        )

    # 6. Set executable permissions
    remote_paths = " ".join([f"{REMOTE_DIR}/{b}" for b in files_to_push])
    run_command(
        f'adb shell "chmod 755 {remote_paths}"',
        f"Setting executable permissions (chmod 755)...",
    )

    print(f"\n{GREEN}{BOLD}" + "=" * 60)
    print("  DEPLOYMENT COMPLETE: Opus + OpenSL ES binaries active")
    print("=" * 60 + f"{RESET}\n")


if __name__ == "__main__":
    main()