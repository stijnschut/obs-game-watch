"""
games.py — shared types and profile constants.

This file is checked into git.
Your personal game list goes into games_user.py (auto-created by add_game.py).
"""

from dataclasses import dataclass, field


@dataclass
class Game:
    name: str
    profile: str
    scene: str
    processes: list[str] = field(default_factory=list)
    window_patterns: list[str] = field(default_factory=list)


# ─── Profile & scene name constants ──────────────────────────────────────────
# Change these to match your OBS setup.

PROFILE_169 = "16:9"
PROFILE_UW = "Ultrawide"

SCENE_169 = "16:9"
SCENE_UW = "43:18"
