#build.py

import subprocess, sys, os, ast, argparse, time, itertools, math, threading, shutil, glob, json, re, stat, hashlib, concurrent.futures, urllib.request, base64
from pathlib import Path
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization
except ImportError:
    pass

if sys.version_info < (3, 12):
    os.environ["SETUPTOOLS_USE_DISTUTILS"] = "stdlib"
os.environ["PYTHONWARNINGS"] = "ignore"

msvcrt = __import__('msvcrt') if os.name == 'nt' else None
fcntl = __import__('fcntl') if os.name != 'nt' else None

if os.name == 'nt':
    os.system('')
sys.setrecursionlimit(5000)


# =============================================================================
# AUTO-IMPORT RESOLVER
# =============================================================================
TYPING_SYMBOLS = {
    "List", "Dict", "Tuple", "Set", "Optional", "Union", "Any", "Callable", 
    "Iterable", "Iterator", "Mapping", "Sequence", "Type", "TypeVar", "Generic", 
    "NamedTuple", "Literal", "Annotated", "Protocol", "Final", "ClassVar",
    "overload", "cast", "TypedDict", "Generator", "AsyncGenerator", "Coroutine",
    "FrozenSet", "DefaultDict", "Deque", "Counter"
}

COMMON_SYMBOL_MAP = {
    "Path": ("pathlib", "Path"),
    "dataclass": ("dataclasses", "dataclass"),
    "field": ("dataclasses", "field"),
    "defaultdict": ("collections", "defaultdict"),
    "deque": ("collections", "deque"),
    "Counter": ("collections", "Counter"),
    "OrderedDict": ("collections", "OrderedDict"),
    "Enum": ("enum", "Enum"),
    "auto": ("enum", "auto"),
    "datetime": ("datetime", "datetime"),
    "timedelta": ("datetime", "timedelta"),
    "date": ("datetime", "date"),
    "sleep": ("time", "sleep"),
    "abstractmethod": ("abc", "abstractmethod"),
    "ABC": ("abc", "ABC"),
}

def auto_inject_missing_imports(filepath):
    """
    Parses an isolated source file, identifies used symbols with missing definitions/imports,
    and prepends the required import statements to the file, detailing exact insertions.
    """
    import builtins
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content, filename=filepath)
    except Exception:
        return

    defined_names = set(dir(builtins))
    used_names = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined_names.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Param)):
                defined_names.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                used_names.add(node.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                defined_names.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                defined_names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            defined_names.update(node.names)

    missing_symbols = used_names - defined_names
    if not missing_symbols:
        return

    # Identify symbols matching known packages
    typing_imports = sorted(list(missing_symbols & TYPING_SYMBOLS))
    other_imports = {}
    for sym in missing_symbols:
        if sym in COMMON_SYMBOL_MAP:
            mod, name = COMMON_SYMBOL_MAP[sym]
            other_imports.setdefault(mod, []).append(name)

    injected_lines = []
    if typing_imports:
        injected_lines.append(f"from typing import {', '.join(typing_imports)}")
    for mod, syms in sorted(other_imports.items()):
        injected_lines.append(f"from {mod} import {', '.join(sorted(syms))}")

    if not injected_lines:
        return

    injection_str = "\n".join(injected_lines) + "\n"
    lines = content.splitlines(keepends=True)

    # Insert after __future__ imports or docstrings
    insert_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("from __future__ import"):
            insert_idx = i + 1
        elif insert_idx == 0 and (stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''")):
            continue

    lines.insert(insert_idx, injection_str)
    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)
    
    file_name = Path(filepath).name
    print(f"{GREEN} [+] Auto-injected missing imports into file '{file_name}':{RESET}")
    for line in injected_lines:
        print(f"{CYAN}       -> Inserted: {line}{RESET}")

def resolve_isolated_missing_imports():
    """Scans all python files within the current isolated build folder."""
    print(f"\n{CYAN}[*] Auto-detecting & patching missing imports across isolated source tree...{RESET}")
    for root, _, files in os.walk("."):
        for file in files:
            if file.endswith(".py"):
                auto_inject_missing_imports(os.path.join(root, file))

# =============================================================================
# CONSTANTS & CONFIGURATION
# =============================================================================
RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"

class BuildConfig:
    APP_NAME = "UnknownApp"
    MAIN_SCRIPT = "main.py"
    ATTACH_CONSOLE = True
    USE_VENV = True
    CPU_CORES = os.cpu_count() or 4
    
    # -------------------------------------------------------------------------
    # GLOBAL EXTERNAL BUILD ROUTING
    # -------------------------------------------------------------------------
    # 1. Base absolute directory for all project builds
    GLOBAL_BUILD_ROOT = r"F:\Coding\dist"
    
    # 2. Get current app folder name dynamically
    APP_FOLDER_NAME = os.path.basename(os.path.abspath(os.path.dirname(__file__)))
    
    # 3. Dedicated target path combining the global root and this project
    PROJECT_BUILD_DIR = os.path.join(GLOBAL_BUILD_ROOT, APP_FOLDER_NAME)
    
    # 4. Final nested paths for environment and binaries.
    ISOLATED_BUILD_DIR = os.path.join(PROJECT_BUILD_DIR, "build_env").replace("\\", "/")
    DIST_DIR = os.path.join(PROJECT_BUILD_DIR, "dist").replace("\\", "/")
    # -------------------------------------------------------------------------
    
    PREFS_FILE = "build_prefs.json"
    ORIGINAL_ROOT = str(Path.cwd().resolve())
    PYTHON_EXE = sys.executable
    PRESERVE_UPDATER = True
    STRIP_METADATA = True
    UPDATER_SCRIPT = ""
    EXCLUDE_FROM_BUILD = ["build.py", "cython_setup.py", "test.py", "test2.py", "setup.py", "auto_fix_cycles.py", "build_prefs.json"]
    COLLECT_DATA_PACKAGES = []
    COLLECT_SUBMODULES = []
    ICON_FILE = None
    C_EXTENSION_TARGETS = []
    PLUGIN_DIRECTORIES = []
    DATA_FILES = []
    DATA_DIRECTORIES = []

# =============================================================================
# UI & PROGRESS UTILITIES
# =============================================================================
class UIAesthetics:
    @staticmethod
    def format_time(seconds):
        return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

    @staticmethod
    def gradient_bar(bar):
        colors = ["\033[38;5;21m","\033[38;5;27m","\033[38;5;33m","\033[38;5;39m","\033[38;5;45m","\033[38;5;51m"]
        return "".join(f"{colors[int((i / len(bar)) * (len(colors) - 1))]}{ch}" for i, ch in enumerate(bar)) + RESET

    @staticmethod
    def run_with_progress_bar(command, engine_name, step_info=None):
        prefix = f"[{step_info}]" if step_info else "[*]"
        print(f"\n{CYAN}{prefix} Initializing {engine_name} task...{RESET}")
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', **kwargs)        
            
            # High-fidelity telemetry assets
            spinners = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            spinner_cycle = itertools.cycle(spinners)
            shades = ["░", "▒", "▓", "█"]
            
            log_buffer = []
            current_activity = ["Assembling operational pipeline..."]
            start_time = time.time()
            tick = 0
            
            # Background pipeline stdout monitor
            def read_output():
                for line in process.stdout:
                    cleaned_line = line.strip()
                    if cleaned_line:
                        log_buffer.append(cleaned_line)
                        current_activity[0] = cleaned_line
                    if len(log_buffer) > 100: 
                        log_buffer.pop(0)
            
            output_thread = threading.Thread(target=read_output, daemon=True)
            output_thread.start()
            
            while process.poll() is None:
                elapsed = time.time() - start_time
                time_str = UIAesthetics.format_time(elapsed)
                
                # 1. Fetch live console column width safely
                term_cols = shutil.get_terminal_size((80, 20)).columns
                spinner_char = next(spinner_cycle)
                
                # 2. Setup dynamic visual budget constraints
                bar_width = 25
                prefix_visible = f" {spinner_char} [{engine_name}] ["
                suffix_visible = f"] {time_str} | Trace: "
                
                fixed_len = len(prefix_visible) + bar_width + len(suffix_visible)
                
                # Auto-shrink the animated wave bar if the terminal is very small
                if fixed_len >= term_cols:
                    bar_width = max(10, term_cols - len(prefix_visible) - len(suffix_visible) - 6)
                    fixed_len = len(prefix_visible) + bar_width + len(suffix_visible)
                
                # 3. Render wave-interpolated sequence
                wave = [int((1 + math.sin(tick / 3.0 + j * 0.35)) * 1.5) for j in range(bar_width)]
                raw_bar = "".join(shades[min(3, max(0, w))] for w in wave)
                colored_bar = UIAesthetics.gradient_bar(raw_bar)
                
                # 4. Enforce strict mathematical alignment bounds to prevent line drops
                # We leave a 4-column absolute safety buffer zone
                max_activity_len = term_cols - fixed_len - 4
                
                if max_activity_len > 5:
                    raw_activity = current_activity[0]
                    if len(raw_activity) > max_activity_len:
                        activity_text = "..." + raw_activity[-(max_activity_len - 3):]
                    else:
                        activity_text = raw_activity.ljust(max_activity_len)
                    
                    full_line = f"\r{MAGENTA} {spinner_char} {CYAN}[{engine_name}] [{colored_bar}] {YELLOW}{time_str} {RESET}| {GREEN}Trace: {activity_text}{RESET}\033[K"
                else:
                    # Emergency layout configuration for hyper-narrow windows (Drops live trace)
                    suffix_short = f"] {time_str}"
                    bar_width = max(5, term_cols - len(prefix_visible) - len(suffix_short) - 4)
                    wave = [int((1 + math.sin(tick / 3.0 + j * 0.35)) * 1.5) for j in range(bar_width)]
                    raw_bar = "".join(shades[min(3, max(0, w))] for w in wave)
                    colored_bar = UIAesthetics.gradient_bar(raw_bar)
                    full_line = f"\r{MAGENTA} {spinner_char} {CYAN}[{engine_name}] [{colored_bar}] {YELLOW}{time_str}{RESET}\033[K"
                
                sys.stdout.write(full_line)
                sys.stdout.flush()
                
                time.sleep(0.04)
                tick += 1
                
            process.wait()
            output_thread.join(timeout=0.2)
            
            # Atomic line clearance before printing status report
            sys.stdout.write("\r" + " " * term_cols + "\r")
            sys.stdout.flush()
            
            if process.returncode == 0: 
                print(f"{GREEN}[✔] Success: {engine_name} task complete! Total duration: {UIAesthetics.format_time(time.time() - start_time)}{RESET}")
            else: 
                print(f"\n{RED}[✘] Critical Failure in {engine_name} (Exit Code {process.returncode})\n{'-'*60}\n" + "\n".join(log_buffer[-30:]) + f"\n{'-'*60}{RESET}")
        except Exception as e: 
            print(f"\n[ERROR] Failure in tracking loop: {e}")

    @staticmethod
    def interactive_folder_menu(title, options):
        if not options: return {}
        state = {opt: [True, True] for opt in options}
        cursor = 0
        sys.stdout.write("\033[?25l")
        
        def draw():
            sys.stdout.write(f"\r{CYAN}[*] {title}{RESET}\n")
            for i, opt in enumerate(options):
                prefix = f"{MAGENTA}>" if i == cursor else " "
                bundle_chk = f"{GREEN}[x]" if state[opt][0] else f"\033[90m[ ]"
                all_chk = f"{GREEN}[x]" if state[opt][1] else f"\033[90m[ ]"
                opt_color = RESET if state[opt][0] else "\033[90m"
                all_text = f"{GREEN}Include All{RESET}" if state[opt][1] else f"\033[90mInclude All{RESET}"
                sys.stdout.write(f"\r{prefix} {bundle_chk} Bundle   {all_chk} {all_text}  |  {opt_color}{opt}{RESET}\033[K\n")
            sys.stdout.write(f"\r{YELLOW}  (Arrows: Nav, Space: Toggle Bundle, 'A': Toggle Include All, Enter: Confirm){RESET}\033[K")
            sys.stdout.flush()

        def clear():
            sys.stdout.write(f"\033[{len(options) + 1}F")

        is_win = os.name == 'nt'
        draw()
        try:
            while True:
                if is_win:
                    key = msvcrt.getch()
                    if key in (b'\x00', b'\xe0'):
                        key = msvcrt.getch()
                        if key == b'H': cursor = max(0, cursor - 1)
                        elif key == b'P': cursor = min(len(options) - 1, cursor + 1)
                    elif key == b' ': state[options[cursor]][0] = not state[options[cursor]][0]
                    elif key in (b'a', b'A'): state[options[cursor]][1] = not state[options[cursor]][1]
                    elif key == b'\r': break
                else:
                    import tty, termios
                    fd = sys.stdin.fileno()
                    old_settings = termios.tcgetattr(fd)
                    try:
                        tty.setraw(sys.stdin.fileno())
                        key = sys.stdin.read(1)
                        if key == '\x1b':
                            sys.stdin.read(1); k = sys.stdin.read(1)
                            if k == 'A': cursor = max(0, cursor - 1)
                            elif k == 'B': cursor = min(len(options) - 1, cursor + 1)
                        elif key == ' ': state[options[cursor]][0] = not state[options[cursor]][0]
                        elif key in ('a', 'A'): state[options[cursor]][1] = not state[options[cursor]][1]
                        elif key in ('\r', '\n'): break
                    finally: termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                clear()
                draw()
        finally: sys.stdout.write("\033[?25h\n\n")
        return state

    @staticmethod
    def interactive_file_menu(title, options):
        if not options: return []
        selected = set(range(len(options)))
        cursor = 0
        sys.stdout.write("\033[?25l")
        
        term_width = shutil.get_terminal_size((80, 20)).columns
        col_width = 42
        cols = max(1, term_width // col_width)
        rows = math.ceil(len(options) / cols)
        
        def draw():
            sys.stdout.write(f"\r{CYAN}[*] {title}{RESET}\n")
            for r in range(rows):
                line = ""
                for c in range(cols):
                    idx = r * cols + c
                    if idx < len(options):
                        opt = options[idx]
                        prefix = f"{MAGENTA}>" if idx == cursor else " "
                        check = f"{GREEN}[x]" if idx in selected else f"\033[90m[ ]"
                        display_opt = opt if len(opt) <= col_width - 8 else "..." + opt[-(col_width - 11):]
                        line += f"{prefix} {check} {display_opt}".ljust(col_width)
                sys.stdout.write(f"\r{line}\033[K\n")
            sys.stdout.write(f"\r{YELLOW}  (Arrows: Navigate grid, Space: Toggle, Enter: Confirm){RESET}\033[K")
            sys.stdout.flush()

        def clear():
            sys.stdout.write(f"\033[{rows + 1}F")

        is_win = os.name == 'nt'
        draw()
        try:
            while True:
                if is_win:
                    key = msvcrt.getch()
                    if key in (b'\x00', b'\xe0'):
                        key = msvcrt.getch()
                        if key == b'H': cursor = max(0, cursor - cols)
                        elif key == b'P': cursor = min(len(options) - 1, cursor + cols)
                        elif key == b'K': cursor = max(0, cursor - 1)
                        elif key == b'M': cursor = min(len(options) - 1, cursor + 1)
                    elif key == b' ':
                        if cursor in selected: selected.remove(cursor)
                        else: selected.add(cursor)
                    elif key == b'\r': break
                else:
                    import tty, termios
                    fd = sys.stdin.fileno()
                    old_settings = termios.tcgetattr(fd)
                    try:
                        tty.setraw(sys.stdin.fileno())
                        key = sys.stdin.read(1)
                        if key == '\x1b':
                            sys.stdin.read(1); k = sys.stdin.read(1)
                            if k == 'A': cursor = max(0, cursor - cols)
                            elif k == 'B': cursor = min(len(options) - 1, cursor + cols)
                            elif k == 'C': cursor = min(len(options) - 1, cursor + 1)
                            elif k == 'D': cursor = max(0, cursor - 1)
                        elif key == ' ':
                            if cursor in selected: selected.remove(cursor)
                            else: selected.add(cursor)
                        elif key in ('\r', '\n'): break
                    finally: termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                clear()
                draw()
        finally: sys.stdout.write("\033[?25h\n\n")
        return [options[i] for i in sorted(list(selected))]

    @staticmethod
    def trigger_gui_folder_selector(title_text, initial_dir):
        gui_folder = None
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            gui_folder = filedialog.askdirectory(title=title_text, initialdir=initial_dir)
            root.destroy()
        except Exception: pass
        return gui_folder

    @staticmethod
    def trigger_gui_file_selector(title_text, initial_dir, filetypes):
        gui_file = None
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            gui_file = filedialog.askopenfilename(title=title_text, initialdir=initial_dir, filetypes=filetypes)
            root.destroy()
        except Exception: pass
        return gui_file

# =============================================================================
# SECURITY & CRYPTOGRAPHIC SIGNING UTILITIES
# =============================================================================
def sign_and_embed_manifest_keys(target_dir):
    """
    Loads or generates Ed25519 keys at F:\\Gaming\\Godot\\Requirements\\WindowsExport\\keys,
    digitally signs the canonical manifest.json payload, and attaches the base64 signature.
    """
    key_dir = r"F:\Gaming\Godot\Requirements\WindowsExport\keys"
    os.makedirs(key_dir, exist_ok=True)
    priv_path = os.path.join(key_dir, "ed25519_private.pem")
    pub_path = os.path.join(key_dir, "ed25519_public.pem")

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    if os.path.exists(priv_path) and os.path.exists(pub_path):
        with open(priv_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        with open(pub_path, "rb") as f:
            public_key_pem = f.read().decode('utf-8')
    else:
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        
        priv_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        pub_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with open(priv_path, "wb") as f:
            f.write(priv_bytes)
        with open(pub_path, "wb") as f:
            f.write(pub_bytes)
        public_key_pem = pub_bytes.decode('utf-8')

    manifest_path = os.path.join(target_dir, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        
        manifest_data.pop("signature", None)
        canonical_bytes = json.dumps(manifest_data, sort_keys=True).encode('utf-8')
        signature = private_key.sign(canonical_bytes)
        manifest_data["signature"] = base64.b64encode(signature).decode('utf-8')

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=4)
        print(f"{GREEN}[+] Manifest successfully signed with Ed25519 private key!{RESET}")
    
    print(f"{CYAN}[*] Public Key generated at: {pub_path}{RESET}")
    return public_key_pem

# =============================================================================
# ENVIRONMENT & WORKSPACE MANAGERS
# =============================================================================
def detect_project_settings():
    print(f"\n{CYAN}[*] Analyzing Workspace & Auto-Detecting Configuration...{RESET}")
    
    default_app = Path.cwd().name or "MyApplication"
    potential_mains = []
    
    for f in os.listdir("."):
        if f.endswith(".py") and f not in BuildConfig.EXCLUDE_FROM_BUILD:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as file:
                    content = file.read()
                    if "if __name__ == " in content or "if __name__==" in content: potential_mains.append(f)
            except Exception: pass
            
    default_main = "main.py" if "main.py" in potential_mains else "app.py" if "app.py" in potential_mains else potential_mains[0] if potential_mains else "main.py"
    
    BuildConfig.APP_NAME = input(f"{YELLOW} -> Enter Application Name [{default_app}]: {RESET}").strip() or default_app
    BuildConfig.MAIN_SCRIPT = input(f"{YELLOW} -> Enter Main Script [{default_main}]: {RESET}").strip() or default_main
    BuildConfig.ATTACH_CONSOLE = False if input(f"{YELLOW} -> Attach Command Prompt to App? (Y/n) [Y]: {RESET}").strip().lower() == 'n' else True
    BuildConfig.USE_VENV = False if input(f"{YELLOW} -> Use Clean Virtual Environment for minimal build size? (Y/n) [Y]: {RESET}").strip().lower() == 'n' else True

    if input(f"{YELLOW} -> Do you want to set a Custom Isolated Build Directory? (Current: {BuildConfig.ISOLATED_BUILD_DIR}) (y/N): {RESET}").strip().lower() == 'y':
        gui_folder = UIAesthetics.trigger_gui_folder_selector("Select Custom Isolated Build Directory", str(Path.cwd()))
        if gui_folder:
            try:
                rel = str(Path(gui_folder).relative_to(Path.cwd()))
                BuildConfig.ISOLATED_BUILD_DIR = rel if not rel.startswith("..") else gui_folder
            except ValueError:
                BuildConfig.ISOLATED_BUILD_DIR = gui_folder
        else:
            iso_in = input(f"{YELLOW} -> Enter manually [{BuildConfig.ISOLATED_BUILD_DIR}]: {RESET}").strip()
            if iso_in: BuildConfig.ISOLATED_BUILD_DIR = iso_in

    if input(f"{YELLOW} -> Do you want to set a Custom Final Output Directory? (Current: {BuildConfig.DIST_DIR}) (y/N): {RESET}").strip().lower() == 'y':
        gui_folder = UIAesthetics.trigger_gui_folder_selector("Select Custom Final Output Directory", str(Path.cwd()))
        if gui_folder:
            try:
                rel = str(Path(gui_folder).relative_to(Path.cwd()))
                BuildConfig.DIST_DIR = rel if not rel.startswith("..") else gui_folder
            except ValueError:
                BuildConfig.DIST_DIR = gui_folder
        else:
            d_in = input(f"{YELLOW} -> Enter manually [{BuildConfig.DIST_DIR}]: {RESET}").strip()
            if d_in: BuildConfig.DIST_DIR = d_in

    if input(f"{YELLOW} -> Do you want to set a Custom Application Icon (.ico)? (y/N): {RESET}").strip().lower() == 'y':
        gui_file = UIAesthetics.trigger_gui_file_selector("Select Application Icon (.ico)", str(Path.cwd()), [("Icon Files", "*.ico"), ("All Files", "*.*")])
        if gui_file:
            try:
                rel = str(Path(gui_file).relative_to(Path.cwd()))
                BuildConfig.ICON_FILE = rel if not rel.startswith("..") else gui_file
            except ValueError:
                BuildConfig.ICON_FILE = gui_file
        else:
            i_in = input(f"{YELLOW} -> Enter manually: {RESET}").strip()
            if i_in: BuildConfig.ICON_FILE = i_in

    BuildConfig.PRESERVE_UPDATER = False if input(f"{YELLOW} -> Preserve existing updater executable to maintain hash? (Y/n) [Y]: {RESET}").strip().lower() == 'n' else True
    BuildConfig.STRIP_METADATA = False if input(f"{YELLOW} -> Strip non-code metadata (LICENSE, README, .dist-info) to reduce size? (Y/n) [Y]: {RESET}").strip().lower() == 'n' else True

    u_in = input(f"{YELLOW} -> Enter Standalone Updater Script (leave blank to disable) []: {RESET}").strip()
    if u_in:
        BuildConfig.UPDATER_SCRIPT = u_in
        if BuildConfig.UPDATER_SCRIPT not in BuildConfig.EXCLUDE_FROM_BUILD:
            BuildConfig.EXCLUDE_FROM_BUILD.append(BuildConfig.UPDATER_SCRIPT)

    if BuildConfig.MAIN_SCRIPT not in BuildConfig.EXCLUDE_FROM_BUILD: BuildConfig.EXCLUDE_FROM_BUILD.append(BuildConfig.MAIN_SCRIPT)

def acquire_workspace_lock():
    try:
        lock_file = open(".workspace.lock", "w")
        if os.name == 'nt' and msvcrt: msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        elif fcntl: fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except OSError:
        ans = input(f"\n{YELLOW}[!] Workspace is locked by another process. Force bypass? (y/N): {RESET}").strip().lower()
        if ans == 'y':
            try:
                if Path(".workspace.lock").exists():
                    Path(".workspace.lock").unlink()
                print(f"{GREEN}[+] Lock file removed. Retrying...{RESET}")
                return acquire_workspace_lock()
            except Exception as e:
                print(f"\n{YELLOW}[!] Could not delete lock file. Bypassing lock mechanism...{RESET}")
                class DummyLock:
                    def close(self): pass
                return DummyLock()
        else:
            print(f"\n{RED}[!] BUILD ABORTED: Workspace is locked by another process.{RESET}")
            sys.exit(1)

def obliterate_distutils_hack():
    if not BuildConfig.USE_VENV: return
    venv_dir = Path(BuildConfig.ISOLATED_BUILD_DIR) / "venv"
    if not venv_dir.exists(): return
    for root, dirs, files in os.walk(str(venv_dir)):
        if "distutils-precedence.pth" in files:
            try: (Path(root) / "distutils-precedence.pth").unlink()
            except: pass

def setup_virtual_environment(prefs_dict):
    if not prefs_dict.get('USE_VENV', BuildConfig.USE_VENV): return
        
    print(f"\n{CYAN}[*] Bootstrapping Pristine Virtual Environment...{RESET}")
    venv_dir = Path(BuildConfig.ISOLATED_BUILD_DIR) / "venv"
    
    def get_exe(d): return d / "Scripts" / "python.exe" if os.name == 'nt' else d / "bin" / "python"

    needs_rebuild = True
    if venv_dir.exists():
        exe_path = get_exe(venv_dir)
        if exe_path.exists():
            print(f"{CYAN} -> Verifying health of existing VENV...{RESET}")
            obliterate_distutils_hack() 
            res = subprocess.run([str(exe_path), "-m", "pip", "--version"], capture_output=True)
            if res.returncode == 0:
                needs_rebuild = False
                print(f"{GREEN}[+] Existing Virtual Environment is perfectly healthy.{RESET}")
            else: print(f"{YELLOW}[!] Existing VENV is corrupted. Performing nuclear reset...{RESET}")
        else: print(f"{YELLOW}[!] Existing VENV is missing its executable. Performing nuclear reset...{RESET}")
            
    if needs_rebuild:
        if venv_dir.exists():
            shutil.rmtree(str(venv_dir), ignore_errors=True)
            time.sleep(1) 
        UIAesthetics.run_with_progress_bar([sys.executable, "-m", "venv", str(venv_dir)], "VENV Engine", step_info="Init")
        
    BuildConfig.PYTHON_EXE = str(get_exe(venv_dir))
    if not Path(BuildConfig.PYTHON_EXE).exists():
        print(f"{RED}[!] VENV python binary not found. Reverting to Host Python.{RESET}")
        BuildConfig.PYTHON_EXE = sys.executable
        return

    print(f"{GREEN}[+] Execution pipeline mapped to Virtual Python: {BuildConfig.PYTHON_EXE}{RESET}")
    print(f"{CYAN}[*] Sanitizing VENV and auto-healing core dependencies...{RESET}")
    obliterate_distutils_hack()
    
    res = subprocess.run([BuildConfig.PYTHON_EXE, "-m", "pip", "--version"], capture_output=True)
    if res.returncode != 0:
        print(f"{YELLOW}[!] Native VENV pip is broken. Engaging auto-healer...{RESET}")
        subprocess.run([BuildConfig.PYTHON_EXE, "-m", "ensurepip", "--default-pip"], capture_output=True)
        res2 = subprocess.run([BuildConfig.PYTHON_EXE, "-m", "pip", "--version"], capture_output=True)
        
        if res2.returncode != 0:
            try:
                print(f"{CYAN} -> Force-fetching get-pip.py from PyPA to guarantee functional pip...{RESET}")
                get_pip_path = Path(BuildConfig.ISOLATED_BUILD_DIR) / "get-pip.py"
                urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", str(get_pip_path))
                subprocess.run([BuildConfig.PYTHON_EXE, str(get_pip_path)], capture_output=True)
                get_pip_path.unlink()
            except Exception as e: print(f"{RED}[!] Ultimate pip fallback failed: {e}{RESET}")

    UIAesthetics.run_with_progress_bar([BuildConfig.PYTHON_EXE, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], "VENV Base Packages", step_info="Upgrade")
    obliterate_distutils_hack() 

def ensure_windows_opus_dll(target_dir="."):
    """Compiles 64-bit Windows opus.dll directly from local opus-1.5.2 source code."""
    target_path = Path(target_dir) / "opus.dll"
    if target_path.exists() and target_path.stat().st_size > 50000:
        return target_path

    opus_src = Path("opus-1.5.2")
    if not opus_src.exists():
        print(f"{RED}[!] opus-1.5.2 source folder not found at project root.{RESET}")
        return None

    print(f"{CYAN}[*] Compiling 64-bit Windows opus.dll from local opus-1.5.2 source...{RESET}")

    # 1. Collect all required C translation units
    c_files = []
    c_files.extend((opus_src / "src").glob("*.c"))
    c_files.extend((opus_src / "celt").glob("*.c"))
    c_files.extend((opus_src / "silk").glob("*.c"))
    c_files.extend((opus_src / "silk" / "float").glob("*.c"))

    excludes = {
        "opus_demo.c",
        "opus_custom_demo.c",
        "opus_compare.c",
        "repacketizer_demo.c",
        "trivial_example.c",
    }
    sources = [str(f.resolve()) for f in c_files if f.name not in excludes]

    include_dirs = [
        str((opus_src / "include").resolve()),
        str((opus_src / "celt").resolve()),
        str((opus_src / "silk").resolve()),
        str((opus_src / "silk" / "float").resolve()),
        str((opus_src / "win32").resolve()) if (opus_src / "win32").exists() else None,
    ]
    include_dirs = [inc for inc in include_dirs if inc]

    # MSVC specific definitions (VAR_ARRAYS omitted: MSVC uses USE_ALLOCA)
    macros = [
        ("OPUS_BUILD", "1"),
        ("DLL_EXPORT", "1"),
        ("USE_ALLOCA", "1"),
        ("HAVE_LRINT", "1"),
        ("HAVE_LRINTF", "1"),
        ("WIN32", "1"),
        ("_WIN32", "1"),
        ("_CRT_SECURE_NO_WARNINGS", "1"),
        ("_CRT_NONSTDC_NO_DEPRECATE", "1"),
    ]

    build_temp = Path("_build_opus_x64_temp")
    build_temp.mkdir(parents=True, exist_ok=True)

    # 2. Compile and link via Python's native MSVC toolchain
    try:
        try:
            from setuptools._distutils.ccompiler import new_compiler
        except ImportError:
            from distutils.ccompiler import new_compiler

        compiler = new_compiler(compiler="msvc" if os.name == "nt" else None)
        compiler.initialize()

        for inc in include_dirs:
            compiler.add_include_dir(inc)
        for name, val in macros:
            compiler.define_macro(name, val)

        print(f"{CYAN} -> Compiling {len(sources)} C translation units with MSVC...{RESET}")
        c_flags = ["/O2", "/W0", "/MD", "/EHsc"] if os.name == "nt" else ["-O3", "-fPIC"]
        objects = compiler.compile(
            sources,
            output_dir=str(build_temp.resolve()),
            extra_postargs=c_flags,
        )

        print(f"{CYAN} -> Linking opus.dll shared library...{RESET}")
        link_args = ["/DLL", "/OPT:REF", "/OPT:ICF"] if os.name == "nt" else ["-shared"]
        compiler.link_shared_lib(
            objects,
            "opus",
            output_dir=str(build_temp.resolve()),
            extra_preargs=link_args if os.name == "nt" else None,
            extra_postargs=link_args if os.name != "nt" else None,
            target_lang="c",
        )

        for generated in build_temp.rglob("*.dll"):
            if generated.name.lower() in ("opus.dll", "libopus.dll", "libopus-0.dll") or generated.stem.lower() == "opus":
                shutil.copy2(generated, target_path)
                print(f"{GREEN}[+] Successfully compiled {target_path} ({target_path.stat().st_size} bytes)!{RESET}")
                shutil.rmtree(build_temp, ignore_errors=True)
                return target_path
    except Exception as e:
        print(f"{YELLOW}[!] MSVC direct compilation encountered an issue: {e}{RESET}")

    # 3. Fallback to CMake if installed
    if shutil.which("cmake"):
        print(f"{CYAN} -> Falling back to CMake generator...{RESET}")
        try:
            cmake_dir = build_temp / "cmake_build"
            cmake_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["cmake", "-S", str(opus_src), "-B", str(cmake_dir), "-DBUILD_SHARED_LIBS=ON", "-A", "x64"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            subprocess.run(
                ["cmake", "--build", str(cmake_dir), "--config", "Release"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            for match in cmake_dir.rglob("opus.dll"):
                if match.exists() and match.stat().st_size > 50000:
                    shutil.copy2(match, target_path)
                    print(f"{GREEN}[+] Successfully compiled {target_path} via CMake!{RESET}")
                    shutil.rmtree(build_temp, ignore_errors=True)
                    return target_path
        except Exception as e2:
            print(f"{YELLOW}[!] CMake fallback failed: {e2}{RESET}")

    if target_path.exists():
        return target_path
    print(f"{RED}[!] Failed to compile opus.dll from source.{RESET}")
    return None


def ensure_build_engine(engine_name):
    print(f"\n{CYAN}[*] Verifying and bootstrapping '{engine_name}' compilation engine...{RESET}")
    deps = ["nuitka"] if engine_name == "nuitka" else ["pyinstaller"] if engine_name == "pyinstaller" else ["cython", "pyinstaller", "setuptools"] if engine_name == "cython" else ["nuitka", "pyinstaller"]
    print(f"{YELLOW} -> Explicitly installing build dependencies: {', '.join(deps)}{RESET}")
    try: 
        UIAesthetics.run_with_progress_bar([BuildConfig.PYTHON_EXE, "-m", "pip", "install", *deps], "Pip Bootstrapper", step_info="Setup")
        obliterate_distutils_hack() 
        
        if "pyinstaller" in deps and BuildConfig.USE_VENV:
            venv_dir = Path(BuildConfig.ISOLATED_BUILD_DIR) / "venv"
            hooks_dir = venv_dir / "Lib" / "site-packages" / "PyInstaller" / "hooks"
            if hooks_dir.exists():
                for file in os.listdir(str(hooks_dir)):
                    if file.endswith(".py"):
                        filepath = hooks_dir / file
                        try:
                            with open(filepath, "r", encoding="utf-8") as f: content = f.read()
                            new_content = content
                            for match in re.finditer(r'([a-zA-Z0-9_]+)\.version\s*([<>]=?)\s*\(', content):
                                orig = match.group(0)
                                replacement = f"{match.group(1)}.version is not None and {orig}"
                                if replacement not in new_content:
                                    new_content = new_content.replace(orig, replacement)
                            if new_content != content:
                                with open(filepath, "w", encoding="utf-8") as f: f.write(new_content)
                        except Exception: pass
                        
            bindepend_path = venv_dir / "Lib" / "site-packages" / "PyInstaller" / "depend" / "bindepend.py"
            if bindepend_path.exists():
                try:
                    with open(bindepend_path, "r", encoding="utf-8") as f: content = f.read()
                    if "# AUTO-PATCHED" not in content:
                        new_content = re.sub(
                            r'(\n\s+)(pe\s*=\s*pefile\.PE\([^)]*\))',
                            r'\1try:\1    \2 # AUTO-PATCHED\1except Exception:\1    return []',
                            content
                        )
                        if new_content != content:
                            with open(bindepend_path, "w", encoding="utf-8") as f: f.write(new_content)
                except Exception: pass
    except Exception as e:
        print(f"{RED}[!] Critical Error: Failed to bootstrap compilation engine via pip. {e}{RESET}")
        sys.exit(1)

# =============================================================================
# DEPENDENCY & DIRECTIVE MANAGERS
# =============================================================================
class UniversalDependencyManager:
    def __init__(self):
        self.dynamic_map = {}
        self.stdlib = self._get_stdlib()
        self._build_dynamic_map()

    def sync_dependencies_surgically(self, final_reqs, target_python_exe):
        print(f"{CYAN}[*] Live-Scanning VENV & Synchronizing Dependencies...{RESET}")
        
        # 1. Normalize desired requirements (strip versions and operators)
        desired_map = {}
        for req in final_reqs:
            # Clean out operators to get the absolute base package name
            clean_req = re.split(r'==|>=|<=|~=|>|<', req)[0].strip().lower().replace('_', '-')
            if clean_req:
                desired_map[clean_req] = req # Map base name to full constraint

        # 2. Safely query ACTUALLY installed packages using pip list JSON (bypasses freeze string bugs)
        try:
            res = subprocess.run([target_python_exe, "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], capture_output=True, text=True)
            installed_data = json.loads(res.stdout)
            installed = {pkg['name'].lower().replace('_', '-') for pkg in installed_data}
        except Exception:
            # Fallback to freeze if JSON formatting fails on extremely old pip versions
            res = subprocess.run([target_python_exe, "-m", "pip", "freeze", "--local"], capture_output=True, text=True)
            installed = set()
            for line in res.stdout.splitlines():
                if line.startswith('-e') or '@' in line:
                    pkg_name = line.split('=')[0].split('@')[0].replace('-e', '').strip().lower()
                else:
                    pkg_name = re.split(r'==|>=|<=|~=|>|<', line)[0].strip().lower()
                installed.add(pkg_name.replace('_', '-'))

        # Core python setup tools that should NEVER be wiped
        core_packages = {
            'pip', 'setuptools', 'wheel', 'ensurepip',
            # Build Engines
            'pyinstaller', 'nuitka', 'cython',
            # PyInstaller transitive dependencies
            'pefile', 'altgraph', 'pyinstaller-hooks-contrib', 'packaging', 'macholib', 'pywin32-ctypes',
            # Nuitka transitive dependencies
            'ordered-set', 'zstandard'
        }
        desired_bases = set(desired_map.keys())

        # 3. Calculate absolute physical differences
        to_uninstall = installed - desired_bases - core_packages
        to_install_bases = desired_bases - installed

        # 4. ANNIHILATE Orphaned/Extra Packages
        if to_uninstall:
            print(f"{YELLOW} -> Annihilating {len(to_uninstall)} orphaned/extra packages: {', '.join(to_uninstall)}{RESET}")
            uninstall_list = list(to_uninstall)
            # Chunk to prevent OS command line length limits crashing the script
            for i in range(0, len(uninstall_list), 20):
                chunk = uninstall_list[i:i+20]
                subprocess.run([target_python_exe, "-m", "pip", "uninstall", "-y", *chunk], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 5. INSTALL Missing Dependencies
        if to_install_bases:
            exact_install_reqs = [desired_map[base] for base in to_install_bases]
            print(f"{YELLOW} -> Installing {len(exact_install_reqs)} missing packages individually to prevent halt on error...{RESET}")
            
            failed_packages = []
            total_reqs = len(exact_install_reqs)
            for idx, req in enumerate(exact_install_reqs, 1):
                try:
                    UIAesthetics.run_with_progress_bar(
                        [target_python_exe, "-m", "pip", "install", req], 
                        f"Pip Sync ({req})", 
                        step_info=f"{idx}/{total_reqs}"
                    )
                except Exception as e:
                    failed_packages.append(req)
                    print(f"{RED}[!] Skipped {req} due to installation error: {e}{RESET}")

            if failed_packages:
                print(f"{YELLOW}[!] Skipped {len(failed_packages)} incompatible/failed package(s):\n    {', '.join(failed_packages)}{RESET}")

        if not to_uninstall and not to_install_bases:
            print(f"{GREEN}[+] VENV is already perfectly synchronized.{RESET}")
        else:
            print(f"{GREEN}[+] Environment Surgical Sync Complete!{RESET}")

    def _get_stdlib(self):
        try:
            if hasattr(sys, 'stdlib_module_names'):
                return set(sys.stdlib_module_names)
        except Exception:
            pass
        return set(["os", "sys", "math", "time", "subprocess", "itertools", "threading", "shutil", 
                    "glob", "ast", "json", "urllib", "re", "random", "datetime", "collections", "logging", "typing", "importlib"])

    def _build_dynamic_map(self):
        try:
            import importlib.metadata
            for dist in importlib.metadata.distributions():
                pkg_name = dist.metadata.get('Name')
                if not pkg_name: continue
                try:
                    top_levels = dist.read_text('top_level.txt')
                    if top_levels:
                        for top in top_levels.split():
                            self.dynamic_map[top] = pkg_name
                except Exception: pass
        except Exception: pass

    def get_pip_name(self, import_name):
        if import_name in self.dynamic_map:
            return self.dynamic_map[import_name]
        return import_name.replace('_dummy', '-')
        
    def extract_transitive_closure(self, base_packages):
        import importlib.metadata
        exhaustive = set(base_packages)
        queue = list(base_packages)
        processed = set()
        req_pattern = re.compile(r'^([a-zA-Z0-9_\-\.]+)')
        
        while queue:
            pkg = queue.pop(0)
            if pkg in processed: continue
            processed.add(pkg)
            
            try:
                reqs = importlib.metadata.requires(pkg) or []
                for req in reqs:
                    if 'extra ==' in req or 'extra==' in req: 
                        continue
                    
                    # Filter out macOS/Darwin or non-Windows dependencies when on Windows
                    if os.name == 'nt':
                        if 'sys_platform == "darwin"' in req or "sys_platform == 'darwin'" in req:
                            continue
                        if 'platform_system == "Darwin"' in req or "platform_system == 'Darwin'" in req:
                            continue
                        if 'os_name == "posix"' in req or "os_name == 'posix'" in req:
                            continue

                    match = req_pattern.match(req)
                    if match:
                        dep_pkg = match.group(1)
                        if dep_pkg not in exhaustive:
                            exhaustive.add(dep_pkg)
                            queue.append(dep_pkg)
            except Exception: 
                pass
            
            try:
                for dist in importlib.metadata.distributions():
                    dist_name = dist.metadata.get('Name')
                    if not dist_name or dist_name in exhaustive: continue
                    
                    eps = dist.entry_points
                    if eps:
                        for ep in eps:
                            group_base = ep.group.split('.')[0] if hasattr(ep, 'group') else ''
                            if group_base in exhaustive or group_base.replace('-', '_') in exhaustive:
                                exhaustive.add(dist_name)
                                queue.append(dist_name) 
                                break
            except Exception: 
                pass
            
        return exhaustive

    def resolve_and_sync(self, used_imports, local_modules, target_python_exe):
        print(f"\n{CYAN}[*] Resolving dynamic dependencies & building transitive graph...{RESET}")
        
        script_excludes = set(ex.replace(".py", "") for ex in BuildConfig.EXCLUDE_FROM_BUILD)
        direct_pip_packages = set()

        # Hardcoded dictionary for notorious packages that don't match their import names
        COMMON_MAPPINGS = {
            'fitz': 'pymupdf',          # Translates 'import fitz' -> pip install pymupdf
            'cv2': 'opencv-python', 
            'PIL': 'Pillow', 
            'bs4': 'beautifulsoup4', 
            'sklearn': 'scikit-learn', 
            'yaml': 'pyyaml', 
            'dotenv': 'python-dotenv', 
            'win32com': 'pywin32', 
            'win32api': 'pywin32', 
            'win32gui': 'pywin32', 
            'win32con': 'pywin32', 
            'win32clipboard': 'pywin32', 
            'jwt': 'PyJWT', 
            'github': 'PyGithub', 
            'dateutil': 'python-dateutil', 
            'pydantic_core': 'pydantic-core', 
            'flask_sqlalchemy': 'Flask-SQLAlchemy',
            'sqlalchemy': 'SQLAlchemy', 
            'wx': 'wxPython', 
            'PyQt5': 'PyQt5', 
            'PyQt6': 'PyQt6'
        }
        for imp in used_imports:
            top_level_imp = imp.split('.')[0]
            if (top_level_imp in self.stdlib or top_level_imp.startswith("_dummy") or 
                top_level_imp in local_modules or top_level_imp in script_excludes): continue
            
            pip_pkg = COMMON_MAPPINGS.get(top_level_imp) or self.dynamic_map.get(top_level_imp) or top_level_imp.replace('_dummy', '-')
            direct_pip_packages.add(pip_pkg)

        print(f"{CYAN} -> Mapping exhaustive dependency tree...{RESET}")
        transitive_packages = self.extract_transitive_closure(direct_pip_packages)
        all_required_packages = direct_pip_packages.union(transitive_packages)
        
        req_file = "requirements.txt"
        existing_reqs = set()
        if Path(req_file).exists():
            with open(req_file, "r", encoding="utf-8") as f:
                for line in f:
                    pkg = line.strip().split("==")[0].split(">=")[0].split("~=")[0].strip()
                    if pkg and not pkg.startswith("#"): existing_reqs.add(pkg)

        final_reqs = set(existing_reqs)
        existing_lower = set(r.lower() for r in existing_reqs)
        
        for pkg in all_required_packages:
            if pkg.lower() not in existing_lower:
                final_reqs.add(pkg)
        pruned_reqs = set()
        obsolete_pkgs = set(["pathlib", "pathlib2"])
        if sys.version_info >= (3, 9):
            obsolete_pkgs.update(["backports.zoneinfo", "backports-zoneinfo"])
        if sys.version_info >= (3, 10):
            obsolete_pkgs.update(["importlib-metadata", "importlib_metadata", "importlib-resources", "importlib_resources"])

        for req in final_reqs:
            clean_req = req.split("==")[0].split(">=")[0].split("~=")[0].strip().lower()
            if clean_req not in obsolete_pkgs:
                pruned_reqs.add(req)
                
        final_reqs = pruned_reqs

        print(f"{YELLOW} -> Generated absolute requirements graph ({len(final_reqs)} modules) to {req_file}{RESET}")
        with open(req_file, "w", encoding="utf-8") as f:
            for req in sorted(final_reqs, key=lambda x: x.lower()): f.write(f"{req}\n")

        try:
            self.sync_dependencies_surgically(final_reqs, target_python_exe)
            obliterate_distutils_hack() 
        except Exception as e: 
            print(f"{RED}[!] Error synchronizing pip: {e}{RESET}")

        final_imports = set(used_imports)
        for t_pkg in transitive_packages:
            import_name = t_pkg.replace('-', '_dummy')
            for k, v in self.dynamic_map.items():
                if v == t_pkg: import_name = k; break
            final_imports.add(import_name)
            
        print(f"{GREEN}[+] Fully mapped and configured {len(transitive_packages)} transitive dependencies!{RESET}")
        return transitive_packages, final_imports

DepEngine = UniversalDependencyManager()

class CompilerDirectiveEngine:
    local_modules = set()

    @classmethod
    def generate_directives(cls, project_imports, transitive_packages):
        directives = {"metadata": set(), "hidden_imports": set()}
        import importlib.util
        
        METADATA_BLACKLIST = {'pyqt5', 'pyqt6', 'pyside2', 'pyside6', 'wx', 'cv2', 'opencv-python', 'numpy', 'scipy'}
        
        def get_valid_metadata(candidates):
            valid = set()
            clean_candidates = [c for c in candidates if c.lower() not in METADATA_BLACKLIST]
            if not clean_candidates: return valid
            
            check_script = "import sys, json; " \
                "import importlib.metadata as meta; " \
                "valid = []; " \
                "for pkg in sys.argv[1:]: " \
                "  try: meta.distribution(pkg); valid.append(pkg) " \
                "  except Exception: pass; " \
                "print(json.dumps(valid))"
            try:
                res = subprocess.run([BuildConfig.PYTHON_EXE, "-c", check_script] + clean_candidates, capture_output=True, text=True)
                if res.returncode == 0:
                    valid.update(json.loads(res.stdout.strip()))
            except Exception: pass
            return valid

        metadata_candidates = set(transitive_packages)
        for imp in project_imports:
            top_lvl = imp.split('.')[0]
            if top_lvl not in cls.local_modules and top_lvl not in sys.stdlib_module_names and top_lvl not in DepEngine.stdlib and not top_lvl.startswith('_dummy'):
                metadata_candidates.add(top_lvl)
                pip_name = DepEngine.get_pip_name(top_lvl)
                if pip_name != top_lvl: metadata_candidates.add(pip_name)
                
        directives["metadata"] = get_valid_metadata(metadata_candidates)
        
        if "keyring" in project_imports or "keyring" in transitive_packages:
            directives["metadata"].add("keyring")
            directives["hidden_imports"].update([
                "keyring.backends",
                "keyring.backends.Windows",
                "keyring.backends.macOS",
                "keyring.backends.SecretService",
                "keyring.backends.kwallet",
                "keyring.backends.chainer",
                "keyring.backends.libsecret",
                "pywin32_ctypes",               
                "pywin32_ctypes.core"           
            ])
            # Add this inside generate_directives in build.py
        if "cryptography" in project_imports or "cryptography" in transitive_packages:
            directives["metadata"].add("cryptography")
            directives["hidden_imports"].update([
                "cryptography",
                "cryptography.hazmat.primitives.asymmetric",
                "cryptography.hazmat.primitives.asymmetric.ed25519",
                "cryptography.hazmat.primitives.asymmetric.padding",
                "cryptography.hazmat.primitives.serialization",
                "cryptography.hazmat.backends",
                "cryptography.hazmat.backends.openssl",
            ])
            if "cryptography" not in BuildConfig.COLLECT_DATA_PACKAGES:
                BuildConfig.COLLECT_DATA_PACKAGES.append("cryptography")
            if "cryptography" not in BuildConfig.COLLECT_SUBMODULES:
                BuildConfig.COLLECT_SUBMODULES.append("cryptography")

        if "opuslib" in project_imports or "opuslib" in transitive_packages or "opus" in project_imports:
            directives["metadata"].add("opuslib")
            directives["hidden_imports"].update([
                "opuslib",
                "opuslib.api",
                "opuslib.api.encoder",
                "opuslib.api.decoder",
                "opuslib.api.constants",
                "opuslib.api.ctl",
                "opuslib.exceptions"
            ])
            if "opuslib" not in BuildConfig.COLLECT_DATA_PACKAGES:
                BuildConfig.COLLECT_DATA_PACKAGES.append("opuslib")
            if "opuslib" not in BuildConfig.COLLECT_SUBMODULES:
                BuildConfig.COLLECT_SUBMODULES.append("opuslib")
            
        for imp in set(list(project_imports) + list(transitive_packages)):
            top_lvl = imp.split('.')[0]
            if top_lvl in cls.local_modules: continue
            
            # MATURE FIX: Explicitly enforce the full parsed import path.
            # This cures the PyInstaller .pyd blindness for nested standard libraries.
            directives["hidden_imports"].add(imp)
            
            if top_lvl in sys.stdlib_module_names or top_lvl in DepEngine.stdlib:
                try:
                    spec = importlib.util.find_spec(top_lvl)
                    if spec and spec.submodule_search_locations:
                        for f in os.listdir(spec.submodule_search_locations[0]):
                            if f.endswith('.py') and not f.startswith('__'): directives["hidden_imports"].add(f"{top_lvl}.{f[:-3]}")
                except Exception: pass
            try:
                spec = importlib.util.find_spec(top_lvl)
                if spec and spec.submodule_search_locations:
                    base_path = spec.submodule_search_locations[0]
                    for root, _dummy, files in os.walk(base_path):
                        for f in files:
                            if f.endswith(('.pyd', '.so')):
                                directives["hidden_imports"].add(top_lvl)
                                try:
                                    rel_path = os.path.relpath(root, base_path)
                                    mod_path = rel_path.replace(os.sep, '.') if rel_path != '.' else ''
                                    base_name = f.split('.')[0]
                                    
                                    if mod_path:
                                        directives["hidden_imports"].add(f"{top_lvl}.{mod_path}.{base_name}")
                                    else:
                                        directives["hidden_imports"].add(f"{top_lvl}.{base_name}")
                                except Exception: pass
            except Exception: pass
        if 'cffi' in project_imports or 'cffi' in transitive_packages: directives["hidden_imports"].update(['_cffi_backend', 'cffi.backend_ctypes'])
        return directives

# =============================================================================
# ASSET DISCOVERY & AST SCANNER
# =============================================================================
def generate_default_audio_icon(output_path="icon.ico") -> str:
    """Programmatically generates a multi-resolution audio symbol icon (.ico)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], stdout=subprocess.DEVNULL)
        from PIL import Image, ImageDraw

    size = (256, 256)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Dark Modern Rounded Container
    draw.rounded_rectangle([8, 8, 248, 248], radius=52, fill=(18, 22, 32, 255), outline=(0, 229, 255, 230), width=6)

    # 2. Speaker Chassis (Cabinet & Cone)
    draw.rounded_rectangle([42, 94, 82, 162], radius=8, fill=(0, 229, 255, 255))
    draw.polygon([(82, 94), (140, 56), (140, 200), (82, 162)], fill=(0, 229, 255, 255))

    # 3. Audio Transmission Radiating Waves
    draw.arc([110, 88, 172, 168], start=-55, end=55, fill=(46, 204, 113, 255), width=10)
    draw.arc([100, 60, 206, 196], start=-55, end=55, fill=(46, 204, 113, 255), width=10)
    draw.arc([90, 32, 240, 224], start=-55, end=55, fill=(46, 204, 113, 255), width=10)

    # 4. Equalizer Frequency Bars (Bottom Sub-indicator)
    eq_bars = [
        (54, 192, 68, 220),
        (80, 180, 94, 220),
        (106, 195, 120, 220),
        (132, 172, 146, 220),
        (158, 185, 172, 220),
        (184, 198, 198, 220)
    ]
    for bar in eq_bars:
        draw.rounded_rectangle(bar, radius=4, fill=(0, 229, 255, 190))

    resolutions = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(output_path, format="ICO", sizes=resolutions)
    print(f"{GREEN}[+] Programmatically generated audio application icon: {output_path}{RESET}")
    return output_path


def find_app_icon():
    exclude_dirs = {".git", "__pycache__", "venv", "env", ".venv", "dist", BuildConfig.DIST_DIR, "build", BuildConfig.ISOLATED_BUILD_DIR}
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
        for file in files:
            if file.lower().endswith(".ico"): return str(Path(root) / file)
    
    # Auto-generate icon if none exists
    return generate_default_audio_icon("icon.ico")

def discover_compilation_targets():
    targets = []
    exclude_dirs = [".git", "__pycache__", "venv", "env", ".venv", "dist", BuildConfig.DIST_DIR, "build", BuildConfig.ISOLATED_BUILD_DIR]
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
        for file in files:
            if file.endswith(".py") and file not in BuildConfig.EXCLUDE_FROM_BUILD and file != "__init__.py":
                targets.append(str(Path(root) / file))
    return targets

def auto_discover_assets():
    exclude_dirs = {".git", "__pycache__", "venv", "env", ".venv", "dist", BuildConfig.DIST_DIR, "build", BuildConfig.ISOLATED_BUILD_DIR, ".vscode", ".idea"}
    exclude_files = {".workspace.lock", "requirements.txt", BuildConfig.PREFS_FILE}.union(BuildConfig.EXCLUDE_FROM_BUILD)
    if BuildConfig.ICON_FILE:
        exclude_files.add(Path(BuildConfig.ICON_FILE).name)
        BuildConfig.DATA_FILES.append((BuildConfig.ICON_FILE, "."))
        
    exclude_exts = {".spec", ".gitignore", ".gitattributes", ".log", ".bak", ".pyc", ".py", ".pyi", ".c", ".cpp", ".h", ".pyd", ".so", ".pyx", ".pyw"}

    for item in os.listdir("."):
            if Path(item).is_dir() and item not in exclude_dirs and not item.startswith('.'):
                if item == "dynamic_settings_modules":
                    continue
                if any(f.endswith('.py') for root, _dummy, files in os.walk(item) for f in files): BuildConfig.PLUGIN_DIRECTORIES.append(item)
                else: BuildConfig.DATA_DIRECTORIES.append((item, item))

    for root, dirs, files in os.walk("."):
        if any(root == d_src or root.startswith(d_src + os.sep) for d_src, _dummy in BuildConfig.DATA_DIRECTORIES): continue
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in exclude_dirs and not d.endswith('.build')]
        for file in files:
            if file in exclude_files or any(file.endswith(ext) for ext in exclude_exts) or file == "__init__.py": continue
            src_path = str(Path(root) / file)
            dst_path = str(Path(root).relative_to(".")) if root != "." else "."
            if not any(src_path == existing[0] for existing in BuildConfig.DATA_FILES): BuildConfig.DATA_FILES.append((src_path, dst_path))

# In build.py

def _parse_single_file(filepath):
    found = set()
    dangerous_top_level = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f: 
            tree = ast.parse(f.read(), filename=filepath)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names: 
                    found.add(alias.name.split('.')[0])
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                # CRITICAL: Skip relative imports (e.g. from .tab_workspace_manager import ...)
                if node.level and node.level > 0:
                    continue
                if node.module:
                    found.add(node.module.split('.')[0])
                    found.add(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == '__import__' and node.args and isinstance(node.args[0], ast.Constant): 
                    found.add(node.args[0].value.split('.')[0])
                elif isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module' and node.args and isinstance(node.args[0], ast.Constant): 
                    found.add(node.args[0].value.split('.')[0])
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names: 
                    dangerous_top_level.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    dangerous_top_level.add(node.module.split('.')[0])
    except Exception: 
        pass 
    return filepath, found, dangerous_top_level

def scan_project_imports(target_files, target_directories):
    all_files = [str(Path(f).resolve()) for f in target_files]
    local_modules = set()
    root_abs = str(Path.cwd().resolve())

    # Add all top-level project folders and files to local_modules
    for item in os.listdir("."):
        if os.path.isdir(item) and not item.startswith('.'):
            local_modules.add(item)
            local_modules.add(item.replace('-', '_'))

    for directory in ["."] + target_directories:
        dir_abs = str(Path(directory).resolve())
        if not Path(dir_abs).exists(): continue
        for root, dirs, files in os.walk(dir_abs):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['dist', BuildConfig.DIST_DIR, 'build', 'venv', 'env', '__pycache__', BuildConfig.ISOLATED_BUILD_DIR]]
            for file in files:
                if file.endswith(".py") and file not in BuildConfig.EXCLUDE_FROM_BUILD:
                    full_path = str(Path(root) / file)
                    if full_path not in all_files: 
                        all_files.append(full_path)
                    
                    # Add base module file stem (e.g. 'tab_workspace_manager')
                    stem = Path(file).stem
                    local_modules.add(stem)
                    
                    # Add parent folder name
                    rel_parts = Path(full_path).relative_to(root_abs).parts
                    if len(rel_parts) > 1:
                        local_modules.add(rel_parts[0])
                        
                    mod_name = str(Path(full_path).relative_to(root_abs)).replace(os.sep, ".")[:-3]
                    if mod_name and mod_name != BuildConfig.MAIN_SCRIPT.replace(".py", ""):
                        local_modules.add(mod_name.split('.')[0])
                        local_modules.add(mod_name)

    found_imports = set(local_modules) 
    main_module_name = BuildConfig.MAIN_SCRIPT.replace(".py", "")
    critical_import_cycle_detected = []

    print(f"{CYAN}[*] Engaging {BuildConfig.CPU_CORES} Processes for AST Analysis...{RESET}")
    with concurrent.futures.ProcessPoolExecutor(max_workers=BuildConfig.CPU_CORES) as executor:
        for filepath, imports, dangerous_top in executor.map(_parse_single_file, all_files):
            found_imports.update(imports)
            if main_module_name in dangerous_top and not filepath.endswith(BuildConfig.MAIN_SCRIPT) and filepath not in critical_import_cycle_detected: 
                critical_import_cycle_detected.append(filepath)
    return found_imports, all_files, local_modules, critical_import_cycle_detected

def populate_dynamic_collections(used_imports, transitive_packages):
    import importlib.util
    import importlib.metadata

    VAULTED_PACKAGES = {
        'pyqtgraph', 'numpy', 'scipy', 'pandas', 'matplotlib', 
        'pyqt5', 'pyqt6', 'pyside2', 'pyside6', 'pil', 'pillow', 'cv2', 'timezonefinderL'
    }

    plugin_providers = set()
    try:
        for dist in importlib.metadata.distributions():
            if dist.entry_points:
                name = dist.metadata.get('Name', '').replace('-', '_')
                plugin_providers.add(name)
    except Exception: pass

    for imp in set(list(used_imports) + list(transitive_packages)):
        top_lvl = imp.split('.')[0]
        if top_lvl in sys.stdlib_module_names or top_lvl in DepEngine.stdlib or top_lvl.startswith('_dummy'): continue
        
        if top_lvl.lower() in VAULTED_PACKAGES:
            continue

        if top_lvl in plugin_providers or imp in plugin_providers:
            if top_lvl not in BuildConfig.COLLECT_SUBMODULES:
                BuildConfig.COLLECT_SUBMODULES.append(top_lvl)

        try:
            spec = importlib.util.find_spec(top_lvl)
            if spec and spec.submodule_search_locations:
                mod_dir = spec.submodule_search_locations[0]
                needs_data, sub_dirs = False, 0
                for root, dirs, files in os.walk(mod_dir):
                    sub_dirs += len(dirs)
                    if not needs_data:
                        for f in files:
                            if not f.endswith(('.py', '.pyc', '.pyd', '.so', '.dll', '.dist-info', '.egg-info', '.pyi')):
                                needs_data = True; break
                    if needs_data and sub_dirs > 1: break
                if needs_data and top_lvl not in BuildConfig.COLLECT_DATA_PACKAGES: BuildConfig.COLLECT_DATA_PACKAGES.append(top_lvl)
                if sub_dirs > 1 and top_lvl not in BuildConfig.COLLECT_SUBMODULES: BuildConfig.COLLECT_SUBMODULES.append(top_lvl)
        except Exception: pass
        
    for notorious in ['timezonefinder', 'timezonefinderL']:
        if notorious in used_imports or notorious in transitive_packages:
            if notorious not in BuildConfig.COLLECT_DATA_PACKAGES: BuildConfig.COLLECT_DATA_PACKAGES.append(notorious)
            if notorious not in BuildConfig.COLLECT_SUBMODULES: BuildConfig.COLLECT_SUBMODULES.append(notorious)

def generate_smart_excludes(used_imports):
    excludes = set()    
    HEAVY_DATA_ML = ["pandas", "scipy", "matplotlib", "seaborn", "sklearn", "tensorflow", "keras", "torch", "h5py", "cv2", "PIL"]    
    
    # FIX: Removed 'unittest' from this list because matplotlib/pyparsing depends on it
    HEAVY_DEV_WEB = ["boto3", "django", "flask", "IPython", "notebook", "jupyter", "pytest", "pdb", "twisted", "aiohttp", "requests", "setuptools", "pkg_resources"]    
    
    HEAVY_GUI = ["PySide2", "PySide6", "PyQt5", "tkinter", "wx", "kivy"]
    PYQT6_BLOAT = ["PyQt6.QtWebEngine", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtNetwork", "PyQt6.QtSql", "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickWidgets", "PyQt6.QtDBus", "PyQt6.QtBluetooth", "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtTest", "PyQt6.QtXml", "PyQt6.QtWebChannel", "PyQt6.QtWebSockets", "PyQt6.Qt3DCore", "PyQt6.Qt3DRender", "PyQt6.QtPrintSupport", "PyQt6.QtDesigner", "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets"]
    
    if "pyqtgraph" in used_imports:
        if "PyQt6.QtOpenGL" in PYQT6_BLOAT: PYQT6_BLOAT.remove("PyQt6.QtOpenGL")
        if "PyQt6.QtOpenGLWidgets" in PYQT6_BLOAT: PYQT6_BLOAT.remove("PyQt6.QtOpenGLWidgets")
        
    for bloat in HEAVY_DATA_ML + HEAVY_DEV_WEB + HEAVY_GUI:
        if bloat not in used_imports: excludes.add(bloat)
            
    if "PyQt6" in used_imports or any(imp.startswith("PyQt6.") for imp in used_imports):
        for qt_module in PYQT6_BLOAT:
            if qt_module not in used_imports: excludes.add(qt_module)
    else: excludes.add("PyQt6")
    
    return sorted(list(excludes))

def map_baked_plugins(directories):
    for directory in directories:
        if not Path(directory).exists(): continue
        plugins = [Path(f).stem for f in os.listdir(directory) if f.endswith(".py") and not f.startswith("__")]
        with open(str(Path(directory) / "__init__.py"), "w", encoding="utf-8") as f: f.write(f"# AUTO-GENERATED BY BUILD SCRIPT\n__all__ = {repr(plugins)}\n")

# =============================================================================
# BUILD ENGINES & COMPILERS
# =============================================================================
def get_tool_path(tool_name, default_module):
    if tool_name == "pyinstaller":
        return [BuildConfig.PYTHON_EXE, "-c", "import sys; sys.setrecursionlimit(5000); import PyInstaller.__main__; sys.exit(PyInstaller.__main__.run())"]
    
    if tool_name == "nuitka":
        return [BuildConfig.PYTHON_EXE, "-m", "nuitka"]
    
    if not BuildConfig.USE_VENV:
        return [BuildConfig.PYTHON_EXE, "-m", default_module]
        
    venv_dir = Path(BuildConfig.ISOLATED_BUILD_DIR) / "venv"
    scripts_dir = venv_dir / ("Scripts" if os.name == 'nt' else "bin")
    
    exe = scripts_dir / (tool_name + (".exe" if os.name == 'nt' else ""))
    if exe.exists():
        return [str(exe)]
        
    cmd = scripts_dir / (tool_name + ".cmd")
    if cmd.exists():
        return [str(cmd)]
        
    return [BuildConfig.PYTHON_EXE, "-m", default_module]

def scrub_dist_folder(dist_path, pyqt_dep=None, strip_metadata=False):
    print(f"\n{CYAN}[*] Running Nuclear Scrub: Purging source code, orphaned bindings, and dev-bloat...{RESET}")
    scrubbed_count = 0
    pruned_dirs = 0
    
    if pyqt_dep is None: 
        pyqt_dep = set()
        
    allowed_qt_cores = {mod[2:].lower() if mod.startswith("Qt") else mod.lower() for mod in pyqt_dep}
    if pyqt_dep: 
        allowed_qt_cores.update(['core', 'gui', 'widgets'])
        
    dev_bloat_dirs = {'examples', 'tests', 'test', 'test_data', 'doc', 'docs', 'translations'}
    
    qt_specific_bloat = set()
    if pyqt_dep: qt_specific_bloat.add('bindings')
    if not any(q in allowed_qt_cores for q in ['qml', 'quick']): qt_specific_bloat.add('qml')
        
    plugin_dependencies = {
        'sqldrivers': 'sql', 'multimedia': 'multimedia', 'audio': 'multimedia',
        'designer': 'designer', '3d': '3d', 'scenegraph': 'quick',
        'qmltooling': 'qml', 'qmllint': 'qml', 'qmlls': 'qml',
        'webview': 'webengine', 'position': 'positioning', 'sensors': 'sensors',
        'tls': 'network', 'networkinformation': 'network', 'assetimporters': '3d',
        'geometryloaders': '3d', 'renderers': '3d', 'sceneparsers': '3d',
        'scxmldatamodel': 'scxml', 'help': 'help'
    }

    qt_regex = re.compile(r'^(?:lib)?(?:py)?qt6?([a-z0-9_]+)(?:\.pyd|\.dll|\.so|\.dylib|\.abi3)', re.IGNORECASE)

    for root, dirs, files in os.walk(dist_path, topdown=False):
        root_lower = root.lower()
        is_qt_env = 'pyqt6' in root_lower or 'pyside6' in root_lower or 'qt6' in root_lower
        
        for d in list(dirs):
            d_lower = d.lower()
            remove_dir = False
            
            if d.endswith('.build') or d == '__pycache__' or d_lower in dev_bloat_dirs:
                remove_dir = True
            elif strip_metadata and (d.endswith('.dist-info') or d.endswith('.egg-info')):
                remove_dir = True
            elif is_qt_env:
                if d_lower in qt_specific_bloat:
                    remove_dir = True
                elif d_lower in plugin_dependencies:
                    if plugin_dependencies[d_lower] not in allowed_qt_cores:
                        remove_dir = True
                    
            if remove_dir:
                try: 
                    shutil.rmtree(str(Path(root) / d), ignore_errors=True)
                    dirs.remove(d)
                    scrubbed_count += 1
                except Exception: pass

        for file in files:
            remove_file = False
            lower_file = file.lower()
            
            if allowed_qt_cores and file.endswith(('.dll', '.so', '.dylib', '.so.6', '.pyd')):
                match = qt_regex.match(lower_file)
                if match and match.group(1) not in allowed_qt_cores:
                    remove_file = True
            
            protected_packages = ['timezonefinder', 'timezonefinderL']
            is_protected_dir = any(pkg in root.lower() for pkg in protected_packages)
            
            # MATURE FIX: Added .lib, .a, .exp, and .def to aggressively nuke static link libraries
            if file.endswith(('.py', '.pyi', '.py.bak', '.pyc', '.c', '.cpp', '.h', '.pyx', '.pyw', '.pdb', '.qml', '.lib', '.a', '.exp', '.def')) or file == "py.typed":
                if is_protected_dir and file.endswith(('.py', '.pyc')):
                    remove_file = False
                else:
                    remove_file = True

            elif lower_file.endswith('.exe') and lower_file.startswith(('qmake', 'windeployqt', 'lrelease', 'qml', 'designer')):
                remove_file = True
                
            # MATURE FIX: Added changelog, api_changes, history, and markdown extensions to the documentation stripper
            elif strip_metadata and not lower_file.endswith(('.pyd', '.so', '.dll', '.exe')) and (lower_file.startswith(('license', 'readme', 'api_changes', 'changelog', 'history', 'authors', 'contributors')) or file in ('METADATA', 'RECORD') or lower_file.endswith(('.md', '.rst'))):
                remove_file = True
            if remove_file:
                try:
                    os.chmod(str(Path(root) / file), stat.S_IWRITE)
                    (Path(root) / file).unlink()
                    scrubbed_count += 1
                except Exception: pass

    print(f"{CYAN} -> Analyzing binary string tables for transitive dependency mapping...{RESET}")
    dll_regex = re.compile(rb'[a-zA-Z0-9_\\\-\.]+\.(?:dll|so|dylib)', re.IGNORECASE)
    
    all_binaries = {}
    for root, _, files in os.walk(dist_path):
        for file in files:
            if file.lower().endswith(('.exe', '.pyd', '.dll', '.so', '.dylib')):
                all_binaries[file.lower()] = str(Path(root) / file)
                
    needed_binaries = set()
    queue = []
    
    for name, path in all_binaries.items():
        # A mature architecture protects execution roots, core runtimes, explicit plugins,
        # and universally recognized dynamic data/lib directories generated by pip/PyInstaller.
        normalized_path = path.replace('\\', '/')
        
        if (name.endswith(('.exe', '.pyd')) or 
            name.startswith('python') or 
            name in ('opus.dll', 'libopus.dll', 'libopus-0.dll') or
            (name.startswith('qt6') and name in all_binaries) or
            'platforms/' in normalized_path or 
            'imageformats/' in normalized_path or 
            'styles/' in normalized_path or
            re.search(r'_[a-zA-Z0-9_]+_data/', normalized_path) or 
            re.search(r'\.libs/', normalized_path)): 
            
            needed_binaries.add(name)
            queue.append(name)
            
    processed = set()
    while queue:
        current = queue.pop(0)
        if current in processed: continue
        processed.add(current)
        
        filepath = all_binaries.get(current)
        if not filepath: continue
        
        try:
            with open(filepath, 'rb') as f:
                matches = dll_regex.findall(f.read())
                for m in matches:
                    dep_name = m.decode('utf-8').lower()
                    if dep_name in all_binaries and dep_name not in needed_binaries:
                        needed_binaries.add(dep_name)
                        queue.append(dep_name)
        except Exception: pass
        
    orphan_count = 0
    for name, path in all_binaries.items():
        if name not in needed_binaries:
            try:
                os.chmod(path, stat.S_IWRITE)
                Path(path).unlink()
                orphan_count += 1
            except Exception: pass

    print(f"{CYAN} -> Sweeping and collapsing empty directory trees to prevent Namespace Shadowing...{RESET}")
    for root, dirs, files in os.walk(dist_path, topdown=False):
        for d in dirs:
            dir_path = Path(root) / d
            try:
                remaining_items = os.listdir(str(dir_path))
                if not remaining_items or remaining_items == ['__pycache__']:
                    shutil.rmtree(str(dir_path), ignore_errors=True)
                    pruned_dirs += 1
            except Exception: pass

    print(f"{GREEN}[+] Transitive sweep eradicated {orphan_count} dynamically orphaned binaries!{RESET}")
    print(f"{GREEN}[+] Nuclear execution complete. Incinerated {scrubbed_count} source/bloat files and collapsed {pruned_dirs} empty ghost folders.{RESET}")

def build_with_nuitka(all_hidden_imports, excludes, exhaustive_imports=None, step_info=None):
    exhaustive_imports = exhaustive_imports or set()
    command = get_tool_path("nuitka", "nuitka") + ["--standalone", "--output-dir=dist", f"--output-filename={BuildConfig.APP_NAME}", f"--jobs={BuildConfig.CPU_CORES}", "--lto=no", "--assume-yes-for-downloads", "--enable-plugin=pyqt6", "--enable-plugin=anti-bloat", "--msvc=latest"]
    command.append("--windows-console-mode=force" if BuildConfig.ATTACH_CONSOLE else "--windows-console-mode=disable")
    if BuildConfig.ICON_FILE and Path(BuildConfig.ICON_FILE).exists(): command.append(f"--windows-icon-from-ico={BuildConfig.ICON_FILE}")
    for src, dst in BuildConfig.DATA_FILES:
        if Path(src).exists(): command.append(f"--include-data-files={str(Path(src).resolve())}={Path(src).name if dst == '.' else str(Path(dst) / Path(src).name).replace(chr(92), '/')}")
    for src, dst in BuildConfig.DATA_DIRECTORIES:
        if Path(src).exists(): command.append(f"--include-data-dir={str(Path(src).resolve())}={dst}")
    for plugin_dir in BuildConfig.PLUGIN_DIRECTORIES:
        if Path(plugin_dir).exists(): command.append(f"--include-package={plugin_dir}")
    for pkg in BuildConfig.COLLECT_DATA_PACKAGES: command.append(f"--include-package-data={pkg}")
    
    directives = CompilerDirectiveEngine.generate_directives(all_hidden_imports, exhaustive_imports)
    for meta in directives["metadata"]: command.append(f"--include-distribution-metadata={meta}")
    for hi in set(list(all_hidden_imports) + list(directives["hidden_imports"])):
        if hi not in excludes: command.append(f"--include-module={hi}")
    for exc in excludes: command.append(f"--nofollow-import-to={exc}")
    command.append(BuildConfig.MAIN_SCRIPT)
    UIAesthetics.run_with_progress_bar(command, "Nuitka", step_info=step_info)

def build_with_pyinstaller(all_hidden_imports, excludes, extra_binaries=None, exhaustive_imports=None, step_info=None):
    exhaustive_imports = exhaustive_imports or set()
    command = get_tool_path("pyinstaller", "PyInstaller") + ["--noconfirm", "--name", BuildConfig.APP_NAME, "--onedir", "--distpath", "dist", "--paths", str(Path.cwd().resolve())]
    command.append("--console" if BuildConfig.ATTACH_CONSOLE else "--windowed")
    if BuildConfig.ICON_FILE and Path(BuildConfig.ICON_FILE).exists(): command.extend(["--icon", BuildConfig.ICON_FILE])
    for src, dst in BuildConfig.DATA_FILES:
        if Path(src).exists(): command.extend(["--add-data", f"{src}{os.pathsep}{dst}"])
    for src, dst in BuildConfig.DATA_DIRECTORIES:
        if Path(src).exists(): command.extend(["--add-data", f"{src}{os.pathsep}{dst}"])
        
    for pkg in BuildConfig.COLLECT_DATA_PACKAGES: command.extend(["--collect-data", pkg])
    
    for sub in set(BuildConfig.COLLECT_SUBMODULES): 
        command.extend(["--collect-data", sub, "--collect-binaries", sub])
    
    directives = CompilerDirectiveEngine.generate_directives(all_hidden_imports, exhaustive_imports)
    for meta in directives["metadata"]: command.extend(["--copy-metadata", meta])
    for hi in set(list(all_hidden_imports) + list(directives["hidden_imports"])):
        if hi not in excludes: command.extend(["--hidden-import", hi])
    for exc in excludes: command.extend(["--exclude-module", exc])
    command.append(BuildConfig.MAIN_SCRIPT)
    UIAesthetics.run_with_progress_bar(command, "PyInstaller", step_info=step_info)

    if extra_binaries:
        print(f"{CYAN} [*] Injecting Pristine Nuitka C-Extensions from Secure Vault...{RESET}")
        app_dir = Path("dist") / BuildConfig.APP_NAME
        internal_dir = app_dir / "_internal" if (app_dir / "_internal").exists() else app_dir
        
        for pristine_path, rel_dir, base_mod_name in extra_binaries:
            if Path(pristine_path).exists():
                dest_dir = internal_dir / rel_dir
                dest_dir.mkdir(parents=True, exist_ok=True)
                if rel_dir != "." and rel_dir != "":
                    init_path = dest_dir / "__init__.py"
                    if not init_path.exists():
                        with open(init_path, "w", encoding="utf-8") as f: f.write("")
                stripped_name = base_mod_name + (".pyd" if os.name == 'nt' else ".so")
                dest_path = dest_dir / stripped_name
                for old_bundled in glob.glob(str(dest_dir / f"{base_mod_name}*.pyd")) + glob.glob(str(dest_dir / f"{base_mod_name}*.so")):
                    try:
                        os.chmod(old_bundled, stat.S_IWRITE)
                        Path(old_bundled).unlink()
                    except: pass
                try:
                    shutil.copy(pristine_path, dest_path)
                    print(f"{GREEN} [+] Safely injected pristine binary: {stripped_name} into {str(dest_dir.relative_to(app_dir))}{RESET}")
                except Exception as e:
                    print(f"{RED} [!] Injection failed for {stripped_name}: {e}{RESET}")

def build_with_nuitka_hybrid(all_hidden_imports, excludes, exhaustive_imports=None):
    import sysconfig
    ext_suffix = sysconfig.get_config_var('EXT_SUFFIX') or (".pyd" if os.name == 'nt' else ".so")
    
    exhaustive_imports = exhaustive_imports or set()
    print(f"\n{CYAN}[*] Stage 1: Forging Native C-Extensions via Nuitka...{RESET}")
    target_modules = sorted(list(set([f for f in BuildConfig.C_EXTENSION_TARGETS if Path(f).exists()])))
    if not target_modules: return build_with_pyinstaller(all_hidden_imports, excludes, exhaustive_imports=exhaustive_imports)
    
    print(f"{CYAN} -> Dynamic Code Injection: Generating Dependency Proxy to protect PE Headers...{RESET}")
    proxy_imports = set()
    for py_file in target_modules:
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=py_file)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names: proxy_imports.add(alias.name.split('.')[0])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        proxy_imports.add(node.module.split('.')[0])
        except Exception: pass
        
    with open("_nuitka_proxy.py", "w", encoding="utf-8") as f:
        for imp in proxy_imports:
            f.write(f"try:\n    import {imp}\nexcept Exception:\n    pass\n")
    
    all_hidden_imports.append("_nuitka_proxy")
    
    vault_dir = Path("_nuitka_vault").resolve()
    vault_dir.mkdir(parents=True, exist_ok=True)
    pyd_binaries = []
    
    try:
        for idx, py_file in enumerate(target_modules, 1):
            abs_py_file = str(Path(py_file).resolve())
            mod_dir = str(Path(abs_py_file).parent)
            rel_dir = str(Path(mod_dir).relative_to(Path.cwd().resolve()))
            base_name = Path(abs_py_file).stem
            
            mod_path = rel_dir.replace(os.sep, ".").replace(".py", "") if rel_dir != "." else ""
            full_mod_name = f"{mod_path}.{base_name}" if mod_path else base_name
            excludes.append(full_mod_name)
            if base_name not in excludes:
                excludes.append(base_name)
            
            clean_name = str(Path(mod_dir) / (base_name + ext_suffix))
            vault_path = str(vault_dir / f"dummy_{base_name}{ext_suffix}")
            
            if Path(vault_path).exists() and Path(abs_py_file).exists() and os.path.getmtime(vault_path) >= os.path.getmtime(abs_py_file):
                print(f"{GREEN}[CACHE HIT]{RESET} Skipping {py_file} (Already compiled)")
                for leftover in glob.glob(str(Path(mod_dir) / f"{base_name}*.pyd")) + glob.glob(str(Path(mod_dir) / f"{base_name}*.so")):
                    try: os.chmod(leftover, stat.S_IWRITE); Path(leftover).unlink()
                    except: pass
                    
                pyd_binaries.append((vault_path, rel_dir, base_name))
                bak_file = abs_py_file + ".bak"
                if Path(bak_file).exists(): 
                    try: os.chmod(bak_file, stat.S_IWRITE); Path(bak_file).unlink()
                    except Exception: pass
                os.rename(abs_py_file, bak_file)
                continue
            
            for leftover in glob.glob(str(Path(mod_dir) / f"{base_name}*.pyd")) + glob.glob(str(Path(mod_dir) / f"{base_name}*.so")):
                try: os.chmod(leftover, stat.S_IWRITE); Path(leftover).unlink()
                except: pass

            nuitka_cmd = get_tool_path("nuitka", "nuitka") + [
                "--module", 
                "--lto=yes",
                "--msvc=latest", 
                f"--output-dir={mod_dir}", 
                abs_py_file
            ]
            UIAesthetics.run_with_progress_bar(nuitka_cmd, f"Nuitka ({py_file})", step_info=f"{idx}/{len(target_modules)}")
            
            compiled_files = glob.glob(str(Path(mod_dir) / f"{base_name}*.pyd")) + glob.glob(str(Path(mod_dir) / f"{base_name}*.so"))
            if compiled_files:
                compiled_file = max(compiled_files, key=os.path.getmtime)
                if Path(vault_path).exists(): Path(vault_path).unlink()
                shutil.move(compiled_file, vault_path)
                pyd_binaries.append((vault_path, rel_dir, base_name))
                bak_file = abs_py_file + ".bak"
                if Path(bak_file).exists(): 
                    try: os.chmod(bak_file, stat.S_IWRITE); Path(bak_file).unlink()
                    except Exception: pass
                os.rename(abs_py_file, bak_file)

        print(f"\n{CYAN}[*] Stage 2: Packaging with PyInstaller...{RESET}")
        build_with_pyinstaller(all_hidden_imports, excludes, extra_binaries=pyd_binaries, exhaustive_imports=exhaustive_imports)
    finally:
        for py_file in target_modules:
            bak = str(Path(py_file).resolve()) + ".bak"
            if Path(bak).exists():
                if Path(py_file).exists(): 
                    try: os.chmod(py_file, stat.S_IWRITE); Path(py_file).unlink()
                    except Exception: pass
                os.rename(bak, py_file)
        if Path("_nuitka_proxy.py").exists(): Path("_nuitka_proxy.py").unlink()
        print(f"\n{CYAN}[*] Nuitka Inner-Build finished. Outer wrapper will handle total cleanup.{RESET}")

def build_with_cython(all_hidden_imports, excludes, exhaustive_imports=None):
    import sysconfig
    ext_suffix = sysconfig.get_config_var('EXT_SUFFIX') or (".pyd" if os.name == 'nt' else ".so")
    
    exhaustive_imports = exhaustive_imports or set()
    print(f"\n{CYAN}[*] Stage 1: Forging Native C-Extensions via Cython...{RESET}")
    target_modules = sorted(list(set([f for f in BuildConfig.C_EXTENSION_TARGETS if Path(f).exists()])))

    if not target_modules: 
        return build_with_pyinstaller(all_hidden_imports, excludes, exhaustive_imports=exhaustive_imports)

    modules_to_compile = []
    pyd_binaries = []
    vault_dir = Path("_cython_vault").resolve()
    vault_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        for py_file in target_modules:
            abs_py_file = str(Path(py_file).resolve())
            mod_dir = str(Path(abs_py_file).parent)
            rel_dir = str(Path(mod_dir).relative_to(Path.cwd().resolve()))
            base_name = Path(abs_py_file).stem
            
            clean_name = str(Path(mod_dir) / (base_name + ext_suffix))
            vault_path = str(vault_dir / f"{base_name}{ext_suffix}")
            
            if Path(vault_path).exists() and Path(abs_py_file).exists() and os.path.getmtime(vault_path) >= os.path.getmtime(abs_py_file):
                pyd_binaries.append((vault_path, rel_dir, base_name))
                bak_file = abs_py_file + ".bak"
                if Path(bak_file).exists(): 
                    try: os.chmod(bak_file, stat.S_IWRITE); Path(bak_file).unlink()
                    except Exception: pass
                os.rename(abs_py_file, bak_file)
                continue
            modules_to_compile.append((py_file, clean_name))

        if modules_to_compile:
            files_for_setup = [f[0].replace(chr(92), '/') for f in modules_to_compile]
            with open("cython_setup.py", "w", encoding="utf-8") as f: 
                f.write(f"from setuptools import setup\nfrom Cython.Build import cythonize\nif __name__ == '__main__':\n    setup(ext_modules=cythonize({files_for_setup}, compiler_directives={{'language_level': '3', 'boundscheck': False, 'wraparound': False, 'annotation_typing': False}}, quiet=True))\n")
            try:
                UIAesthetics.run_with_progress_bar([BuildConfig.PYTHON_EXE, "cython_setup.py", "build_ext", "--inplace"], "Cython Compiler", step_info="1/2")
                for py_file, clean_name in modules_to_compile:
                    abs_py_file = str(Path(py_file).resolve())
                    mod_dir = str(Path(abs_py_file).parent)
                    rel_dir = str(Path(mod_dir).relative_to(Path.cwd().resolve()))
                    base_name = Path(abs_py_file).stem
                    vault_path = str(vault_dir / f"{base_name}{ext_suffix}")
                    
                    compiled_files = glob.glob(str(Path(mod_dir) / f"{base_name}*.pyd")) + glob.glob(str(Path(mod_dir) / f"{base_name}*.so"))
                    if compiled_files:
                        compiled_file = max(compiled_files, key=os.path.getmtime)
                        
                        if Path(vault_path).exists(): Path(vault_path).unlink()
                        shutil.move(compiled_file, vault_path)
                            
                        pyd_binaries.append((vault_path, rel_dir, base_name))
                        bak_file = abs_py_file + ".bak"
                        if Path(bak_file).exists(): 
                            try: os.chmod(bak_file, stat.S_IWRITE); Path(bak_file).unlink()
                            except Exception: pass
                        os.rename(abs_py_file, bak_file)
                        
                build_with_pyinstaller(all_hidden_imports, excludes, extra_binaries=pyd_binaries, exhaustive_imports=exhaustive_imports, step_info="2/2")
            finally: 
                print(f"\n{CYAN}[*] Cython Inner-Build finished. Outer wrapper will handle cleanup.{RESET}")
        else: 
            build_with_pyinstaller(all_hidden_imports, excludes, extra_binaries=pyd_binaries, exhaustive_imports=exhaustive_imports, step_info="1/1")
    finally:
        for py_file in target_modules:
            bak = str(Path(py_file).resolve()) + ".bak"
            if Path(bak).exists():
                if Path(py_file).exists(): 
                    try: os.chmod(py_file, stat.S_IWRITE); Path(py_file).unlink()
                    except Exception: pass
                os.rename(bak, py_file)

def build_extreme_nuitka_hybrid(all_hidden_imports, excludes, transitive_packages, exhaustive_imports=None):
    import sysconfig, importlib.util
    ext_suffix = sysconfig.get_config_var('EXT_SUFFIX') or (".pyd" if os.name == 'nt' else ".so")
    exhaustive_imports = exhaustive_imports or set()
    
    print(f"\n{MAGENTA}[*] Stage 1: EXTREME HYBRID MODE - Forging Site-Packages & Source into Native C++...{RESET}")
    print(f"{YELLOW} -> WARNING: Compiling entire dependency tree. This will take a monumentally long time.{RESET}")
    
    vault_dir = Path("_extreme_nuitka_vault").resolve()
    vault_dir.mkdir(parents=True, exist_ok=True)
    pyd_binaries = []
    modules_to_compile = []

    for py_file in sorted(list(set([f for f in BuildConfig.C_EXTENSION_TARGETS if Path(f).exists()]))):
        abs_py_file = str(Path(py_file).resolve())
        mod_dir = str(Path(abs_py_file).parent)
        rel_dir = str(Path(mod_dir).relative_to(Path.cwd().resolve()))
        base_name = Path(abs_py_file).stem
        modules_to_compile.append(('local', py_file, rel_dir, base_name))
        
        mod_path = rel_dir.replace(os.sep, ".").replace(".py", "") if rel_dir != "." else ""
        full_mod_name = f"{mod_path}.{base_name}" if mod_path else base_name
        excludes.append(full_mod_name)
        if base_name not in excludes: excludes.append(base_name)

    BLACKLIST = {
        'pyqt5', 'pyqt6', 'pyside2', 'pyside6', 'PyQt6-sip', 'shiboken6', 
        'numpy', 'scipy', 'pandas', 'matplotlib', 'cv2', 'torch', 'tensorflow', 'PIL', 'Pillow',
        'pytz', 'timezonefinder', 'h3',
        'requests', 'urllib3', 'certifi', 'idna', 'charset_normalizer', 'aiohttp', 
        'yarl', 'multidict', 'bs4', 'beautifulsoup4', 'chardet'
    }
    for pkg in transitive_packages:
        if pkg.lower() in BLACKLIST or pkg in sys.stdlib_module_names: continue
        try:
            spec = importlib.util.find_spec(pkg)
            if spec and spec.origin and spec.origin.endswith('.py'):
                target_path = spec.origin
                if '__init__.py' in target_path:
                    target_path = str(Path(target_path).parent)
                modules_to_compile.append(('site', target_path, "", pkg)) 
        except Exception: pass

    total = len(modules_to_compile)
    for idx, (m_type, target_path, rel_dir, base_name) in enumerate(modules_to_compile, 1):
        vault_path = str(vault_dir / f"{m_type}_dummy{base_name}{ext_suffix}")
        
        if Path(vault_path).exists():
            print(f"{GREEN}[CACHE HIT]{RESET} Skipping {base_name} (Already compiled)")
            pyd_binaries.append((vault_path, rel_dir, base_name))
            if m_type == 'site': excludes.append(base_name)
            continue
            
        nuitka_cmd = get_tool_path("nuitka", "nuitka") + [
            "--module", "--lto=yes", "--msvc=latest", f"--output-dir={str(vault_dir)}", target_path
        ]
        UIAesthetics.run_with_progress_bar(nuitka_cmd, f"Nuitka ({base_name})", step_info=f"{idx}/{total}")
        
        compiled_files = glob.glob(str(vault_dir / f"{base_name}*.pyd")) + glob.glob(str(vault_dir / f"{base_name}*.so"))
        if compiled_files:
            compiled_file = max(compiled_files, key=os.path.getmtime)
            if compiled_file != vault_path: shutil.move(compiled_file, vault_path)
            pyd_binaries.append((vault_path, rel_dir, base_name))
            if m_type == 'site': excludes.append(base_name)

    print(f"\n{CYAN}[*] Stage 2: Packaging Extreme Vault with PyInstaller into a clean Dist folder...{RESET}")
    build_with_pyinstaller(all_hidden_imports, excludes, extra_binaries=pyd_binaries, exhaustive_imports=exhaustive_imports)

def prune_packaged_bloat_assets(base_search_dir, used_imports=None):
    """Scans and explicitly strips massive unused internal Tcl/Tk subsystems, 
    lxml compile remnants, and Numpy dev-bloat to significantly optimize executable size.
    """
    print(f"\n{CYAN}[*] Running deep asset pruning inside package scopes...{RESET}")
    
    # Standard PyInstaller/Tcl Tk paths
    tcl_tk_garbage = [
        'tcl/tzdata', 'tcl/msgs', 'tk/msgs', 'tcl/http1.0',
        'tcl8/8.4/platform', 'tcl8/8.5/msgcat', 'tcl8/8.6/http2.9', 'tcl8/8.6/tdbc1.1.3',
        'tk/demos', 'tk/images', '_tcl_data/msgs', '_tcl_data/opt0.4', '_tk_data/msgs',
        '_tk_data/images', 'tcl8/8.6/http2.9', 'tcl8/8.6/tdbc1.1.3', 'tzdata'
    ]
    
    # Numpy specific development bloat directories
    numpy_bloat_dirs = [
        'f2py', 'tests', 'testing', 'doc', 'docs', '_examples', 'example', 'includes'
    ]
    
    is_tkinter_used = used_imports is not None and (
        'tkinter' in used_imports or any('tkinter' in imp for imp in used_imports)
    )
    is_numpy_used = used_imports is not None and (
        'numpy' in used_imports or any('numpy' in imp for imp in used_imports)
    )
    
    removed_count = 0
    
    for root, dirs, files in os.walk(base_search_dir, topdown=False):
        normalized_root = root.replace('\\', '/')
        is_in_numpy = '/numpy' in normalized_root.lower() or normalized_root.lower().endswith('/numpy')

        # STRATEGY A: Unused Core Packages -> Eliminate entire environment folders cleanly
        if not is_tkinter_used:
            for d in list(dirs):
                if d.lower() in ['tcl', 'tk', '_tcl_data', '_tk_data', 'tcl8']:
                    dir_path = os.path.join(root, d)
                    try:
                        shutil.rmtree(dir_path, ignore_errors=True)
                        dirs.remove(d)
                        removed_count += 1
                        print(f"    {RED}[×] Stripped unused module environment tree: {d}{RESET}")
                    except Exception: pass

        if not is_numpy_used:
            for d in list(dirs):
                if d.lower() == 'numpy':
                    dir_path = os.path.join(root, d)
                    try:
                        shutil.rmtree(dir_path, ignore_errors=True)
                        dirs.remove(d)
                        removed_count += 1
                        print(f"    {RED}[×] Stripped unused module environment tree: {d}{RESET}")
                    except Exception: pass

        # STRATEGY B: Surgical pruning on target asset pathways
        for d in list(dirs):
            dir_path = os.path.join(root, d)
            normalized_path = dir_path.replace('\\', '/')
            
            # 1. Tcl/Tk bloat
            is_garbage_dir = any(garbage in normalized_path for garbage in tcl_tk_garbage) or d.lower() in ['tzdata', 'http1.0']
            # 2. lxml bloat
            is_lxml_bloat_dir = 'lxml' in normalized_path.lower() and d.lower() in ['includes', 'isoschematron']
            # 3. Numpy bloat (f2py, _examples, tests)
            is_numpy_bloat_dir = is_in_numpy and d.lower() in numpy_bloat_dirs

            if is_garbage_dir or is_lxml_bloat_dir or is_numpy_bloat_dir:
                try:
                    for r, _, f_list in os.walk(dir_path):
                        for f_item in f_list:
                            os.chmod(os.path.join(r, f_item), stat.S_IWRITE)
                    shutil.rmtree(dir_path, ignore_errors=True)
                    dirs.remove(d)
                    removed_count += 1
                    print(f"    {RED}[×] Stripped internal asset bloat folder: {d}{RESET}")
                except Exception: pass

        # STRATEGY C: File-level deep purge (Removes targeted files inside kept environments)
        for f in files:
            file_path = os.path.join(root, f)
            normalized_file_path = file_path.replace('\\', '/')
            
            # 1. Tcl/Tk bloat files
            is_garbage_file = any(garbage in normalized_file_path for garbage in tcl_tk_garbage)
            # 2. lxml runtime-useless Cython source files
            is_lxml_bloat_file = 'lxml' in normalized_file_path.lower() and f.lower().endswith('.pxi')
            # 3. Numpy runtime-useless dev files (Cython headers, C/C++ headers, static libs)
            is_numpy_bloat_file = is_in_numpy and f.lower().endswith(('.pxd', '.pyx', '.c', '.cpp', '.h', '.a'))
            
            if is_garbage_file or is_lxml_bloat_file or is_numpy_bloat_file:
                try:
                    os.chmod(file_path, stat.S_IWRITE)
                    os.remove(file_path)
                    removed_count += 1
                except Exception: pass
                    
    if removed_count > 0:
        print(f"{GREEN}[+] Successfully purged {removed_count} unneeded system component trees/files!{RESET}")

# =============================================================================
# MAIN EXECUTION PIPELINE
# =============================================================================
if __name__ == "__main__":
    print(f"\n{CYAN}======================================================{RESET}")
    print(f"{CYAN}       🚀 UNIVERSAL PYTHON BUILD SYSTEM v3.0 🚀       {RESET}")
    print(f"{CYAN}======================================================{RESET}")
    # Ensure the entire path tree is safely generated
    os.makedirs(BuildConfig.ISOLATED_BUILD_DIR, exist_ok=True)
    os.makedirs(BuildConfig.DIST_DIR, exist_ok=True)
    prefs, use_saved, folder_prefs, selected_files, files_to_fix, auto_fix_ans = {}, False, {}, [], [], 'y'
    reselect_assets = False
    reconfigure_mode = False

    parser = argparse.ArgumentParser(description="Universal Robust Build Script")
    parser.add_argument("engine", nargs="?", choices=["nuitka", "pyinstaller", "cython", "nuitka_hybrid", "extreme_nuitka_hybrid"])
    args = parser.parse_args()

    if Path(BuildConfig.PREFS_FILE).exists():
        try:
            with open(BuildConfig.PREFS_FILE, "r", encoding="utf-8") as f: prefs = json.load(f)
            print(f"{CYAN}[*] Previous build preferences found in '{BuildConfig.PREFS_FILE}'.{RESET}")
            
            if args.engine: prefs['engine'] = args.engine

            if input(f"{YELLOW} -> Do you want to modify specific preferences before building? (y/N): {RESET}").strip().lower() == 'y':
                reconfigure_mode = True
            else:
                use_saved = True
                print(f"{GREEN}[+] Using saved preferences. Headless build initiated.{RESET}")
        except Exception as e: print(f"{RED}[!] Failed to load preferences: {e}{RESET}")

    while True:
        BuildConfig.C_EXTENSION_TARGETS.clear(); BuildConfig.PLUGIN_DIRECTORIES.clear(); BuildConfig.DATA_FILES.clear(); BuildConfig.DATA_DIRECTORIES.clear()

        if reconfigure_mode:
            use_saved = True
            while True:
                print(f"\n{CYAN}[*] Configuration Menu:{RESET}")
                print(f" {YELLOW}[1]{RESET} Application Name       (Current: {prefs.get('APP_NAME', BuildConfig.APP_NAME)})")
                print(f" {YELLOW}[2]{RESET} Main Script            (Current: {prefs.get('MAIN_SCRIPT', BuildConfig.MAIN_SCRIPT)})")
                print(f" {YELLOW}[3]{RESET} Attach Console         (Current: {prefs.get('ATTACH_CONSOLE', BuildConfig.ATTACH_CONSOLE)})")
                print(f" {YELLOW}[4]{RESET} Isolated Build Dir     (Current: {prefs.get('ISOLATED_BUILD_DIR', BuildConfig.ISOLATED_BUILD_DIR)})")
                print(f" {YELLOW}[5]{RESET} Dist Output Dir        (Current: {prefs.get('DIST_DIR', BuildConfig.DIST_DIR)})")
                print(f" {YELLOW}[6]{RESET} Build Engine           (Current: {prefs.get('engine', engine if 'engine' in locals() else None)})")
                print(f" {YELLOW}[7]{RESET} Reselect Folders/Files (Triggers prompt)")
                print(f" {YELLOW}[8]{RESET} Full Reconfiguration   (Discards saved prefs)")
                print(f" {YELLOW}[9]{RESET} Use Virtual Env        (Current: {prefs.get('USE_VENV', BuildConfig.USE_VENV)})")
                print(f" {YELLOW}[A]{RESET} Preserve Updater       (Current: {prefs.get('PRESERVE_UPDATER', BuildConfig.PRESERVE_UPDATER)})")
                print(f" {YELLOW}[B]{RESET} Strip Metadata         (Current: {prefs.get('STRIP_METADATA', BuildConfig.STRIP_METADATA)})")
                print(f" {YELLOW}[E]{RESET} Standalone Updater     (Current: {prefs.get('UPDATER_SCRIPT', BuildConfig.UPDATER_SCRIPT) or 'None'})")
                print(f" {YELLOW}[I]{RESET} App Icon               (Current: {prefs.get('ICON_FILE', 'Auto-detect')})")
                print(f" {YELLOW}[U]{RESET} Unlock Workspace       (Delete Lock File)")
                print(f" {GREEN}[C]{RESET} Continue with Build")
                
                choice = input(f"\n{CYAN}Select option to change: {RESET}").strip().lower()
                if choice == '1': prefs['APP_NAME'] = input(f"{YELLOW} -> New App Name: {RESET}").strip() or prefs.get('APP_NAME')
                elif choice == '2': prefs['MAIN_SCRIPT'] = input(f"{YELLOW} -> New Main Script: {RESET}").strip() or prefs.get('MAIN_SCRIPT')
                elif choice == '3':
                    prefs['ATTACH_CONSOLE'] = not prefs.get('ATTACH_CONSOLE', True)
                    print(f"{GREEN} [+] Toggled Attach Console to: {prefs['ATTACH_CONSOLE']}{RESET}")
                elif choice == '4':
                    iso_in = input(f"{YELLOW} -> Enter manually (or leave blank for GUI selector) [{prefs.get('ISOLATED_BUILD_DIR')}]: {RESET}").strip()
                    if iso_in: prefs['ISOLATED_BUILD_DIR'] = iso_in
                    else:
                        gui_folder = UIAesthetics.trigger_gui_folder_selector("Select Custom Isolated Build Directory", str(Path.cwd()))
                        if gui_folder: 
                            try:
                                prefs['ISOLATED_BUILD_DIR'] = str(Path(gui_folder).relative_to(Path.cwd()))
                            except ValueError:
                                prefs['ISOLATED_BUILD_DIR'] = gui_folder
                elif choice == '5':
                    d_in = input(f"{YELLOW} -> Enter manually (or leave blank for GUI selector) [{prefs.get('DIST_DIR')}]: {RESET}").strip()
                    if d_in: prefs['DIST_DIR'] = d_in
                    else:
                        gui_folder = UIAesthetics.trigger_gui_folder_selector("Select Custom Final Output Directory", str(Path.cwd()))
                        if gui_folder: 
                            try:
                                prefs['DIST_DIR'] = str(Path(gui_folder).relative_to(Path.cwd()))
                            except ValueError:
                                prefs['DIST_DIR'] = gui_folder
                elif choice == '6':
                    eng_choice = input(f"\n{CYAN}Select pathway (1: Nuitka, 2: PyInstaller, 3: Cython, 4: Nuitka Hybrid, 5: Extreme Nuitka): {RESET}").strip().lower()
                    eng = {
                        "1": "nuitka", "nuitka": "nuitka", 
                        "2": "pyinstaller", "pyinstaller": "pyinstaller", 
                        "3": "cython", "cython": "cython", 
                        "4": "nuitka_hybrid", "nuitka_hybrid": "nuitka_hybrid", "nuitka hybrid": "nuitka_hybrid",
                        "5": "extreme_nuitka_hybrid", "extreme": "extreme_nuitka_hybrid", "extreme nuitka": "extreme_nuitka_hybrid", "extreme nuitka hybrid": "extreme_nuitka_hybrid"
                    }.get(eng_choice)
                    if eng: prefs['engine'] = eng
                    else: print(f"{RED}[!] Invalid choice.{RESET}")
                elif choice == '7':
                    reselect_assets = True
                    print(f"{GREEN} [+] Will prompt for asset selection.{RESET}")
                elif choice == '8': use_saved = False; break
                elif choice == '9':
                    prefs['USE_VENV'] = not prefs.get('USE_VENV', BuildConfig.USE_VENV)
                    print(f"{GREEN} [+] Toggled Virtual Env to: {prefs['USE_VENV']}{RESET}")
                elif choice == 'a':
                    prefs['PRESERVE_UPDATER'] = not prefs.get('PRESERVE_UPDATER', BuildConfig.PRESERVE_UPDATER)
                    print(f"{GREEN} [+] Toggled Preserve Updater to: {prefs['PRESERVE_UPDATER']}{RESET}")
                elif choice == 'b':
                    prefs['STRIP_METADATA'] = not prefs.get('STRIP_METADATA', BuildConfig.STRIP_METADATA)
                    print(f"{GREEN} [+] Toggled Strip Metadata to: {prefs['STRIP_METADATA']}{RESET}")
                elif choice == 'e':
                    e_in = input(f"{YELLOW} -> Enter Updater Script (leave blank to disable): {RESET}").strip()
                    prefs['UPDATER_SCRIPT'] = e_in
                    print(f"{GREEN} [+] Updater Script set to: {prefs['UPDATER_SCRIPT'] or 'Disabled'}{RESET}")
                elif choice == 'i':
                    i_in = input(f"{YELLOW} -> Enter icon path manually (or leave blank for GUI selector): {RESET}").strip()
                    if i_in: 
                        prefs['ICON_FILE'] = i_in
                    else:
                        gui_file = UIAesthetics.trigger_gui_file_selector("Select Application Icon (.ico)", str(Path.cwd()), [("Icon Files", "*.ico"), ("All Files", "*.*")])
                        if gui_file: 
                            try:
                                prefs['ICON_FILE'] = str(Path(gui_file).relative_to(Path.cwd()))
                            except ValueError:
                                prefs['ICON_FILE'] = gui_file
                    print(f"{GREEN} [+] App Icon set to: {prefs.get('ICON_FILE')}{RESET}")
                elif choice == 'u':
                    try:
                        if Path(".workspace.lock").exists(): Path(".workspace.lock").unlink(); print(f"{GREEN} [+] Workspace unlocked successfully!{RESET}")
                        else: print(f"{YELLOW} [*] Workspace is already unlocked.{RESET}")
                    except Exception as e: print(f"{RED} [!] Failed to delete lock file. Error: {e}{RESET}")
                elif choice == 'c' or choice == '': 
                    break
                
                try:
                    with open(BuildConfig.PREFS_FILE, "w", encoding="utf-8") as f:
                        json.dump(prefs, f, indent=4)
                    print(f"{GREEN}[+] Preferences saved immediately!{RESET}")
                except Exception: pass
                
            reconfigure_mode = False

        if use_saved:
            BuildConfig.APP_NAME = prefs.get("APP_NAME", "UnknownApp")
            BuildConfig.MAIN_SCRIPT = prefs.get("MAIN_SCRIPT", "main.py")
            BuildConfig.ATTACH_CONSOLE = prefs.get("ATTACH_CONSOLE", True)
            BuildConfig.ISOLATED_BUILD_DIR = prefs.get("ISOLATED_BUILD_DIR", "_isolated_build_env")
            BuildConfig.DIST_DIR = prefs.get("DIST_DIR", "dist")
            BuildConfig.USE_VENV = prefs.get("USE_VENV", BuildConfig.USE_VENV)
            BuildConfig.PRESERVE_UPDATER = prefs.get("PRESERVE_UPDATER", BuildConfig.PRESERVE_UPDATER)
            BuildConfig.STRIP_METADATA = prefs.get("STRIP_METADATA", BuildConfig.STRIP_METADATA)
            BuildConfig.UPDATER_SCRIPT = prefs.get("UPDATER_SCRIPT", "")
            BuildConfig.ICON_FILE = prefs.get("ICON_FILE")
            if BuildConfig.MAIN_SCRIPT not in BuildConfig.EXCLUDE_FROM_BUILD: BuildConfig.EXCLUDE_FROM_BUILD.append(BuildConfig.MAIN_SCRIPT)
            if BuildConfig.UPDATER_SCRIPT and BuildConfig.UPDATER_SCRIPT not in BuildConfig.EXCLUDE_FROM_BUILD: BuildConfig.EXCLUDE_FROM_BUILD.append(BuildConfig.UPDATER_SCRIPT)
        else: 
            detect_project_settings()
            prefs['APP_NAME'] = BuildConfig.APP_NAME
            prefs['MAIN_SCRIPT'] = BuildConfig.MAIN_SCRIPT
            prefs['ATTACH_CONSOLE'] = BuildConfig.ATTACH_CONSOLE
            prefs['ISOLATED_BUILD_DIR'] = BuildConfig.ISOLATED_BUILD_DIR
            prefs['DIST_DIR'] = BuildConfig.DIST_DIR
            prefs['USE_VENV'] = BuildConfig.USE_VENV
            prefs['PRESERVE_UPDATER'] = BuildConfig.PRESERVE_UPDATER
            prefs['STRIP_METADATA'] = BuildConfig.STRIP_METADATA
            prefs['UPDATER_SCRIPT'] = BuildConfig.UPDATER_SCRIPT
            if BuildConfig.ICON_FILE: prefs['ICON_FILE'] = BuildConfig.ICON_FILE
            
            try:
                with open(BuildConfig.PREFS_FILE, "w", encoding="utf-8") as f: json.dump(prefs, f, indent=4)
            except Exception: pass

        if not BuildConfig.ICON_FILE or not Path(BuildConfig.ICON_FILE).exists():
            BuildConfig.ICON_FILE = find_app_icon()
            if BuildConfig.ICON_FILE and not use_saved: print(f"{CYAN}[*] Auto-detected application icon at: {BuildConfig.ICON_FILE}{RESET}")
        else:
            print(f"{CYAN}[*] Using configured application icon at: {BuildConfig.ICON_FILE}{RESET}")
        if not use_saved: print(f"{CYAN}[*] Auto-discovering source files for C-Extension compilation...{RESET}")
        
        BuildConfig.C_EXTENSION_TARGETS = discover_compilation_targets()
        auto_discover_assets()

        all_discovered_dirs = list(BuildConfig.PLUGIN_DIRECTORIES)
        for d_src, _dummy in BuildConfig.DATA_DIRECTORIES:
            if d_src not in all_discovered_dirs: all_discovered_dirs.append(d_src)
        for src, dst in BuildConfig.DATA_FILES:
            parts = os.path.normpath(src).split(os.sep)
            if len(parts) > 1 and parts[0] not in all_discovered_dirs and parts[0] != ".": all_discovered_dirs.append(parts[0])
                
        if all_discovered_dirs:
            folder_prefs = prefs.get("folder_prefs", {}) if (use_saved and not reselect_assets) else UIAesthetics.interactive_folder_menu("Select Folders to Bundle:", all_discovered_dirs)
            bundled_dirs = [d for d in all_discovered_dirs if folder_prefs.get(d, [False, False])[0]]
            BuildConfig.PLUGIN_DIRECTORIES = [d for d in BuildConfig.PLUGIN_DIRECTORIES if d in bundled_dirs]
            BuildConfig.DATA_DIRECTORIES = [(s, d) for s, d in BuildConfig.DATA_DIRECTORIES if s in bundled_dirs]
            filtered_data_files, auto_included_files = [], []
            for src, dst in BuildConfig.DATA_FILES:
                parts = os.path.normpath(src).split(os.sep)
                if len(parts) == 1 or parts[0] == ".": filtered_data_files.append((src, dst))
                elif parts[0] in bundled_dirs:
                    if folder_prefs[parts[0]][1]: auto_included_files.append((src, dst))
                    else: filtered_data_files.append((src, dst))
            BuildConfig.DATA_FILES = filtered_data_files

        if not use_saved: print(f"\n{CYAN}[*] Acquiring Workspace Lock...{RESET}")
        workspace_lock = acquire_workspace_lock()

        engine = args.engine if args.engine else prefs.get("engine") if use_saved else None
        
        while not engine or engine not in ["nuitka", "pyinstaller", "cython", "nuitka_hybrid", "extreme_nuitka_hybrid"]:
            print(f"\n{CYAN}[?] No build engine specified.\n {YELLOW}[1]{RESET} Nuitka\n {YELLOW}[2]{RESET} PyInstaller\n {YELLOW}[3]{RESET} Cython Hybrid\n {GREEN}[4]{RESET} Nuitka Hybrid\n {MAGENTA}[5]{RESET} Extreme Nuitka Hybrid (Compiles Dependencies)")
            choice = input(f"\n{CYAN}Select pathway (1/2/3/4/5 or name): {RESET}").strip().lower()
            engine = {"1": "nuitka", "nuitka": "nuitka", "2": "pyinstaller", "pyinstaller": "pyinstaller", "3": "cython", "cython": "cython", "4": "nuitka_hybrid", "nuitka_hybrid": "nuitka_hybrid", "5": "extreme_nuitka_hybrid", "extreme": "extreme_nuitka_hybrid", "extreme_nuitka_hybrid": "extreme_nuitka_hybrid"}.get(choice)
            if not engine: print(f"{RED}[!] Invalid choice.{RESET}")

        # Update runtime preference mapping immediately following validation
        prefs['engine'] = engine

        found_qss_files = []
        for root, _dummy, files in os.walk("."):
            if any(exclude in root for exclude in [".git", "__pycache__", "venv", "env", "dist", BuildConfig.DIST_DIR, "build", BuildConfig.ISOLATED_BUILD_DIR]): continue
            for file in files:
                if file.endswith(".qss"):
                    src_path = str(Path(root) / file)
                    dst_path = str(Path(root).relative_to(".")) if root != "." else "."
                    if not any(src_path == existing[0] for existing in BuildConfig.DATA_FILES): BuildConfig.DATA_FILES.append((src_path, dst_path)); found_qss_files.append(src_path)

        all_discovered_files = [src for src, dst in BuildConfig.DATA_FILES]
        if all_discovered_files:
            selected_files = prefs.get("selected_files", []) if (use_saved and not reselect_assets) else UIAesthetics.interactive_file_menu("Select Individual Files to Bundle:", sorted(all_discovered_files, key=lambda x: (os.path.splitext(x)[1].lower(), os.path.basename(x).lower())))
            BuildConfig.DATA_FILES = [(s, d) for s, d in BuildConfig.DATA_FILES if s in selected_files]
            found_qss_files = [q for q in found_qss_files if q in selected_files]

        for src, dst in locals().get('auto_included_files', []):
            if not any(src == existing[0] for existing in BuildConfig.DATA_FILES): BuildConfig.DATA_FILES.append((src, dst))
            if src.endswith(".qss") and src not in found_qss_files: found_qss_files.append(src)

        print(f"\n{CYAN}======================================================{RESET}")
        print(f"{GREEN}        📋 BUILD CONFIGURATION SUMMARY 📋         {RESET}")
        print(f"{CYAN}======================================================{RESET}")
        print(f"{YELLOW} - Application Name: {RESET}  {BuildConfig.APP_NAME}")
        print(f"{YELLOW} - Main Script:      {RESET}  {BuildConfig.MAIN_SCRIPT}")
        print(f"{YELLOW} - Attach Console:   {RESET}  {'YES' if BuildConfig.ATTACH_CONSOLE else 'NO'}")
        print(f"{YELLOW} - Build Engine:     {RESET}  {engine}")
        print(f"{YELLOW} - Virtual Env:      {RESET}  {'YES' if prefs.get('USE_VENV', BuildConfig.USE_VENV) else 'NO'}")
        print(f"{YELLOW} - Isolated Env:     {RESET}  {BuildConfig.ISOLATED_BUILD_DIR}")
        print(f"{YELLOW} - Target Output:    {RESET}  {BuildConfig.DIST_DIR}")
        print(f"{YELLOW} - Bundled Dirs:     {RESET}  {len(bundled_dirs) if 'bundled_dirs' in locals() else 0}")
        print(f"{YELLOW} - Bundled Files:    {RESET}  {len(BuildConfig.DATA_FILES)}")
        print(f"{YELLOW} - Preserve Updater: {RESET}  {'YES' if prefs.get('PRESERVE_UPDATER', BuildConfig.PRESERVE_UPDATER) else 'NO'}")
        print(f"{YELLOW} - Strip Metadata:   {RESET}  {'YES' if prefs.get('STRIP_METADATA', BuildConfig.STRIP_METADATA) else 'NO'}")
        print(f"{YELLOW} - Updater Script:   {RESET}  {BuildConfig.UPDATER_SCRIPT or 'None'}")
        print(f"{CYAN}======================================================{RESET}")        
        ans = input(f"{GREEN}Press Enter to commence build sequence... or R to reconfigure! {RESET}").strip().lower()

        if ans == 'r':
            reconfigure_mode = True
            continue
        try:
            prefs.update({
                "APP_NAME": BuildConfig.APP_NAME,
                "MAIN_SCRIPT": BuildConfig.MAIN_SCRIPT,
                "ATTACH_CONSOLE": BuildConfig.ATTACH_CONSOLE,
                "ISOLATED_BUILD_DIR": BuildConfig.ISOLATED_BUILD_DIR,
                "DIST_DIR": BuildConfig.DIST_DIR,
                "engine": engine,
                "folder_prefs": folder_prefs,
                "selected_files": selected_files,
                "ICON_FILE": BuildConfig.ICON_FILE,
                "PRESERVE_UPDATER": prefs.get('PRESERVE_UPDATER', BuildConfig.PRESERVE_UPDATER),
                "STRIP_METADATA": prefs.get('STRIP_METADATA', BuildConfig.STRIP_METADATA),
                "UPDATER_SCRIPT": BuildConfig.UPDATER_SCRIPT
            })
            with open(BuildConfig.PREFS_FILE, "w", encoding="utf-8") as f:
                json.dump(prefs, f, indent=4)
        except Exception:
            pass
        break

    setup_virtual_environment(prefs)
    ensure_build_engine(engine)

    APP_FILES, APP_DIRS = [BuildConfig.MAIN_SCRIPT], list(BuildConfig.PLUGIN_DIRECTORIES)
    used_imports, scanned_files, local_modules, cycle_files = scan_project_imports(APP_FILES, APP_DIRS)
    CompilerDirectiveEngine.local_modules = local_modules

    transitive_pkgs, exhaustive_imports = DepEngine.resolve_and_sync(used_imports, local_modules, BuildConfig.PYTHON_EXE)
    populate_dynamic_collections(used_imports, transitive_pkgs)

    # Auto-fetch and bundle 64-bit Windows opus.dll if opuslib is detected
    if "opuslib" in used_imports or "opuslib" in transitive_pkgs or "opus" in used_imports:
        opus_dll_target = ensure_windows_opus_dll(".")
        if opus_dll_target and opus_dll_target.exists():
            abs_dll = str(opus_dll_target.resolve())
            if not any(abs_dll == existing[0] for existing in BuildConfig.DATA_FILES):
                BuildConfig.DATA_FILES.append((abs_dll, "."))
                BuildConfig.DATA_FILES.append((abs_dll, "_internal"))

    pyqt_dep = {imp.split('.')[1] for imp in used_imports if imp.startswith('PyQt6.')}
    if pyqt_dep or "PyQt6" in used_imports:
        pyqt_dep.update(["QtCore", "QtGui", "QtWidgets", "sip","PyQt6.sip"])
    if any(pkg in used_imports or pkg in transitive_pkgs for pkg in ["pyqtgraph", "matplotlib"]):
        pyqt_dep.update(["QtOpenGL", "QtOpenGLWidgets", "QtSvg", "QtPrintSupport", "QtNetwork"])
    
    for pkg in transitive_pkgs:
        if pkg not in BuildConfig.COLLECT_SUBMODULES: BuildConfig.COLLECT_SUBMODULES.append(pkg)

    if not use_saved: print(f"\n{CYAN}[*] Syncing Persistent Isolated Build Environment to retain caches...{RESET}")
    if not Path(BuildConfig.ISOLATED_BUILD_DIR).exists(): Path(BuildConfig.ISOLATED_BUILD_DIR).mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(BuildConfig.ISOLATED_BUILD_DIR):
        dirs[:] = [d for d in dirs if not d.endswith('.build') and d not in ['dist', 'build', '__pycache__', 'venv', 'env', '.venv']]
        for file in files:
            if file.endswith('.py') and not (Path(root).relative_to(BuildConfig.ISOLATED_BUILD_DIR) / file).exists():
                try: (Path(root) / file).unlink()
                except: pass

    updater_exe = ""
    updater_base = ""
    if BuildConfig.UPDATER_SCRIPT and Path(BuildConfig.UPDATER_SCRIPT).exists():
        updater_base = Path(BuildConfig.UPDATER_SCRIPT).stem
        updater_exe = f"{updater_base}.exe" if os.name == 'nt' else updater_base
        original_updater_path = Path(BuildConfig.DIST_DIR) / BuildConfig.APP_NAME / updater_exe
        updater_vault_path = Path(BuildConfig.ISOLATED_BUILD_DIR) / "_updater_vault" / updater_exe
        
        if prefs.get('PRESERVE_UPDATER', BuildConfig.PRESERVE_UPDATER) and original_updater_path.exists():
            print(f"\n{CYAN}[*] Backing up existing Standalone Updater to maintain hash...{RESET}")
            (Path(BuildConfig.ISOLATED_BUILD_DIR) / "_updater_vault").mkdir(parents=True, exist_ok=True)
            shutil.copy2(original_updater_path, updater_vault_path)

        items_to_ignore = [item for item in BuildConfig.EXCLUDE_FROM_BUILD if item not in [BuildConfig.MAIN_SCRIPT, BuildConfig.UPDATER_SCRIPT]]
    else:
        items_to_ignore = [item for item in BuildConfig.EXCLUDE_FROM_BUILD if item != BuildConfig.MAIN_SCRIPT]

    shutil.copytree(".", BuildConfig.ISOLATED_BUILD_DIR, ignore=shutil.ignore_patterns(".git", ".svn", "__pycache__", "venv", ".venv", "env", "dist", BuildConfig.DIST_DIR, "build", BuildConfig.ISOLATED_BUILD_DIR, "*.pyc", "*.bak", ".workspace.lock", *items_to_ignore), dirs_exist_ok=True)
    
    original_cwd = os.getcwd()
    os.chdir(BuildConfig.ISOLATED_BUILD_DIR)

    # Automatically scan & patch missing imports on the isolated files only
    resolve_isolated_missing_imports()

    try:
        if Path(BuildConfig.MAIN_SCRIPT).exists():
            with open(BuildConfig.MAIN_SCRIPT, "r", encoding="utf-8") as f: lines = f.read().split('\n')
            insert_idx = next((i + 1 for i, line in enumerate(lines) if line.strip().startswith('from __future__ import')), 0)
            
            metadata_patch = (
                "import sys\n"
                "try:\n"
                "    import importlib.metadata\n"
                "    _orig_version = importlib.metadata.version\n"
                "    def _mock_version(pkg, *args, **kwargs):\n"
                "        try: return _orig_version(pkg, *args, **kwargs)\n"
                "        except Exception: return '1.0.0'\n"
                "    importlib.metadata.version = _mock_version\n"
                "    _orig_dist = importlib.metadata.distribution\n"
                "    class _MockDist:\n"
                "        version = '1.0.0'\n"
                "        metadata = {'Name': 'mocked', 'version': '1.0.0'}\n"
                "    def _mock_dist(pkg, *args, **kwargs):\n"
                "        try: return _orig_dist(pkg, *args, **kwargs)\n"
                "        except Exception: return _MockDist()\n"
                "    importlib.metadata.distribution = _mock_dist\n"
                "except Exception: pass\n"
                "try:\n"
                "    import pkg_resources\n"
                "    _orig_get_dist = pkg_resources.get_distribution\n"
                "    class _MockPkgRes:\n"
                "        version = '1.0.0'\n"
                "    def _mock_get_dist(pkg, *args, **kwargs):\n"
                "        try: return _orig_get_dist(pkg, *args, **kwargs)\n"
                "        except Exception: return _MockPkgRes()\n"
                "    pkg_resources.get_distribution = _mock_get_dist\n"
                "except Exception: pass\n"
            )
            
            log_patch = ""
            if BuildConfig.ATTACH_CONSOLE:
                log_patch = 'class _BLog(object):\n    def __init__(self,f,s):\n        self.t=s;self.l=open(f,"a",encoding="utf-8") if f else None\n    def write(self,m):\n        try: self.t.write(m); self.t.flush()\n        except: pass\n        if self.l:\n            try: self.l.write(m); self.l.flush()\n            except: pass\n    def flush(self):\n        try: self.t.flush()\n        except: pass\ntry: sys.stdout=_BLog("logs.txt",sys.stdout); sys.stderr=_BLog("logs.txt",sys.stderr)\nexcept: pass\n'
            
            with open(BuildConfig.MAIN_SCRIPT, "w", encoding="utf-8") as f:
                f.write('\n'.join(lines[:insert_idx]) + '\n' + metadata_patch + log_patch + '\n'.join(lines[insert_idx:]))
                
        map_baked_plugins(BuildConfig.PLUGIN_DIRECTORIES)
        _dummy, _dummy, _dummy, cycle_files_iso = scan_project_imports(APP_FILES, APP_DIRS)
        
        if cycle_files_iso:
            auto_fix_ans = ('y' if prefs.get("auto_fix_cycles") else 'n') if use_saved else input(f"\n{YELLOW} -> Attempt to auto-fix circular imports inline? (Y/n): {RESET}").strip().lower()
            if auto_fix_ans != 'n':
                files_to_fix = [f for f in cycle_files_iso if f in prefs.get("fixed_cycle_files", [])] if use_saved else UIAesthetics.interactive_file_menu("Select files to Auto-Fix:", cycle_files_iso)
                if files_to_fix:
                    try:
                        sys.path.insert(0, str(Path.cwd().parent))
                        import auto_fix_cycles; auto_fix_cycles.fix_circular_imports(files_to_fix, BuildConfig.MAIN_SCRIPT)
                        sys.path.pop(0)
                        _dummy, _dummy, _dummy, cycle_files_iso = scan_project_imports(APP_FILES, APP_DIRS)
                    except ImportError: pass
            if cycle_files_iso and (input(f"\n{YELLOW}[?] Unresolved Circular imports present. Continue anyways? (y/N): {RESET}").strip().lower() != 'y' if not use_saved else 'y' != 'y'): sys.exit(1)

        all_hidden_imports = exhaustive_imports.copy()
        dynamic_excludes = generate_smart_excludes(exhaustive_imports)

        if engine == "nuitka": build_with_nuitka(list(all_hidden_imports), dynamic_excludes, exhaustive_imports=exhaustive_imports)
        elif engine == "pyinstaller": build_with_pyinstaller(list(all_hidden_imports), dynamic_excludes, exhaustive_imports=exhaustive_imports)
        elif engine == "cython": build_with_cython(list(all_hidden_imports), dynamic_excludes, exhaustive_imports=exhaustive_imports)
        elif engine == "nuitka_hybrid": build_with_nuitka_hybrid(list(all_hidden_imports), dynamic_excludes, exhaustive_imports=exhaustive_imports)
        elif engine == "extreme_nuitka_hybrid": build_extreme_nuitka_hybrid(list(all_hidden_imports), dynamic_excludes, transitive_packages=transitive_pkgs, exhaustive_imports=exhaustive_imports)

        if BuildConfig.UPDATER_SCRIPT and Path(BuildConfig.UPDATER_SCRIPT).exists():
            vault_path = Path("_updater_vault") / updater_exe
            target_dir = Path("dist") / BuildConfig.APP_NAME
            target_path = target_dir / updater_exe

            if prefs.get('PRESERVE_UPDATER', BuildConfig.PRESERVE_UPDATER) and vault_path.exists():
                print(f"\n{CYAN}[*] Restoring preserved Standalone Updater to maintain hash...{RESET}")
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(vault_path, target_path)
            else:
                print(f"\n{CYAN}[*] Bootstrapping Dedicated Updater VENV (venv_updater)...{RESET}")
                updater_venv_dir = Path("venv_updater")
                
                # 0. Nuke the existing corrupted updater venv if it exists
                if updater_venv_dir.exists():
                    import shutil
                    shutil.rmtree(str(updater_venv_dir), ignore_errors=True)
                
                updater_python = str(updater_venv_dir / "Scripts" / "python.exe") if os.name == 'nt' else str(updater_venv_dir / "bin" / "python")
                
                # 1. Create the pristine isolated environment
                UIAesthetics.run_with_progress_bar([sys.executable, "-m", "venv", str(updater_venv_dir)], "Updater VENV", step_info="Init")
                
                # 2. Bootstrap Pip and PyInstaller within it
                subprocess.run([updater_python, "-m", "pip", "install", "--upgrade", "pip", "pyinstaller"], stdout=subprocess.DEVNULL)
                
                # 3. DYNAMICALLY GENERATE requirements_updater.txt from the updater script itself
                print(f"{CYAN} -> Analyzing standalone updater imports...{RESET}")
                _, updater_imports, _ = _parse_single_file(str(Path(BuildConfig.UPDATER_SCRIPT).resolve()))
                
                updater_pip_pkgs = set()
                for imp in updater_imports:
                    top_lvl = imp.split('.')[0]
                    # Filter out standard libraries and local modules
                    if top_lvl not in sys.stdlib_module_names and top_lvl not in DepEngine.stdlib and not top_lvl.startswith('_dummy'):
                        pip_name = DepEngine.get_pip_name(top_lvl)
                        updater_pip_pkgs.add(pip_name)
                
                req_updater = Path("requirements_updater.txt")
                with open(req_updater, "w", encoding="utf-8") as f:
                    for pkg in sorted(updater_pip_pkgs):
                        f.write(f"{pkg}\n")
                        
                print(f"{GREEN} -> Generated requirements_updater.txt with: {', '.join(updater_pip_pkgs)}{RESET}")
                
                # 4. Install the dynamically generated requirements individually
                for pkg in sorted(updater_pip_pkgs):
                    subprocess.run([updater_python, "-m", "pip", "install", pkg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                print(f"\n{CYAN}[*] Forging Standalone Updater Executable...{RESET}")
                
                # 5. Execute PyInstaller directly from the newly created updater environment
                updater_cmd = [
                    updater_python, "-m", "PyInstaller",
                    "--noconfirm", 
                    "--onefile", 
                    "--noconsole",
                    "--name", updater_base,
                    "--distpath", str(target_dir)
                ]
                
                if BuildConfig.ICON_FILE and Path(BuildConfig.ICON_FILE).exists():
                    updater_cmd.extend(["--icon", BuildConfig.ICON_FILE])
                updater_cmd.append(BuildConfig.UPDATER_SCRIPT)
                
                UIAesthetics.run_with_progress_bar(updater_cmd, "PyInstaller", step_info="Standalone Updater")
            
    finally:
        os.chdir(original_cwd)
        isolated_dist_path = Path(BuildConfig.ISOLATED_BUILD_DIR) / "dist"
        if isolated_dist_path.exists():
            scrub_dist_folder(str(isolated_dist_path), pyqt_dep=pyqt_dep, strip_metadata=prefs.get('STRIP_METADATA', BuildConfig.STRIP_METADATA))
            
            # MATURE FIX: Pass the exhaustive list so the scrubber protects transitive requirements
            prune_packaged_bloat_assets(str(isolated_dist_path), used_imports=exhaustive_imports)
            
            try:
                sign_and_embed_manifest_keys(str(isolated_dist_path))
            except Exception as e:
                print(f"{RED}[!] Manifest signing failed: {e}{RESET}")

            if Path(BuildConfig.DIST_DIR).exists(): shutil.rmtree(BuildConfig.DIST_DIR, ignore_errors=True)
            try:
                shutil.copytree(isolated_dist_path, BuildConfig.DIST_DIR, dirs_exist_ok=True)
                shutil.rmtree(isolated_dist_path, ignore_errors=True)

                # Ensure opus.dll is physically present in both the app root and _internal
                root_app_dir = Path(BuildConfig.DIST_DIR) / BuildConfig.APP_NAME
                src_opus = Path("opus.dll")
                if src_opus.exists() and root_app_dir.exists():
                    shutil.copy2(src_opus, root_app_dir / "opus.dll")
                    internal_dir = root_app_dir / "_internal"
                    if internal_dir.exists():
                        shutil.copy2(src_opus, internal_dir / "opus.dll")
                    print(f"{GREEN}[+] Injected opus.dll into output distribution folders.{RESET}")

                print(f"\n{GREEN}[+] Build successfully extracted to root '{BuildConfig.DIST_DIR}' folder!{RESET}")
            except Exception as e:
                print(f"{RED}[!] Failed to move dist folder. You can find it inside '{BuildConfig.ISOLATED_BUILD_DIR}/dist'. Error: {e}{RESET}")

        # Save operation runs independently of directory health or initial execution state flag constraints
        try:
            prefs_out = {
                "APP_NAME": BuildConfig.APP_NAME,
                "MAIN_SCRIPT": BuildConfig.MAIN_SCRIPT,
                "ATTACH_CONSOLE": BuildConfig.ATTACH_CONSOLE,
                "ISOLATED_BUILD_DIR": BuildConfig.ISOLATED_BUILD_DIR,
                "DIST_DIR": BuildConfig.DIST_DIR,
                "engine": engine,
                "folder_prefs": folder_prefs,
                "selected_files": selected_files,
                "auto_fix_cycles": auto_fix_ans != 'n',
                "fixed_cycle_files": files_to_fix,
                "ICON_FILE": BuildConfig.ICON_FILE,
                "PRESERVE_UPDATER": prefs.get('PRESERVE_UPDATER', BuildConfig.PRESERVE_UPDATER),
                "STRIP_METADATA": prefs.get('STRIP_METADATA', BuildConfig.STRIP_METADATA),
                "UPDATER_SCRIPT": prefs.get('UPDATER_SCRIPT', BuildConfig.UPDATER_SCRIPT)
            }
            with open(BuildConfig.PREFS_FILE, "w", encoding="utf-8") as f:
                json.dump(prefs_out, f, indent=4)
            print(f"{GREEN}[+] Build preferences successfully saved to '{BuildConfig.PREFS_FILE}'!{RESET}")
        except Exception as e:
            print(f"{RED}[!] Could not save build preferences: {e}{RESET}")

        if not use_saved: print(f"\n{CYAN}[*] Preserving Isolated Build Environment caches for faster subsequent builds.{RESET}")