"""Learn the quickslot icon of a potion from the price list that has no icon yet.

Potion counts are read by finding each potion's icon on the quickslot keys
(expstrip.vision.quickslots), so a new potion needs its icon once. It is learned without
help: when such a potion is picked up (an item line "<Name> xN earned. (Use)"), the key
holding it shows its count rise by N. Every quickslot scan reads all 12 keys' counts; the
key whose count rose by exactly the quantity picked up since the scan before the pickup,
and is the only one that did, holds the potion. Its icon is cut from that key.

The same key must be found CONFIRM times (separate pickups) with matching icons before the
icon is saved to <learned_dir>/<Name>.png, so a coincidence (a purchase on another key at
the same moment) never teaches a wrong icon. Pickups whose key is not found within
PENDING_S are dropped; the next pickup tries again.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from expstrip.vision import quickslots
from expstrip.vision.items import ItemLine

log = logging.getLogger(__name__)

CONFIRM = 2          # separate pickups that must point at the same key and icon
PENDING_S = 20.0     # s a pickup waits for its key's count to rise
ICON_AGREE = 0.9     # TM_CCOEFF_NORMED between two cuts of the same key's icon
_HISTORY_S = 15.0    # s of key scans kept to find the scan before a pickup


class IconLearner:
    def __init__(self, learned_dir: Path | None,
                 read_counts: Callable | None = None, cut_icon: Callable | None = None) -> None:
        self._dir = Path(learned_dir) if learned_dir is not None else None
        self._read_counts = read_counts or self._default_counts
        self._cut_icon = cut_icon or self._default_icon
        self._scans: list[tuple[float, dict[int, int]]] = []
        self._pending: dict[str, dict] = {}     # name -> {"qty", "t", "base"}
        self._votes: dict[str, list[tuple[int, np.ndarray]]] = {}
        self.learned: list[str] = []
        if self._dir is not None:
            quickslots.add_icon_dir(self._dir)

    # -- inputs ------------------------------------------------------
    def on_items(self, lines: list[ItemLine], potions: list[str], now: float) -> None:
        """Item lines new this tick; `potions`: the price list's potion names."""
        for it in lines:
            if it.tab != "Use" or it.name not in potions or quickslots.has_icon(it.name):
                continue
            p = self._pending.get(it.name)
            if p is None:
                self._pending[it.name] = {"qty": it.qty, "t": now, "base": self._base(now)}
            else:
                p["qty"] += it.qty
                p["t"] = now

    def on_scan(self, frame: np.ndarray, scale: float, now: float) -> list[str]:
        """A quickslot scan; returns names whose icon was learned now."""
        # scans run even with nothing pending: a pickup needs the scan from before it
        origins = quickslots.key_origins(frame, scale)
        counts = self._read_counts(frame, scale, origins)
        self._scans.append((now, counts))
        self._scans = [s for s in self._scans if now - s[0] <= _HISTORY_S]
        out = []
        for name in list(self._pending):
            p = self._pending[name]
            if p["base"] is None:   # the pickup came before any scan: use this one as base
                p["base"] = counts
                continue
            rose = [k for k, n in counts.items()
                    if k in p["base"] and n - p["base"][k] == p["qty"]]
            if len(rose) == 1:
                del self._pending[name]
                icon = self._cut_icon(frame, scale, origins[rose[0]])
                if icon is not None and self._vote(name, rose[0], icon):
                    out.append(name)
            elif now - p["t"] > PENDING_S:
                del self._pending[name]
        return out

    # -- internals ---------------------------------------------------
    def _base(self, now: float) -> dict[int, int] | None:
        """Counts of the latest scan (before the pickup), if recent enough."""
        return self._scans[-1][1] if self._scans else None

    def _vote(self, name: str, key: int, icon: np.ndarray) -> bool:
        votes = self._votes.setdefault(name, [])
        agree = [v for v in votes if v[0] == key and _similar(v[1], icon)]
        votes.append((key, icon))
        if len(agree) + 1 < CONFIRM:
            return False
        self._votes.pop(name, None)
        return self._save(name, icon)

    def _save(self, name: str, icon: np.ndarray) -> bool:
        if self._dir is None:
            return False
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{name}.png"
            if not cv2.imwrite(str(path), icon):
                raise OSError(f"could not write {path}")
        except OSError as e:
            log.warning("could not save learned icon %s: %s", name, e)
            return False
        log.info("learned quickslot icon: %s", name)
        self.learned.append(name)
        quickslots.reload_icons()
        return True

    @staticmethod
    def _default_counts(frame, scale, origins):
        return quickslots.read_key_counts(frame, scale, origins)

    @staticmethod
    def _default_icon(frame, scale, origin):
        return quickslots.key_icon(frame, scale, origin)


def _similar(a: np.ndarray, b: np.ndarray) -> bool:
    if a.shape != b.shape:
        return False
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY) if a.ndim == 3 else a
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY) if b.ndim == 3 else b
    return float(cv2.matchTemplate(ga, gb, cv2.TM_CCOEFF_NORMED)[0, 0]) >= ICON_AGREE
