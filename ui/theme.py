"""Общая палитра и мелкие хелперы интерфейса."""

from __future__ import annotations

BG = "#0b1220"
SIDEBAR = "#0f172a"
CARD = "#151e32"
CARD2 = "#1a2440"
BTN2_BG = "#2a3d68"
BTN2_HOVER = "#3a548c"
BRIGHT = "#f1f5f9"
ACCENT = "#22d3ee"
ACCENT2 = "#8b5cf6"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"
GOOD = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"


def _fmt(v) -> str:
    return "—" if v is None or v == "" else str(v)

def resource_path(*parts) -> str:
    """Путь к ресурсу: работает и из исходников, и из exe (MEIPASS)."""
    import os
    import sys
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)
