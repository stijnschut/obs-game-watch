#!/usr/bin/env python3
"""
obs_game_watch.py — OBS profile/scene switcher + always-on replay buffer.

Detects fullscreen games (XWayland via xdotool+xprop, Wayland-native via process)
and switches OBS to the matching profile/scene. The replay buffer stays on at
all times — no more missed clips.

Wayland-native detection: KWin's D-Bus queryWindowInfo() is interactive (shows
crosshair cursor), so it is NOT used. Instead, Wayland-native games are matched
by running process (pgrep). Add process patterns in games_user.py via add_game.py.

Clip shortcut: Send SIGUSR1 to save the replay buffer. The ReplayBufferSaved
event triggers a desktop notification with the filename.

Requirements:
    pip install obsws-python
    sudo pacman -S xdotool xorg-xprop libnotify python-dbus

OBS:
    Tools → WebSocket Server Settings → Enable, set a password
"""

import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import obsws_python as obs
from games_user import DEFAULT_FULLSCREEN, GAMES, Game

# ─── Configuration ───────────────────────────────────────────────────────────

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
STABLE_COUNT = int(os.getenv("STABLE_COUNT", "2"))
MAX_RETRIES = 3


# ─── Desktop notifications ──────────────────────────────────────────────────


def notify(title: str, message: str, urgency: str = "normal") -> None:
    """Send a desktop notification via notify-send."""
    try:
        subprocess.run(
            ["notify-send", "--app-name=OBS Game Watch", f"--urgency={urgency}", title, message],
            timeout=5,
        )
    except Exception:
        pass


def notify_interactive(title: str, message: str, timeout: int = 30) -> bool:
    """Send interactive notification with Restart/Cancel buttons.

    Uses D-Bus directly for the action buttons. Blocks up to *timeout*
    seconds, then returns. Returns True if the user clicked Restart.
    """
    try:
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        notif = bus.get_object(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
        )
        iface = dbus.Interface(notif, "org.freedesktop.Notifications")

        notif_id = iface.Notify(
            "OBS Game Watch", 0, "",
            title, message,
            ["restart", "Restart service", "cancel", "Cancel"],
            {"urgency": dbus.Byte(2, variant_level=1)},
            timeout * 1000,
        )

        result: dict = {"action": None}
        loop = GLib.MainLoop()

        def on_action(nid, action_key):
            if nid == notif_id:
                result["action"] = action_key
                loop.quit()

        def on_close(nid, _reason):
            if nid == notif_id:
                loop.quit()

        notif.connect_to_signal(
            "ActionInvoked", on_action,
            dbus_interface="org.freedesktop.Notifications",
        )
        notif.connect_to_signal(
            "NotificationClosed", on_close,
            dbus_interface="org.freedesktop.Notifications",
        )

        GLib.timeout_add_seconds(timeout + 1, loop.quit)
        loop.run()

        if result["action"] == "restart":
            subprocess.run(
                ["systemctl", "--user", "restart", "obs-game-watch.service"],
                timeout=5,
            )
            return True
        return False

    except Exception:
        return False


# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Fullscreen detection ───────────────────────────────────────────────────


def _run(cmd: list[str]) -> Optional[str]:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError:
        return None


def get_fullscreen_window() -> Optional[dict]:
    """Return info about the active fullscreen X11/XWayland window, or None."""
    win_id = _run(["xdotool", "getactivewindow"])
    if not win_id or win_id == "2097152":
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
        "title": title.lower(),
        "wm_class": wm_class.lower(),
        "source": "x11",
    }


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


def _any_game_running() -> Optional[Game]:
    """Return the first Game whose process is running, or None."""
    for game in GAMES:
        for proc in game.processes:
            if _pgrep(proc):
                return game
    return None


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


def _stop_replay(client: obs.ReqClient) -> bool:
    try:
        if client.get_replay_buffer_status().output_active:
            log.info("Replay buffer → stopping")
            client.stop_replay_buffer()
            time.sleep(1)
            return True
    except Exception:
        pass
    return False


def apply_game(client: obs.ReqClient, game: Game) -> None:
    needs_restart = False
    if get_profile(client) != game.profile:
        needs_restart = _stop_replay(client)
        log.info(f"Profile  → {game.profile}")
        client.set_current_profile(game.profile)
        time.sleep(PROFILE_SWITCH_WAIT)
    if get_scene(client) != game.scene:
        log.info(f"Scene    → {game.scene}")
        client.set_current_program_scene(game.scene)
    if needs_restart or not replay_active(client):
        log.info("Replay buffer → starting")
        client.start_replay_buffer()


def apply_idle(client: obs.ReqClient) -> None:
    apply_game(client, DEFAULT_FULLSCREEN)


# ─── Clip signal (SIGUSR1) ───────────────────────────────────────────────────

_clip_requested: bool = False


def _handle_clip_signal(signum: int, frame) -> None:
    global _clip_requested
    _clip_requested = True


# ─── OBS event listener ────────────────────────────────────────────────────

_event_client: Optional[obs.EventClient] = None


def on_replay_buffer_saved(data):
    path = data.saved_replay_path
    filename = os.path.basename(path)
    log.info(f"Replay buffer saved to: {path}")
    notify("OBS Game Watch", f"Clip saved ✅\n{filename}")


def _start_event_client() -> Optional[obs.EventClient]:
    global _event_client
    try:
        _event_client = obs.EventClient(
            host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD
        )
        _event_client.callback.register(on_replay_buffer_saved)
        log.info("Event listener active — waiting for ReplayBufferSaved events.")
        return _event_client
    except Exception as e:
        log.warning(f"Event listener failed to start: {e}")
        return None


# ─── Main loop ───────────────────────────────────────────────────────────────


def run(client: obs.ReqClient) -> None:
    global _clip_requested
    current_game: Optional[Game] = None
    candidate_game: Optional[Game] = None
    stable: int = 0

    log.info("Watch started — Ultrawide + replay buffer active.")
    apply_game(client, DEFAULT_FULLSCREEN)

    while True:
        if _clip_requested:
            _clip_requested = False
            try:
                log.info("Clip requested — saving replay buffer...")
                client.save_replay_buffer()
            except Exception as e:
                log.warning(f"Failed to save replay buffer: {e}")
                notify("OBS Game Watch", "Clip save failed ❌", urgency="critical")

        game: Optional[Game] = None
        source = ""

        window = get_fullscreen_window()
        if window:
            game = match_game(window)
            source = window.get("source", "?")
        else:
            game = _any_game_running()
            if game:
                source = "process"

        if game == candidate_game:
            stable += 1
        else:
            candidate_game = game
            stable = 1

        if stable >= STABLE_COUNT:
            if game and game != current_game:
                log.info(f"{source}: → {game.name}")
                notify("OBS Game Watch", f"Profile → {game.profile}\nScene → {game.scene}")
                apply_game(client, game)
                current_game = game
            elif not game and current_game is not None:
                log.info("No longer fullscreen — reverting to default")
                notify("OBS Game Watch", "Profile → Ultrawide\nScene → 43:18")
                apply_idle(client)
                current_game = None
                candidate_game = None

        time.sleep(POLL_INTERVAL)


# ─── Entry point ────────────────────────────────────────────────────────────


def main() -> None:
    signal.signal(signal.SIGUSR1, _handle_clip_signal)
    log.info("Clip shortcut: send SIGUSR1 to save replay buffer.")

    retries = 0

    while retries < MAX_RETRIES:
        try:
            retries += 1
            log.info(
                f"Connecting to OBS ({OBS_HOST}:{OBS_PORT})... "
                f"(attempt {retries}/{MAX_RETRIES})"
            )
            client = obs.ReqClient(
                host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD, timeout=5,
            )
            log.info("Connected.")
            notify("OBS Game Watch", "Connected to OBS WebSocket ✅")
            _start_event_client()
            retries = 0
            run(client)

        except KeyboardInterrupt:
            notify("OBS Game Watch", "Stopped.")
            log.info("Stopped.")
            sys.exit(0)

        except Exception as e:
            log.warning(f"OBS connection failed: {e}")
            if retries < MAX_RETRIES:
                log.info(f"Retrying in {RECONNECT_INTERVAL}s...")
                time.sleep(RECONNECT_INTERVAL)
            else:
                log.error(f"Giving up after {MAX_RETRIES} failed attempts.")
                notify_interactive(
                    "OBS Game Watch",
                    "Failed to connect after 3 attempts.\n"
                    "Start OBS WebSocket and click Restart to try again.",
                )
                sys.exit(0)


if __name__ == "__main__":
    main()
