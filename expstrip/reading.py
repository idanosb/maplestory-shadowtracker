"""Reading model, EXP text parsing, map-name normalization and validation."""
from __future__ import annotations

import re
from dataclasses import dataclass

_EXP_RE = re.compile(r"^\s*(\d{1,3}(?:,\d{3})*)\s*\[\s*(\d{1,3}\.\d{2})\s*%\s*\]\s*$")
_PCT_TOLERANCE = 0.02


@dataclass(frozen=True)
class MapName:
    region: str
    map: str

    @property
    def full(self) -> str:
        return f"{self.region} : {self.map}" if self.region else self.map


@dataclass(frozen=True)
class Reading:
    level: int
    exp: int
    pct: float
    map: MapName | None


def parse_exp_text(text: str) -> tuple[int, float] | None:
    m = _EXP_RE.match(text)
    if not m:
        return None
    return int(m.group(1).replace(",", "")), float(m.group(2))


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def normalize_map_name(lines: list[str]) -> MapName | None:
    cleaned = [c for c in (_clean(s) for s in lines) if c]
    if not cleaned:
        return None
    if len(cleaned) == 1:
        text = cleaned[0]
        if ":" in text:
            region, _, name = text.partition(":")
            region, name = _clean(region), _clean(name)
            if name:
                return MapName(region, name)
            return MapName("", region) if region else None
        return MapName("", text)
    return MapName(cleaned[0], cleaned[1])


class ReadingValidator:
    def __init__(self) -> None:
        self._requirements: dict[int, int] = {}
        # level -> (req_lo, req_hi): bounds implied by 2-decimal pct rounding
        self._bounds: dict[int, tuple[float, float]] = {}
        self._bad: tuple[int, int, float] | None = None
        self._bad_count = 0
        self._pending: tuple[int, int, float] | None = None
        self._accepted: tuple[int, int, float] | None = None  # last triple returned as a Reading
        self._last_map: MapName | None = None
        self._map: MapName | None = None

    def requirement(self, level: int) -> int | None:
        return self._requirements.get(level)

    def _feed_map(self, map_name: MapName | None) -> None:
        if map_name is None:
            return
        if map_name == self._last_map:
            self._map = map_name
        self._last_map = map_name

    def _consistent(self, level: int, exp: int, pct: float) -> bool:
        b = self._bounds.get(level)
        if b is None:
            return True
        req_lo, req_hi = b
        lo = exp / req_hi * 100 - _PCT_TOLERANCE
        hi = exp / req_lo * 100 + _PCT_TOLERANCE
        return lo <= pct <= hi

    def _learn(self, level: int, exp: int, pct: float) -> None:
        if pct >= 1.0:
            self._requirements[level] = round(exp / (pct / 100))
            self._bounds[level] = (exp / ((pct + 0.005) / 100),
                                   exp / ((pct - 0.005) / 100))

    def feed(self, level: int | None, exp_pct: tuple[int, float] | None,
             map_name: MapName | None) -> Reading | None:
        self._feed_map(map_name)
        if level is None or exp_pct is None:
            self._pending = None
            self._bad, self._bad_count = None, 0
            return None
        exp, pct = exp_pct
        triple = (level, exp, pct)
        if not self._consistent(level, exp, pct):
            self._pending = None
            self._bad_count = self._bad_count + 1 if triple == self._bad else 1
            self._bad = triple
            if self._bad_count < 3:
                return None
            # escape hatch: same inconsistent triple 3x in a row
            self._bad, self._bad_count = None, 0
            self._pending = triple
            self._learn(level, exp, pct)
            self._accepted = triple
            return Reading(level, exp, pct, self._map)
        self._bad, self._bad_count = None, 0
        if triple != self._pending:
            self._pending = triple
            # Fast path: while grinding, EXP can change every frame, so two equal frames in a
            # row are rare. A value whose EXP and pct agree with the learned requirement (two
            # independent glyph reads) and that went up at the same level is trusted at once.
            a = self._accepted
            if (level in self._bounds and a is not None and a[0] == level and exp >= a[1]):
                self._accepted = triple
                return Reading(level, exp, pct, self._map)
            return None
        self._learn(level, exp, pct)
        self._accepted = triple
        return Reading(level, exp, pct, self._map)
