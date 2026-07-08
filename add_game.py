#!/usr/bin/env python3
"""
add_game.py — interactively add a 16:9 game to your personal game list.

Usage:
    1. Launch your game in fullscreen
    2. Run: python add_game.py
    3. Confirm suggestions or edit them
"""

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent
GAMES_USER_PATH = SCRIPT_DIR / "games_user.py"
GAMES_PY_PATH = SCRIPT_DIR / "games.py"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _run(cmd: list[str]) -> Optional[str]:
    try:
        return subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except subprocess.CalledProcessError:
        return None


# ─── Fullscreen detection (same logic as obs_game_watch.py) ──────────────────


def get_fullscreen_window() -> Optional[dict]:
    """Detect the active fullscreen X11/XWayland window via xdotool + xprop.

    Wayland-native windows cannot be queried non-interactively (KWin's
    queryWindowInfo() shows a crosshair cursor). For Wayland-native games,
    use manual entry instead (enter the process name when prompted).
    """
    win_id = _run(["xdotool", "getactivewindow"])
    if not win_id or win_id == "2097152":  # 2097152 = XWayland root on Wayland
        return None

    state = _run(["xprop", "-id", win_id, "_NET_WM_STATE"]) or ""
    if "_NET_WM_STATE_FULLSCREEN" not in state:
        return None

    title_raw = _run(["xprop", "-id", win_id, "_NET_WM_NAME"]) or ""
    wm_class = _run(["xprop", "-id", win_id, "WM_CLASS"]) or ""
    m = re.search(r'"(.+)"', title_raw)
    title = m.group(1) if m else ""

    return {
        "id": win_id,
        "title": title,
        "title_lower": title.lower(),
        "wm_class": wm_class.lower(),
    }


# ─── Process detection via PID ───────────────────────────────────────────────


def detect_process(win_id: str) -> Optional[str]:
    """Try to get the executable name of the window's PID."""
    if win_id.startswith("{"):  # KWin UUID
        try:
            import dbus

            bus = dbus.SessionBus()
            kwin = bus.get_object("org.kde.KWin", "/KWin")
            info = kwin.getWindowInfo(win_id, dbus_interface="org.kde.KWin")
            pid = info.get("pid", 0)
            if pid:
                comm = _run(["ps", "-p", str(pid), "-o", "comm="])
                return comm.strip() if comm else None
        except Exception:
            return None
    else:  # X11 hex ID
        pid_raw = _run(["xprop", "-id", win_id, "_NET_WM_PID"])
        if pid_raw:
            m = re.search(r"= (\d+)", pid_raw)
            if m:
                comm = _run(["ps", "-p", m.group(1), "-o", "comm="])
                return comm.strip() if comm else None
    return None


# ─── Suggest patterns ────────────────────────────────────────────────────────


def suggest_patterns(window: dict) -> tuple[list[str], list[str]]:
    """Suggest pgrep patterns and window title patterns based on window info."""
    proc_patterns: list[str] = []
    window_patterns: list[str] = []

    proc = detect_process(window["id"])
    if proc and proc.lower() not in (
        "steam",
        "proton",
        "wine",
        "wine64",
        "wine-preloader",
        "lutris",
        "gamescope",
    ):
        proc_patterns.append(proc)

    words = window["title_lower"].split()
    if words:
        window_patterns.append(" ".join(words[:3]))

    wm_parts = window["wm_class"].replace('"', "").split(",")
    wm_first = wm_parts[0].strip() if wm_parts else ""
    if wm_first and wm_first not in window_patterns:
        window_patterns.append(wm_first)

    return proc_patterns, window_patterns


# ─── Generate Game() entry ──────────────────────────────────────────────────


def make_entry(name: str, procs: list[str], patterns: list[str]) -> str:
    """Generate a Game() definition string for games_user.py."""
    indent = " " * 4

    def q(s: str) -> str:
        return f'"{s}"'

    lines = [
        f"{indent}Game(",
        f"{indent}    name={q(name)},",
        f"{indent}    profile=PROFILE_169,",
        f"{indent}    scene=SCENE_169,",
    ]
    if procs:
        lines.append(f"{indent}    processes=[{', '.join(q(p) for p in procs)}],")
    if patterns:
        lines.append(
            f"{indent}    window_patterns=[{', '.join(q(p) for p in patterns)}],"
        )
    lines.append(f"{indent}),")
    return "\n".join(lines)


# ─── Manage games_user.py ────────────────────────────────────────────────────

GAMES_USER_TEMPLATE = '''"""
games_user.py — your personal game list.

Auto-created by add_game.py. This file is in .gitignore.
"""

from games import Game, PROFILE_169, PROFILE_UW, SCENE_169, SCENE_UW


# ─── Default profile for unknown / UW-native fullscreen apps ─────────────────

DEFAULT_FULLSCREEN = Game(
    name="Unknown / UW",
    profile=PROFILE_UW,
    scene=SCENE_UW,
)

# ─── 16:9 games — will switch profile & scene when detected ──────────────────

GAMES: list[Game] = [
    # Add your 16:9 games here using add_game.py
]
'''


def ensure_games_user() -> None:
    """Create games_user.py with default template if it doesn't exist."""
    if not GAMES_USER_PATH.exists():
        GAMES_USER_PATH.write_text(GAMES_USER_TEMPLATE)
        print("📄 Created games_user.py — add your 16:9 games here.")
        print()


def game_already_exists(name: str) -> bool:
    """Check if a game with this name is already in GAMES."""
    content = GAMES_USER_PATH.read_text()
    pattern = rf'name="{re.escape(name)}"'
    return bool(re.search(pattern, content))


def append_to_games(entry: str, name: str) -> None:
    """Append a Game() entry to the GAMES list in games_user.py."""
    ensure_games_user()

    if game_already_exists(name):
        print(f"⚠️  '{name}' is already in GAMES — skipped.")
        return

    content = GAMES_USER_PATH.read_text()
    lines = content.split("\n")

    # Find the GAMES list definition
    start_idx = None
    for i, line in enumerate(lines):
        if "GAMES: list[Game] = [" in line:
            start_idx = i
            break

    if start_idx is None:
        print("❌ Cannot find GAMES list in games_user.py")
        sys.exit(1)

    # Find the closing bracket by counting nesting
    brace_count = 0
    end_idx = None
    for i in range(start_idx, len(lines)):
        for ch in lines[i]:
            if ch == "[":
                brace_count += 1
            elif ch == "]":
                brace_count -= 1
        if brace_count == 0:
            end_idx = i
            break

    if end_idx is None:
        print("❌ Cannot find end of GAMES list")
        sys.exit(1)

    # Insert before the closing bracket
    new_lines = lines[:end_idx]
    new_lines.append("    # Added by add_game.py")
    new_lines.append(entry)
    new_lines.append("")
    new_lines.append(lines[end_idx])
    new_lines.extend(lines[end_idx + 1 :])

    GAMES_USER_PATH.write_text("\n".join(new_lines))
    print(f"✅ '{name}' added to GAMES in games_user.py")


# ─── Terminal helpers ───────────────────────────────────────────────────────


def prompt(label: str, default: str = "") -> str:
    if default:
        val = input(f"  {label} [{default}]: ").strip()
        return val if val else default
    val = input(f"  {label}: ").strip()
    return val


def manual_entry() -> tuple[str, list[str], list[str]]:
    """Prompt the user to manually enter a game."""
    print()
    print("── Manual entry ──")
    name = prompt("Game name")
    if not name:
        print("Cancelled.")
        sys.exit(1)

    procs: list[str] = []
    win_pats: list[str] = []

    proc_str = prompt("pgrep pattern (Enter to skip)")
    if proc_str:
        procs = [proc_str]

    win_str = prompt("Window title pattern (Enter to skip)")
    if win_str:
        win_pats = [win_str]

    if not procs and not win_pats:
        print("⚠️  At least one pgrep pattern or window title pattern is required.")
        proc_str = prompt("pgrep pattern")
        if proc_str:
            procs = [proc_str]

    return name, procs, win_pats


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    print("═" * 50)
    print("  OBS Game Watch — Add a 16:9 game")
    print("═" * 50)
    print()

    ensure_games_user()

    # ── Step 1: auto-detect ────────────────────────────────────────────────
    print("🔍 Scanning automatically", end="", flush=True)
    for _ in range(3):
        print(".", end="", flush=True)
        time.sleep(1)
    print()

    window = get_fullscreen_window()

    if window:
        print(f"✅ Game detected: {window['title']}")
        print()
    else:
        # ── Wait for the user to click the game ────────────────────────────
        print("❌ No game detected automatically.")
        print()
        print("   Click the game window you want to add.")
        print("   (Ctrl+C to cancel)")
        print()

        while True:
            window = get_fullscreen_window()
            if window:
                print(f"✅ Game detected: {window['title']}")
                print()
                break
            time.sleep(1)

    # ── Window found ───────────────────────────────────────────────────────
    print(f"Detected fullscreen window:")
    print(f"  Title : {window['title']}")
    print(f"  Class : {window['wm_class']}")
    print()

    proc_patterns, window_patterns = suggest_patterns(window)

    if proc_patterns:
        print(f"  🔍 Process suggestion : {', '.join(proc_patterns)}")
    else:
        print("  ⚠️  Could not detect process name automatically.")
        print("     Tip: run 'pgrep -a -f -i <game>' in another terminal.")

    if window_patterns:
        print(f"  🔍 Window suggestion  : {', '.join(window_patterns)}")
    print()

    resp = input("Add this game to the 16:9 list? (y/N) ").strip().lower()
    if resp not in ("y", "yes"):
        print()
        name, proc_patterns, window_patterns = manual_entry()
    else:
        print("── Edit values (Enter = keep suggestion) ──")
        name = prompt("Game name", window["title"])

        proc_str = prompt(
            "pgrep pattern (leave empty to skip)",
            proc_patterns[0] if proc_patterns else "",
        )
        if proc_str:
            proc_patterns = [proc_str]

        win_str = prompt(
            "Window title pattern",
            window_patterns[0] if window_patterns else "",
        )
        if win_str:
            window_patterns = [win_str]

    entry = make_entry(name, proc_patterns, window_patterns)

    print()
    print("── Entry to add ──")
    print(entry)
    print()

    resp = input("Save? (y/N) ").strip().lower()
    if resp not in ("y", "yes"):
        print("Cancelled.")
        return

    append_to_games(entry, name)


if __name__ == "__main__":
    main()
