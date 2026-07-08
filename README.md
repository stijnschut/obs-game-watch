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
| xprop | X11 / Xwayland windows (Proton, Wine) | `xorg-xprop` |
| KWin D-Bus | KDE Wayland native games (CS2, etc.) | `python-dbus` |

xprop is running first and tries to detect it automatically, if it does not find anything you can manually click the game in focus to detect it with KWin D-Bus.

## Requirements

### Arch Linux

```bash
sudo pacman -S xdotool xorg-xprop python-dbus
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
RestartSec=10

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

Stop or disable it:

```bash
systemctl --user stop obs-game-watch.service       # stop now
systemctl --user disable obs-game-watch.service    # don't start at boot
```

> **Why systemd?** It starts after the graphical session is ready (so D-Bus
> and OBS are available), restarts automatically if the script crashes,
> and gives you proper logging via `journalctl`.

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
