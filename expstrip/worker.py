"""Engine: one capture -> read -> validate -> track -> persist iteration, plus a 1 Hz thread."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np

from PySide6.QtCore import QThread, Signal

from expstrip.capture import grab as _grab, is_blank
from expstrip.config import Settings
from expstrip.frame_reader import FrameReader
from expstrip.history import SpotSummary, VisitStore
from expstrip.loot_tally import load_tally_state, save_tally
from expstrip.mesos import MesoDelta, MesoLedger
from expstrip.paths import data_dir
from expstrip.prices import Prices, load_prices, load_prices_checked
from expstrip.reading import ReadingValidator
from expstrip.share import read_zones, to_screen
from expstrip.tracker import Stats, Tracker, Visit
from expstrip.vision.items import ItemLine, read_item_lines
from expstrip.vision.inventory import MesosConfirmer, read_inventory_mesos
from expstrip.vision.pickups import PickupCounter, pickup_rect, read_pickup_lines, region_changed
from expstrip.vision.quickslots import CountConfirmer, read_potion_counts
from expstrip.win.window import find_game_window

log = logging.getLogger(__name__)

_UNREADABLE_AFTER = 10
_FLUSH_EVERY = 60
_QUICKSLOTS_EVERY = 5   # ticks between quickslot reads (and prices-file mtime checks)


class StatusKind(Enum):
    OK = "ok"
    WAITING = "waiting"
    UNREADABLE = "unreadable"
    NOT_VISIBLE = "not_visible"
    PAUSED = "paused"


@dataclass
class Snapshot:
    status: StatusKind
    stats: Stats
    summaries: list[SpotSummary]


class PickupReader:
    """New pickups in a frame: crop the pickup region, skip it if unchanged since the last
    frame that was read, else read the meso and item lines and count each line once.

    Calling it returns the new meso amounts; `items` holds the ItemLines new in the last call
    (emptied at each call). `names` gives the item names to read (none: meso lines only).
    """

    def __init__(self, names: Callable[[], list[str]] | None = None, fallback=None) -> None:
        self._counter = PickupCounter()
        self._prev: np.ndarray | None = None
        self._names = names
        self._fallback = fallback  # reads names without a template (item_names.NameLearner)
        self.items: list[ItemLine] = []

    def reset(self) -> None:
        self._counter.reset()
        self._prev = None
        self.items = []

    def __call__(self, frame: np.ndarray, scale: float) -> list[int]:
        self.items = []
        r = pickup_rect(frame, scale)
        if r is None:
            return []
        region = r.crop(frame)
        if not region_changed(self._prev, region):
            return []
        lines = [(("m", amount), y) for amount, y in read_pickup_lines(region, scale)]
        names = self._names() if self._names else []
        if names:
            lines += [(("i", t[0]), t[1], t[2]) for t in read_item_lines(region, scale, names, with_level=True,
                                                                      fallback=self._fallback)]
        new = self._counter.feed(lines)
        self._prev = region.copy()  # only updated when a region is read (as tools/replay_pickups.py)
        self.items = [k[1] for k in new if k[0] == "i"]
        return [k[1] for k in new if k[0] == "m"]


@dataclass
class MesoReaders:
    """The meso readers the Engine calls; tests inject fakes.

    pickups(frame, scale) -> new amounts this tick (an optional reset() is called on
    resume/reset); potions(frame, scale, names) -> raw counts; inventory(frame, scale) ->
    raw total or None. The Engine applies the confirmers to the raw reads. icons (optional):
    an icon_learner.IconLearner that learns new potions' quickslot icons."""
    pickups: Callable[[np.ndarray, float], list[int]]
    potions: Callable[[np.ndarray, float, list[str]], dict[str, int]]
    inventory: Callable[[np.ndarray, float], int | None]
    icons: object | None = None


def default_meso_readers(names: Callable[[], list[str]] | None = None,
                         learned_dir: Path | None = None) -> MesoReaders:
    """learned_dir: where learned templates are kept (data/glyphs): item names learned by OCR
    in items/, potion quickslot icons in potions/."""
    from expstrip.icon_learner import IconLearner
    from expstrip.vision.item_names import NameLearner
    root = Path(learned_dir) if learned_dir is not None else None
    return MesoReaders(PickupReader(names, NameLearner(root / "items" if root else None)),
                       read_potion_counts, read_inventory_mesos,
                       IconLearner(root / "potions" if root else None))


class Engine:
    """Capture/read/track/persist pipeline.

    tick() runs on the worker thread; pause/resume/shutdown are called from the UI
    thread. All four hold one lock. shutdown() should still be called after the
    worker thread has stopped.
    """

    def __init__(self, settings: Settings, store: VisitStore,
                 clock: Callable[[], float] = time.time,
                 find=find_game_window, grab=_grab,
                 reader: FrameReader | None = None,
                 prices_path: Path | None = None,
                 meso_readers: MesoReaders | None = None) -> None:
        self._settings = settings
        self._store = store
        self._clock = clock
        self._find = find
        self._grab = grab
        self._warned: set[str] = set()
        self._pending: list[Visit] = []
        self._lock = threading.RLock()
        self._summaries: list[SpotSummary] = []
        self._summaries = self._load_summaries()
        if reader is None:
            reader = FrameReader(known_maps=[s.map_full for s in self._summaries],
                                 level_hint=settings.level_hint)
        self._reader = reader
        self._validator = ReadingValidator()
        self._tracker = Tracker(clock(), self._on_visit_closed)
        self._paused = False
        self._ticks = 0
        self._bad = 0
        self._status = StatusKind.WAITING
        self._prices_path = Path(prices_path) if prices_path is not None else data_dir() / "prices.json"
        self._prices: Prices = load_prices(self._prices_path)
        self._prices_mtime = self._mtime()
        self._item_names: list[str] = self._build_names()
        self._meso = (meso_readers if meso_readers is not None
                      else default_meso_readers(lambda: self._item_names,
                                                self._prices_path.parent / "glyphs"))
        self._tally_path = self._prices_path.parent / "loot_tally.json"
        self._ledger = MesoLedger(self._prices.potions, self._prices.trash)
        tally, last_exact = load_tally_state(self._tally_path)
        self._ledger.unsold_trash = tally
        self._ledger.carried_exact = last_exact
        self._saved_tally = (self._ledger.unsold_trash, self._ledger.exact_reference)
        self._tracked: set[str] = set()
        self._counts = CountConfirmer()
        self._exact = MesosConfirmer()
        self._zones: tuple = ()   # screen rects read from, last frame; swapped whole (UI thread reads it)

    # -- public ------------------------------------------------------
    def tick(self) -> Snapshot:
        with self._lock:
            now = self._clock()
            try:
                self._tick_body(now)
            except Exception as e:  # never let the loop die
                self._warn_once(f"tick failed: {type(e).__name__}: {e}")
                self._safe(lambda: self._tracker.update(None, now))
                self._count_bad()
            try:
                stats = self._tracker.stats(now)
                if self._ticks % _FLUSH_EVERY == 0 and self._status is StatusKind.OK:
                    self._safe(lambda: self._log_exp(stats, now))
                return Snapshot(self._status, stats, list(self._summaries))
            except Exception as e:
                self._warn_once(f"stats failed: {type(e).__name__}: {e}")
                return Snapshot(self._status,
                                Stats(level=None, pct=None, map=None, exp_per_hour=None,
                                      level_up_in=None, last5=0, session=0,
                                      paused=self._paused),
                                list(self._summaries))

    def read_zones(self) -> tuple:
        """(name, Rect) screen rects of the game areas read on the last frame; () when no game
        was captured. Safe to call from the UI thread without the lock."""
        return self._zones

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            self._status = StatusKind.PAUSED
            self._safe(lambda: self._tracker.pause(self._clock()))

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._bad = 0
            self._safe(lambda: self._tracker.resume(self._clock()))
            self._restart_meso()

    def reset(self) -> None:
        """Start a new session: close the current visit and zero session EXP and mesos."""
        with self._lock:
            now = self._clock()
            self._safe(lambda: self._tracker.reset(now))
            self._restart_meso(keep_exact=True)
            # write now, so a crash right after the reset can't re-append the closed visit
            self._safe(lambda: self._write_current(now))

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown()

    def _shutdown(self) -> None:
        now = self._clock()
        self._safe(lambda: self._tracker.close(now))
        self._safe(self._retry_pending)
        try:
            if self._pending:
                # still locked: stash the newest so recover_in_progress picks it up next start
                self._store.write_in_progress(self._pending[-1])
            else:
                self._store.write_in_progress(None)
        except Exception as e:
            self._warn_once(f"shutdown flush failed: {type(e).__name__}: {e}")

    # -- internals ---------------------------------------------------
    def _tick_body(self, now: float) -> None:
        self._ticks += 1
        if self._ticks % _FLUSH_EVERY == 0:
            self._safe(lambda: self._flush(now))
        if self._paused:
            self._status = StatusKind.PAUSED
            return
        win = self._find(self._settings.window_title)
        if win is None:
            self._zones = ()
            self._status = StatusKind.WAITING
            self._bad = 0
            self._tracker.update(None, now)
            return
        frame = self._grab(win)
        if is_blank(frame):
            self._zones = ()
            self._status = StatusKind.NOT_VISIBLE
            self._bad = 0
            self._tracker.update(None, now)
            return
        level, ep, map_name = self._reader.read(frame)
        self._safe(lambda: self._update_zones(win, frame))
        reading = self._validator.feed(level, ep, map_name)
        if reading is None:
            # A single unconfirmed frame (e.g. EXP just changed and the validator
            # wants a second agreeing frame) must not cut the timing interval, or
            # the active time shrinks and EXP/hour is inflated. Only a sustained
            # unreadable streak breaks the interval.
            self._count_bad()
            if self._bad >= _UNREADABLE_AFTER:
                self._tracker.update(None, now)
        else:
            self._tracker.update(reading, now)
            self._bad = 0
            self._status = StatusKind.OK
        self._meso_tick(frame, now)

    def _update_zones(self, win, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        zones = read_zones(w, h, getattr(self._reader, "regions", None))
        self._zones = tuple(to_screen(zones, win.left, win.top))

    def _meso_tick(self, frame: np.ndarray, now: float) -> None:
        """Run the meso readers on this frame; each failure is contained (EXP keeps working)."""
        scale = getattr(self._reader, "scale", None)
        if scale is None:
            return
        self._safe(lambda: self._read_pickups(frame, scale, now))
        if (self._ticks - 1) % _QUICKSLOTS_EVERY == 0:
            self._safe(self._maybe_reload_prices)
            self._safe(lambda: self._read_potions(frame, scale, now))
        self._safe(lambda: self._read_inventory(frame, scale, now))
        self._save_tally_if_changed()

    def _read_pickups(self, frame: np.ndarray, scale: float, now: float) -> None:
        self._add(self._ledger.on_pickups(self._meso.pickups(frame, scale)), now)
        items = getattr(self._meso.pickups, "items", None)
        if items:
            self._add(self._ledger.on_items(list(items), set(self._tracked)), now)
            if self._meso.icons is not None:
                self._meso.icons.on_items(list(items), list(self._prices.potions), now)

    def _save_tally_if_changed(self) -> None:
        v = (self._ledger.unsold_trash, self._ledger.exact_reference)
        if v == self._saved_tally:
            return

        def write() -> None:
            save_tally(self._tally_path, v[0], v[1])
            self._saved_tally = v   # only after a successful write, so a failure retries next tick
        self._safe(write)

    def _build_names(self) -> list[str]:
        return list(self._prices.trash) + [n for n in self._prices.potions if n not in self._prices.trash]

    def _read_potions(self, frame: np.ndarray, scale: float, now: float) -> None:
        if self._meso.icons is not None:
            self._safe(lambda: self._meso.icons.on_scan(frame, scale, now))
        raw = self._meso.potions(frame, scale, list(self._prices.potions))
        counts = self._counts.feed(raw)
        if raw:
            self._tracked = set(raw)   # on a key = in the raw read, even mid-confirmation
        if counts:
            self._add(self._ledger.on_potion_counts(counts), now)

    def _read_inventory(self, frame: np.ndarray, scale: float, now: float) -> None:
        exact = self._exact.feed(self._meso.inventory(frame, scale))
        if exact is None:
            return
        d = self._ledger.on_exact_mesos(exact)
        if d.reconciled:
            diff = d.correction + d.other
            log.info("meso reconciliation: expected %d, actual %d, difference %+d -> %s",
                     exact - diff, exact, diff, "other" if d.other else "correction")
        self._add(d, now)

    def _add(self, d: MesoDelta, now: float) -> None:
        self._tracker.add_mesos(d, now)

    def _restart_meso(self, keep_exact: bool = False) -> None:
        self._tracked = set()
        self._safe(lambda: self._ledger.restart(keep_exact))
        self._safe(self._counts.reset)
        self._safe(self._exact.reset)
        reset = getattr(self._meso.pickups, "reset", None)
        if reset is not None:
            self._safe(reset)
        self._save_tally_if_changed()

    def _mtime(self) -> int | None:
        try:
            return self._prices_path.stat().st_mtime_ns
        except OSError:
            return None

    def _maybe_reload_prices(self) -> None:
        m = self._mtime()
        if m is None or m == self._prices_mtime:
            return
        self._prices, ok = load_prices_checked(self._prices_path, self._prices)
        if ok:
            self._prices_mtime = m   # a failed read is retried on the next tick
        self._ledger.set_prices(self._prices.potions, self._prices.trash)
        self._item_names = self._build_names()
        log.info("prices reloaded from %s", self._prices_path)

    def _count_bad(self) -> None:
        self._bad += 1
        self._status = (StatusKind.UNREADABLE if self._bad >= _UNREADABLE_AFTER
                        else StatusKind.OK)

    def _log_exp(self, st: Stats, now: float) -> None:
        """One line a minute so the EXP/h pace can be checked afterwards (the strip itself
        is hidden from screenshots)."""
        v = self._tracker.current_visit_snapshot(now)
        rate = f"{st.exp_per_hour:.0f}/h" if st.exp_per_hour is not None else "-"
        visit = f"{v.exp_gained} in {v.active_seconds:.0f}s active" if v is not None else "-"
        pct = f"{st.pct:.2f}%" if st.pct is not None else "?"
        log.info("exp: map %s | level %s %s | 5m %d | rate %s | visit %s | session %d",
                 st.map.full if st.map is not None else "?", st.level, pct, st.last5, rate,
                 visit, st.session)

    def _flush(self, now: float) -> None:
        self._retry_pending()
        self._write_current(now)

    def recover_in_progress(self, min_visit: float = 60) -> None:
        """Startup: hand a saved in-progress visit to the pending-retry path.

        current_visit.json stays (via _write_current's stash) until the visit is in the CSV.
        Never raises.
        """
        try:
            with self._lock:
                v = self._store.load_in_progress()
                if v is None:
                    return
                if v.active_seconds >= min_visit:
                    self._pending.append(v)
                    self._retry_pending()
                    self._write_current(self._clock())
                else:
                    self._store.write_in_progress(None)
        except Exception as e:
            self._warn_once(f"startup recovery failed: {type(e).__name__}: {e}")

    def _on_visit_closed(self, v: Visit) -> None:
        self._pending.append(v)
        self._retry_pending()
        # replace the closed visit's in-progress copy now, so recovery can't append it again
        self._safe(lambda: self._write_current(v.end))

    def _write_current(self, now: float) -> None:
        if self._pending:  # not in visits.csv yet: keep it recoverable
            self._store.write_in_progress(self._pending[-1])
        else:
            self._store.write_in_progress(self._tracker.current_visit_snapshot(now))

    def _retry_pending(self) -> None:
        if not self._pending:
            return
        appended = False
        try:
            for v in list(self._pending):
                try:
                    self._store.append(v)
                except OSError as e:  # includes PermissionError (file open in Excel)
                    self._warn_once(f"could not append visit, will retry: {e}")
                    continue
                self._pending.remove(v)  # never re-append a visit that was written
                appended = True
        finally:
            if appended:
                self._summaries = self._load_summaries()

    def _load_summaries(self) -> list[SpotSummary]:
        try:
            return self._store.summaries()
        except Exception as e:
            self._warn_once(f"summaries failed: {type(e).__name__}: {e}")
            return list(self._summaries)

    def _safe(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:
            self._warn_once(f"{type(e).__name__}: {e}")

    def _warn_once(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            log.warning(msg)


class EngineThread(QThread):
    snapshot_ready = Signal(object)

    def __init__(self, engine: Engine, interval_ms: int = 1000, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._interval_ms = interval_ms

    @property
    def engine(self) -> Engine:
        return self._engine

    def run(self) -> None:
        while not self.isInterruptionRequested():
            started = time.monotonic()
            try:
                self.snapshot_ready.emit(self._engine.tick())
            except Exception:
                log.warning("engine tick raised", exc_info=True)
            remaining = self._interval_ms - int((time.monotonic() - started) * 1000)
            while remaining > 0 and not self.isInterruptionRequested():
                step = min(100, remaining)
                self.msleep(step)
                remaining -= step
