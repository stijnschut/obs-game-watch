#!/usr/bin/env python3
"""
obs_game_watch.py — OBS profile/scene switcher + always-on replay buffer.

Detects fullscreen games (Wayland-native via KWin D-Bus, or Xwayland via xprop)
and switches OBS to the matching profile/scene. The replay buffer stays on at
all times — no more missed clips.

Requirements:
    pip install obsws-python
    sudo pacman -S xdotool xorg-xprop python-dbus

OBS:
    Tools → WebSocket Server Settings → Enable, set a password
"""

import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import obsws_python as obs

from games_user import DEFAULT_FULLSCREEN, GAMES, Game

# ─── Configuration ───────────────────────────────────────────────────────────

# Load .env (optional) — makes env vars accessible via os.getenv()
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

OBS_HOST = os.getenv("OBS_HOST", "localhost")
OBS_PORT = int(os.getenv("OBS_PORT", "4455"))
OBS_PASSWORD = os.getenv("OBS_PASSWORD", "")

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "4"))
RECONNECT_INTERVAL = float(os.getenv("RECONNECT_INTERVAL", "10"))
PROFILE_SWITCH_WAIT = float(os.getenv("PROFILE_SWITCH_WAIT", "1.5"))


# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Fullscreen detection (KDE Wayland + Xwayland) ───────────────────────────


def _run(cmd: list[str]) -> Optional[str]:
    try:
        return subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True
        ).strip()
    except subprocess.CalledProcessError:
        return None


def get_fullscreen_window() -> Optional[dict]:
    """
    Return info about the topmost fullscreen window, or None.

    Works on both KDE Wayland (KWin D-Bus) and Xwayland (xprop).
    """
    # ── 1. KWin D-Bus — Wayland-native windows ─────────────────────────────
    try:
        import dbus

        bus = dbus.SessionBus()
        kwin = bus.get_object("org.kde.KWin", "/KWin")
        info = kwin.queryWindowInfo(dbus_interface="org.kde.KWin", timeout=2)

        if info and info.get("fullscreen"):
            caption = info.get("caption", "") or ""
            wm_class = info.get("resourceClass", "") or ""
            return {
                "id": str(info.get("uuid", "")),
                "title": caption.lower(),
                "wm_class": wm_class.lower(),
                "source": "wayland",
            }
    except Exception:
        pass

    # ── 2. xprop — X11 / Xwayland windows (Proton, etc.) ───────────────────
    client_list = _run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])
    if not client_list:
        return None

    ids = re.findall(r"0x[0-9a-fA-F]+", client_list)
    if not ids:
        return None

    for win_id in reversed(ids):
        state = _run(["xprop", "-id", win_id, "_NET_WM_STATE"]) or ""
        if "_NET_WM_STATE_FULLSCREEN" not in state:
            continue

        title_raw = _run(["xprop", "-id", win_id, "_NET_WM_NAME"]) or ""
        wm_class = _run(["xprop", "-id", win_id, "WM_CLASS"]) or ""

        m = re.search(r'"(.+)"', title_raw)
        title = m.group(1) if m else ""

        return {
            "id": win_id,
            "title": title.lower(),
            "wm_class": wm_class.lower(),
            "source": "x11",
        }

    return None


# ─── Game matching ───────────────────────────────────────────────────────────


def _pgrep(pattern: str) -> bool:
    try:
        subprocess.check_output(
            ["pgrep", "-f", "-i", pattern], stderr=subprocess.DEVNULL
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _matches(game: Game, window: dict) -> bool:
    for proc in game.processes:
        if _pgrep(proc):
            return True
    for pattern in game.window_patterns:
        if pattern in window["title"] or pattern in window["wm_class"]:
            return True
    return False


def match_game(window: dict) -> Game:
    """Return a Game from GAMES, or DEFAULT_FULLSCREEN as fallback."""
    for game in GAMES:
        if _matches(game, window):
            return game
    return DEFAULT_FULLSCREEN


# ─── OBS helpers ─────────────────────────────────────────────────────────────


def get_profile(client: obs.ReqClient) -> str:
    return client.get_profile_list().current_profile_name


def get_scene(client: obs.ReqClient) -> str:
    return client.get_current_program_scene().scene_name


def replay_active(client: obs.ReqClient) -> bool:
    try:
        return client.get_replay_buffer_status().output_active
    except Exception:
        return False


def apply_game(client: obs.ReqClient, game: Game) -> None:
    """Switch OBS to the given game's profile/scene and ensure replay buffer is on."""
    if get_profile(client) != game.profile:
        log.info(f"Profile  → {game.profile}")
        client.set_current_profile(game.profile)
        time.sleep(PROFILE_SWITCH_WAIT)

    if get_scene(client) != game.scene:
        log.info(f"Scene    → {game.scene}")
        client.set_current_program_scene(game.scene)

    if not replay_active(client):
        log.info("Replay buffer → starting")
        client.start_replay_buffer()


def apply_idle(client: obs.ReqClient) -> None:
    """No fullscreen → revert to Ultrawide profile/scene, replay stays on."""
    apply_game(client, DEFAULT_FULLSCREEN)


# ─── Main loop ───────────────────────────────────────────────────────────────


def run(client: obs.ReqClient) -> None:
    current_game: Optional[Game] = None
    log.info("Watch started — Ultrawide + replay buffer active.")
    apply_game(client, DEFAULT_FULLSCREEN)

    while True:
        window = get_fullscreen_window()

        if window:
            game = match_game(window)
            if game != current_game:
                log.info(
                    f"Fullscreen ({window.get('source', '?')}): "
                    f"'{window['title'][:60]}' → {game.name}"
                )
                apply_game(client, game)
                current_game = game
        else:
            if current_game is not None:
                log.info("No longer fullscreen")
                apply_idle(client)
                current_game = None

        time.sleep(POLL_INTERVAL)


def main() -> None:
    while True:
        try:
            log.info(f"Connecting to OBS ({OBS_HOST}:{OBS_PORT})...")
            client = obs.ReqClient(
                host=OBS_HOST,
                port=OBS_PORT,
                password=OBS_PASSWORD,
                timeout=5,
            )
            log.info("Connected.")
            run(client)

        except KeyboardInterrupt:
            log.info("Stopped.")
            sys.exit(0)

        except Exception as e:
            log.warning(f"OBS connection lost: {e}")
            log.info(f"Retrying in {RECONNECT_INTERVAL}s...")
            time.sleep(RECONNECT_INTERVAL)


if __name__ == "__main__":
    main()
