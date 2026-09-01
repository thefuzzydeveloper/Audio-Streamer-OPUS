import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# Enable ANSI colors on Windows console
os.system("")

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"

# Path Configuration
EXPORT_ROOT = Path(r"F:\Gaming\Godot\Requirements\AndroidExport")
SDK_ROOT = EXPORT_ROOT / "sdk"
JAVA_HOME = EXPORT_ROOT / "java"
JAVA_BIN = JAVA_HOME / "bin"

# Export JAVA_HOME and inject java/bin into PATH so d8.bat and apksigner.bat can locate java
os.environ["JAVA_HOME"] = str(JAVA_HOME)
os.environ["PATH"] = f"{JAVA_BIN};{os.environ.get('PATH', '')}"

NDK_BIN_DIR = (
    SDK_ROOT
    / r"ndk\23.2.8568313\toolchains\llvm\prebuilt\windows-x86_64\bin"
)
NDK_CLANG = NDK_BIN_DIR / "aarch64-linux-android28-clang.cmd"
NDK_AR = NDK_BIN_DIR / "llvm-ar.exe"

PROJECT_ROOT = Path(__file__).resolve().parent
OPUS_SRC_DIR = PROJECT_ROOT / "opus-1.5.2"
BUILD_DIR = PROJECT_ROOT / "build"
SRC_DIR = PROJECT_ROOT / "src"
RES_DIR = PROJECT_ROOT / "res"
JNI_DIR = PROJECT_ROOT / "jni"
MANIFEST_FILE = PROJECT_ROOT / "AndroidManifest.xml"
KEYSTORE_FILE = BUILD_DIR / "debug.keystore"


def log_info(msg: str):
    print(f"{CYAN}[INFO]{RESET} {msg}")


def log_success(msg: str):
    print(f"{GREEN}[OK]{RESET} {msg}")


def log_error(msg: str):
    print(f"{RED}{BOLD}[ERROR]{RESET} {msg}")
    sys.exit(1)


def run_command(command, description: str):
    log_info(description)
    is_cmd = isinstance(command, list) and str(command[0]).endswith(
        (".cmd", ".bat")
    )
    result = subprocess.run(
        command,
        shell=True if (isinstance(command, str) or is_cmd) else False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log_error(f"Failed: {description}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def resolve_sdk_and_jdk_tools():
    # Verify JDK tools
    javac_exe = JAVA_BIN / "javac.exe"
    keytool_exe = JAVA_BIN / "keytool.exe"
    java_exe = JAVA_BIN / "java.exe"

    for tool in [javac_exe, keytool_exe, java_exe]:
        if not tool.exists():
            log_error(f"Required JDK binary not found at: {tool}")

    # Verify Android Build Tools
    build_tools_root = SDK_ROOT / "build-tools"
    versions = sorted(build_tools_root.glob("*"), reverse=True)
    if not versions:
        log_error(f"No Android build-tools found in {build_tools_root}")
    bt_dir = versions[0]

    # Verify Android Platform Jar
    platforms_root = SDK_ROOT / "platforms"
    platforms = sorted(platforms_root.glob("android-*"), reverse=True)
    if not platforms:
        log_error(f"No Android platforms found in {platforms_root}")
    android_jar = platforms[0] / "android.jar"

    return {
        "javac": javac_exe,
        "keytool": keytool_exe,
        "aapt2": bt_dir / "aapt2.exe",
        "d8": bt_dir / "d8.bat",
        "zipalign": bt_dir / "zipalign.exe",
        "apksigner": bt_dir / "apksigner.bat",
        "android_jar": android_jar,
    }


def setup_include_tree():
    staged_include_opus = BUILD_DIR / "include" / "opus"
    staged_include_opus.mkdir(parents=True, exist_ok=True)
    for header in (OPUS_SRC_DIR / "include").glob("*.h"):
        shutil.copy2(header, staged_include_opus / header.name)
        shutil.copy2(header, BUILD_DIR / "include" / header.name)


def build_libopus() -> Path:
    lib_path = BUILD_DIR / "libopus.a"
    if lib_path.exists():
        return lib_path

    log_info("Compiling Opus 1.5.2 for Android arm64-v8a...")
    setup_include_tree()
    obj_dir = BUILD_DIR / "obj" / "opus"
    obj_dir.mkdir(parents=True, exist_ok=True)

    opus_c_files = []
    opus_c_files.extend((OPUS_SRC_DIR / "src").glob("*.c"))
    opus_c_files.extend((OPUS_SRC_DIR / "celt").glob("*.c"))
    opus_c_files.extend((OPUS_SRC_DIR / "silk").glob("*.c"))
    opus_c_files.extend((OPUS_SRC_DIR / "silk" / "float").glob("*.c"))

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
            log_error(f"Failed compiling {src.name}: {res.stderr}")

    run_command(
        [str(NDK_AR), "rcs", str(lib_path)] + [str(o) for o in obj_files],
        "Archiving libopus.a...",
    )
    return lib_path


def build_jni_shared_lib(libopus_a: Path) -> Path:
    lib_dir = BUILD_DIR / "lib" / "arm64-v8a"
    lib_dir.mkdir(parents=True, exist_ok=True)
    so_out = lib_dir / "libaudio_player.so"

    cmd = [
        str(NDK_CLANG),
        "-shared",
        "-fPIC",
        str(JNI_DIR / "audio_player_jni.c"),
        str(libopus_a),
        f"-I{BUILD_DIR}/include",
        "-O3",
        "-s",
        "-lOpenSLES",
        "-llog",
        "-lm",
        "-o",
        str(so_out),
    ]
    run_command(cmd, "Compiling libaudio_player.so...")
    return so_out


def compile_resources_and_link(tools: dict) -> Path:
    compiled_res = BUILD_DIR / "compiled_res.zip"
    gen_dir = BUILD_DIR / "gen"
    gen_dir.mkdir(parents=True, exist_ok=True)
    unaligned_apk = BUILD_DIR / "app-unaligned.apk"

    # Compile drawable XML resources
    aapt2_compile_cmd = [
        str(tools["aapt2"]),
        "compile",
        "--dir",
        str(RES_DIR),
        "-o",
        str(compiled_res),
    ]
    run_command(aapt2_compile_cmd, "Compiling resources with aapt2...")

    # Link resources and generate R.java
    aapt2_link_cmd = [
        str(tools["aapt2"]),
        "link",
        "-I",
        str(tools["android_jar"]),
        "--manifest",
        str(MANIFEST_FILE),
        "--java",
        str(gen_dir),
        "--auto-add-overlay",
        "-o",
        str(unaligned_apk),
        str(compiled_res),
    ]
    run_command(aapt2_link_cmd, "Linking resources & generating R.java...")
    return unaligned_apk


def build_dex(tools: dict):
    classes_dir = BUILD_DIR / "classes"
    classes_dir.mkdir(parents=True, exist_ok=True)

    java_files = list(SRC_DIR.rglob("*.java")) + list(
        (BUILD_DIR / "gen").rglob("*.java")
    )
    
    javac_cmd = [
        str(tools["javac"]),
        "-source",
        "1.8",
        "-target",
        "1.8",
        "-cp",
        str(tools["android_jar"]),
        "-d",
        str(classes_dir),
    ] + [str(f) for f in java_files]
    run_command(javac_cmd, "Compiling Java sources with javac...")

    class_files = list(classes_dir.rglob("*.class"))
    d8_cmd = [
        str(tools["d8"]),
        "--lib",
        str(tools["android_jar"]),
        "--output",
        str(BUILD_DIR),
    ] + [str(f) for f in class_files]
    run_command(d8_cmd, "Generating classes.dex with d8...")


def package_and_sign(tools: dict, unaligned_apk: Path):
    aligned_apk = BUILD_DIR / "app-aligned.apk"
    final_apk = BUILD_DIR / "OpusPlayer.apk"

    for f in [aligned_apk, final_apk]:
        if f.exists():
            f.unlink()

    log_info("Injecting classes.dex and arm64-v8a native library into APK...")
    with zipfile.ZipFile(unaligned_apk, "a", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(BUILD_DIR / "classes.dex", "classes.dex")
        so_path = BUILD_DIR / "lib" / "arm64-v8a" / "libaudio_player.so"
        z.write(so_path, "lib/arm64-v8a/libaudio_player.so")

    run_command(
        [
            str(tools["zipalign"]),
            "-f",
            "-p",
            "4",
            str(unaligned_apk),
            str(aligned_apk),
        ],
        "Aligning APK (zipalign)...",
    )

    if not KEYSTORE_FILE.exists():
        keytool_cmd = [
            str(tools["keytool"]),
            "-genkeypair",
            "-v",
            "-keystore",
            str(KEYSTORE_FILE),
            "-alias",
            "androiddebugkey",
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "10000",
            "-storepass",
            "android",
            "-keypass",
            "android",
            "-dname",
            "CN=Android Debug,O=Android,C=US",
        ]
        run_command(keytool_cmd, "Generating debug.keystore...")

    run_command(
        [
            str(tools["apksigner"]),
            "sign",
            "--ks",
            str(KEYSTORE_FILE),
            "--ks-pass",
            "pass:android",
            "--ks-key-alias",
            "androiddebugkey",
            "--key-pass",
            "pass:android",
            "--out",
            str(final_apk),
            str(aligned_apk),
        ],
        "Signing APK with apksigner...",
    )
    return final_apk


def deploy(apk: Path):
    run_command(
        ["adb", "install", "-r", str(apk)],
        "Installing APK to connected device...",
    )
    run_command(
        [
            "adb",
            "shell",
            "am",
            "start",
            "-n",
            "com.example.opusplayer/.MainActivity",
        ],
        "Launching com.example.opusplayer...",
    )


def main():
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    tools = resolve_sdk_and_jdk_tools()
    setup_include_tree()
    libopus_a = build_libopus()
    build_jni_shared_lib(libopus_a)
    unaligned_apk = compile_resources_and_link(tools)
    build_dex(tools)
    final_apk = package_and_sign(tools, unaligned_apk)
    deploy(final_apk)
    print(f"\n{GREEN}{BOLD}STREAMING APP DEPLOYED: {final_apk}{RESET}\n")


if __name__ == "__main__":
    main()