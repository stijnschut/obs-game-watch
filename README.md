# OBS Game Watch

Dynamically switch OBS profiles & scenes based on the game you're playing.
Replay buffer stays on at all times.

## How it works

When you launch a game in fullscreen, this script detects it and tells OBS
to switch to the matching profile/scene. Close the game (or alt-tab away)
and it reverts to your ultrawide/default profile, with the replay buffer
still running.

### Detection methods

| Method | Works for | Tool |
|---|---|---|
| xdotool + xprop | X11 / Xwayland windows (Proton, Wine) | `xdotool`, `xorg-xprop` |
| pgrep (process) | Any game (Wayland-native fallback) | `pgrep` |

**Note:** KWin's D-Bus `queryWindowInfo()` is interactive (shows a crosshair
cursor), so it is intentionally NOT used. Wayland-native games (CS2, etc.)
are detected by running process instead — add their process name via
`add_game.py` and it will match when the game is running.

## Requirements

### Arch Linux

```bash
sudo pacman -S xdotool xorg-xprop libnotify
pip install obsws-python
```

### OBS Studio

1. Open OBS → **Tools** → **WebSocket Server Settings**
2. **Enable WebSocket server**
3. Set a password and note it down

## Setup

### 1. Configure OBS profiles & scenes

Create the following profiles and scenes in OBS:

| Name | Purpose |
|---|---|
| Profile `Ultrawide` / Scene `43:18` | Your default ultrawide setup |
| Profile `16:9` / Scene `16:9` | 16:9 layout for narrower games |

If your profile/scene names differ, edit the constants in `games.py`.

### 2. Configure secrets

Copy the example env file and fill in your OWS WebSocket password:

```bash
cp .env.example .env
```

Edit `.env`:

```env
OBS_HOST=localhost
OBS_PORT=4455
OBS_PASSWORD=your-password-here
```

All settings are optional except `OBS_PASSWORD`. The timing values can be
uncommented if you need to tweak them.

### 3. Add your 16:9 games

Launch a 16:9 game in fullscreen, then run:

```bash
python add_game.py
```

The script detects the active game window, suggests process / title patterns,
and appends an entry to `games_user.py`.

Games that natively support 21:9 (ultrawide) don't need to be added,
they'll automatically use the default `Ultrawide` profile.

## Runtime behaviour

Once started, the script:

1. **Tries to connect** to OBS WebSocket (up to 3 attempts, 10s apart).
2. On success: sends a **desktop notification** ✅, sets the default profile
   (Ultrawide), and **starts the replay buffer**.
3. **Polls every 4 seconds**:
   - **X11/XWayland**: checks if the active window is fullscreen via xdotool + xprop
   - **Wayland-native**: matched by running process (pgrep)
4. When a game is detected → switches profile/scene and starts replay buffer.
5. When no game is detected → reverts to the default Ultrawide profile.
6. If the **connection drops** mid-session, it retries up to 3 times again.
7. After **3 failed attempts**: sends a critical notification and **stops**
   (start OBS and restart the service manually).
8. **Clip on demand**: send `SIGUSR1` to save the replay buffer. The script
   catches the signal, calls `SaveReplayBuffer`, and listens for the
   `ReplayBufferSaved` event to show a notification with the filename.
   (Set up a KDE global shortcut — see below.)

Desktop notifications use `notify-send` (KDE, GNOME, dunst, etc.):

| Event | Notification | Urgency |
|---|---|---|
| Connected to OBS | ✅ Verbonden met OBS WebSocket | normal |
| Connection failed | ⚠️ Opnieuw proberen... (1/3, 2/3, 3/3) | normal |
| Gave up | 🛑 Gestopt na 3 pogingen — start OBS en herstart | critical |
| Stopped (Ctrl+C) | 🛑 Gestopt | normal |
| Clip saved | ✅ Clip opgeslagen + bestandsnaam | normal |

## Running

### Manually

```bash
python obs_game_watch.py
```

The script runs in the foreground. It connects to OBS, sets the default
profile, starts the replay buffer, and waits for fullscreen windows.

Press **Ctrl+C** to stop.

### Automatically at startup (recommended)

The script needs to run continuously for the watchdog to work. The cleanest
way is a **systemd user service** — it starts automatically after login,
restarts on failure, and keeps logs.

Create the service file and enable it:

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/obs-game-watch.service << 'EOF'
[Unit]
Description=OBS Game Watch — auto-switch OBS profiles/scenes based on fullscreen game
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/scripts/obs-game-watch/obs_game_watch.py
Restart=on-failure
RestartSec=30
StartLimitBurst=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now obs-game-watch.service
```

Check that it's running:

```bash
systemctl --user status obs-game-watch.service
```

View live logs:

```bash
journalctl --user -u obs-game-watch -f
```

Stop, restart or disable it:

```bash
systemctl --user stop obs-game-watch.service       # stop now
systemctl --user restart obs-game-watch.service    # restart
systemctl --user disable obs-game-watch.service    # don't start at boot
```

> **Why systemd?** It starts after the graphical session is ready (so D-Bus
> and OBS are available). The script tries 3 times to connect and then stops
> if OBS isn't available — start OBS first, then restart the service.

> **Note:** Make sure `libnotify` is installed for desktop notifications:
> `sudo pacman -S libnotify`.

## Clip notifications

The script listens for the `ReplayBufferSaved` event from OBS. Whenever you
save the replay buffer (via an OBS hotkey, the "Save Replay" button, or any
other method), you'll get a desktop notification with the filename.

To set up a hotkey in OBS: **Settings → Hotkeys** → find "Save Replay Buffer"
→ set your preferred shortcut (e.g. Shift+F12).

## File structure

| File | In git? | Purpose |
|---|---|---|
| `obs_game_watch.py` | ✅ | Main watchdog daemon |
| `add_game.py` | ✅ | Interactive game list manager |
| `games.py` | ✅ | `Game` dataclass & profile constants |
| `games_user.py` | ❌ | Your personal 16:9 game list (auto-created) |
| `.env` | ❌ | Secrets (OBS password), copy of `.env.example` |
| `.env.example` | ✅ | Template with all config keys |
| `obs-game-watch.service` | ❌ | systemd user service (in `~/.config/systemd/user/`) |
