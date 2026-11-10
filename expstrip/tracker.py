"""EXP math: gains, active time with idle handling, pause, and map visits."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable

from expstrip.mesos import MesoDelta
from expstrip.reading import MapName, Reading

_RATE_MIN_ACTIVE = 60.0
_LAST_WINDOW = 300.0     # "last 5 min" (wall clock) and the EXP/h pace window (active time)
_UNKNOWN_MAP = MapName("", "Unknown map")


@dataclass
class Visit:
    map: MapName
    start: float
    end: float
    active_seconds: float
    exp_gained: int
    level_start: int
    level_end: int
    mesos_picked: int | None = None
    potion_cost: int | None = None
    meso_correction: int | None = None
    meso_other: int | None = None
    reconciliations: int | None = None
    loot_value: int | None = None
    found_potions_value: int | None = None

    @property
    def mesos_net(self) -> int | None:
        if self.mesos_picked is None:
            return None
        return (self.mesos_picked - (self.potion_cost or 0) + (self.meso_correction or 0)
                + (self.loot_value or 0) + (self.found_potions_value or 0))


@dataclass
class Stats:
    level: int | None
    pct: float | None
    map: MapName | None
    exp_per_hour: float | None
    level_up_in: float | None
    last5: int
    session: int
    paused: bool
    mesos_net: int = 0
    mesos_picked: int = 0
    potion_cost: int = 0
    mesos_per_hour: float | None = None


class Tracker:
    def __init__(self, now: float, on_visit_closed: Callable[[Visit], None],
                 idle_after: float = 120, min_visit: float = 60) -> None:
        self._on_closed = on_visit_closed
        self._idle_after = idle_after
        self._min_visit = min_visit
        self._paused = False
        self._fresh = True            # next reading starts a fresh time interval
        self._rebaseline = False      # next reading also re-baselines EXP (after pause)
        self._last_t: float | None = None
        self._level: int | None = None
        self._exp = 0
        self._pct: float | None = None
        self._map: MapName | None = None
        self._req: dict[int, int] = {}
        self._pending = 0.0
        self._session_active = 0.0
        self._session_gain = 0
        self._gains: deque[tuple[float, int]] = deque()
        # (session active seconds when gained, gain): EXP/h is the pace over the last 5 active
        # minutes, so time away (idle, unreadable, paused) neither counts nor ages the window.
        self._paced: deque[tuple[float, int]] = deque()
        self._visit_clock0 = 0.0      # session active seconds when the current visit opened
        self._visit: Visit | None = None
        self._meso_picked = 0
        self._meso_cost = 0
        self._meso_corr = 0
        self._meso_loot = 0
        self._meso_found = 0

    # -- public API --------------------------------------------------
    def update(self, reading: Reading | None, now: float) -> None:
        if self._paused:
            return
        if reading is None:
            self._fresh = True
            return
        if reading.pct >= 1.0:
            self._req[reading.level] = round(reading.exp / (reading.pct / 100))

        if self._level is None or self._rebaseline:
            gain, dt = 0, 0.0
            self._rebaseline = False
        else:
            gain = self._gain(reading)
            dt = 0.0 if self._fresh or self._last_t is None else max(0.0, now - self._last_t)
        changed = self._level is not None and (reading.exp != self._exp or reading.level != self._level)

        self._pending += dt
        if changed:
            self._commit_pending()
        self._fresh = False
        self._last_t = now
        self._level, self._exp, self._pct = reading.level, reading.exp, reading.pct

        if gain:
            self._session_gain += gain
            self._gains.append((now, gain))
            self._paced.append((self._session_active, gain))
            if self._visit is not None:
                self._visit.exp_gained += gain
        while self._gains and self._gains[0][0] <= now - _LAST_WINDOW:
            self._gains.popleft()
        while self._paced and self._paced[0][0] <= self._session_active - _LAST_WINDOW:
            self._paced.popleft()
        if self._visit is not None:
            self._visit.level_end = reading.level

        if reading.map is None and self._visit is None:
            self._visit = self._new_visit(_UNKNOWN_MAP, now, reading.level)
            self._visit_clock0 = self._session_active
        if reading.map is not None:
            if self._visit is not None and self._visit.map != reading.map:
                self._close_visit(now)
            if self._visit is None:
                self._visit = self._new_visit(reading.map, now, reading.level)
                self._visit_clock0 = self._session_active
            self._map = reading.map

    def pause(self, now: float) -> None:
        self._paused = True

    def resume(self, now: float) -> None:
        self._paused = False
        self._fresh = True
        self._rebaseline = True

    def close(self, now: float) -> None:
        self._close_visit(now)

    def add_mesos(self, d: MesoDelta, now: float) -> None:
        if self._paused:
            return
        self._meso_picked += d.picked
        self._meso_cost += d.potion_cost
        self._meso_corr += d.correction
        self._meso_loot += d.loot
        self._meso_found += d.found_potions
        v = self._visit
        if v is not None:
            v.mesos_picked = (v.mesos_picked or 0) + d.picked
            v.potion_cost = (v.potion_cost or 0) + d.potion_cost
            v.meso_correction = (v.meso_correction or 0) + d.correction
            v.meso_other = (v.meso_other or 0) + d.other
            v.loot_value = (v.loot_value or 0) + d.loot
            v.found_potions_value = (v.found_potions_value or 0) + d.found_potions
            if d.reconciled:
                v.reconciliations = (v.reconciliations or 0) + 1

    def reset(self, now: float) -> None:
        self._close_visit(now)          # commits pending idle time, applies the min-visit rule
        self._session_active = 0.0
        self._session_gain = 0
        self._gains.clear()
        self._paced.clear()
        self._meso_picked = self._meso_cost = self._meso_corr = 0
        self._meso_loot = self._meso_found = 0
        self._fresh = True              # the next reading starts a new interval
        self._pending = 0.0
        self._visit_clock0 = 0.0
        if self._map is not None and self._level is not None:
            self._visit = self._new_visit(self._map, now, self._level)

    def _pace(self, clock: float, since: float) -> float | None:
        """EXP/hour over the last 5 active minutes after `since` (session active seconds),
        or over all of them when fewer; None under _RATE_MIN_ACTIVE."""
        span = min(_LAST_WINDOW, clock - since)
        if span < _RATE_MIN_ACTIVE:
            return None
        start = clock - span
        return sum(g for c, g in self._paced if c > start) / span * 3600

    def stats(self, now: float) -> Stats:
        pend = self._pending if self._pending <= self._idle_after else 0.0
        clock = self._session_active + pend
        vis_rate = self._pace(clock, self._visit_clock0) if self._visit is not None else None
        rate = vis_rate if vis_rate is not None else self._pace(clock, 0.0)

        level_up = None
        req = self._req.get(self._level) if self._level is not None else None
        if rate is not None and rate > 0 and req is not None:
            level_up = max(0.0, (req - self._exp) / (rate / 3600))
        mph = None
        if self._visit is not None and self._visit.mesos_net is not None:
            va = self._visit.active_seconds + pend
            if va >= _RATE_MIN_ACTIVE:
                mph = self._visit.mesos_net / va * 3600
        last5 = sum(g for t, g in self._gains if now - _LAST_WINDOW < t <= now)
        return Stats(self._level, self._pct, self._map, vis_rate, level_up,
                     last5, self._session_gain, self._paused,
                     self._meso_picked - self._meso_cost + self._meso_corr
                     + self._meso_loot + self._meso_found,
                     self._meso_picked, self._meso_cost, mph)

    def current_visit_snapshot(self, now: float) -> Visit | None:
        if self._visit is None:
            return None
        v = self._visit
        pend = self._pending if self._pending <= self._idle_after else 0.0
        return Visit(v.map, v.start, now, v.active_seconds + pend, v.exp_gained,
                     v.level_start, v.level_end, v.mesos_picked, v.potion_cost,
                     v.meso_correction, v.meso_other, v.reconciliations,
                     v.loot_value, v.found_potions_value)

    # -- internals ---------------------------------------------------
    @staticmethod
    def _new_visit(map_: MapName, now: float, level: int) -> Visit:
        return Visit(map_, now, now, 0.0, 0, level, level, 0, 0, 0, 0, 0, 0, 0)

    def _gain(self, rd: Reading) -> int:
        if rd.level == self._level:
            return rd.exp - self._exp
        if rd.level == self._level + 1:
            req_old = self._req.get(self._level)
            if self._pct is None or rd.pct >= self._pct:
                return 0        # a real level-up drops the pct; otherwise a misread level
            return (req_old - self._exp) + rd.exp if req_old is not None else rd.exp
        return 0

    def _commit_pending(self) -> None:
        if self._pending <= self._idle_after:
            self._session_active += self._pending
            if self._visit is not None:
                self._visit.active_seconds += self._pending
        self._pending = 0.0

    def _close_visit(self, now: float) -> None:
        v = self._visit
        if v is None:
            return
        self._commit_pending()
        self._visit = None
        v.end = now
        if v.active_seconds >= self._min_visit:
            self._on_closed(v)
