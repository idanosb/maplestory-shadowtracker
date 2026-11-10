"""Screen-share mode: which game areas the app reads, and whether our overlays cover them.

Normally the strip and the spots panel are hidden from every screen capture, the app's own
included. With "Show in screen share" on they are visible to Discord / OBS, and so also to the
app's own capture: an overlay over a read area breaks that reading. These pure helpers find
the read areas of the last frame and the ones an overlay covers.
"""
from __future__ import annotations

from expstrip.vision.layout import BOTTOM_FRACTION, Rect, Regions
from expstrip.vision.pickups import BOTTOM_INSET, pickup_rect

STATUS_BAR = "status bar and quick slots"
PICKUPS = "pickup lines"
MINIMAP = "map name"


def read_zones(frame_w: int, frame_h: int, regions: Regions | None) -> list[tuple[str, Rect]]:
    """Client-area rects the app reads from: the bottom band (EXP, level, potion keys), the
    pickup lines above it on the right, and the minimap title when one was found. The open
    inventory is read too, but it can sit anywhere, so it is not a zone."""
    if frame_w <= 0 or frame_h <= 0:
        return []
    scale = regions.scale if regions is not None else None
    band = round(BOTTOM_INSET * scale) if scale else round(frame_h * BOTTOM_FRACTION)
    band = min(frame_h, max(1, band))
    zones = [(STATUS_BAR, Rect(0, frame_h - band, frame_w, band))]
    if scale:
        r = pickup_rect(_Shape(frame_w, frame_h), scale)
        if r is not None:
            zones.append((PICKUPS, r))
    if regions is not None and regions.minimap_title is not None:
        zones.append((MINIMAP, regions.minimap_title))
    return zones


def to_screen(zones: list[tuple[str, Rect]], left: int, top: int) -> list[tuple[str, Rect]]:
    return [(n, Rect(r.x + left, r.y + top, r.w, r.h)) for n, r in zones]


def intersects(a: Rect, b: Rect) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def covered(overlays: list[Rect], zones: list[tuple[str, Rect]]) -> list[str]:
    """Names of the zones any overlay rect intersects, in zone order."""
    return [n for n, z in zones if any(intersects(o, z) for o in overlays)]


class _Shape:
    """Stand-in frame for pickup_rect, which only reads .shape."""

    def __init__(self, w: int, h: int) -> None:
        self.shape = (h, w, 3)
