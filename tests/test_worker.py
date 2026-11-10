import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from expstrip.config import Settings
from expstrip.history import VisitStore
from expstrip.prices import DEFAULT_PRICES
from expstrip.reading import MapName
from expstrip.tracker import Visit
from expstrip.worker import Engine, EngineThread, StatusKind

BLANK = np.zeros((50, 50, 3), dtype=np.uint8)
LIVE = np.full((50, 50, 3), 128, dtype=np.uint8)
MAP_A = MapName("R", "A")


class FakeClock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        v = self.t
        self.t += 1
        return v


class FakeReader:
    def __init__(self, reads, scale=None):
        self._it = iter(reads)
        self._last = (None, None, None)
        self.scale = scale

    def read(self, frame):
        self._last = next(self._it, self._last)
        return self._last


def steady_gain(map_name, seconds, step_ticks=10):
    """+10 EXP/s at level 44; exp changes every step_ticks ticks so the validator accepts."""
    out = []
    for i in range(seconds):
        exp = 100_000 + (i // step_ticks) * 10 * step_ticks
        out.append((44, (exp, round(exp / 10_000, 2)), map_name))
    return out


def make_engine(tmp_path, window=object(), frame=LIVE, reads=(), store=None, meso=None,
                prices_path=None):
    return Engine(Settings(), store or VisitStore(tmp_path), clock=FakeClock(),
                  find=lambda title: window, grab=lambda w: frame,
                  reader=FakeReader(reads, scale=1.0 if meso is not None else None),
                  prices_path=prices_path or tmp_path / "prices.json",
                  meso_readers=meso.readers() if meso is not None else None)


def test_waiting_when_no_window(tmp_path):
    assert make_engine(tmp_path, window=None).tick().status is StatusKind.WAITING


def test_not_visible_on_blank(tmp_path):
    assert make_engine(tmp_path, frame=BLANK).tick().status is StatusKind.NOT_VISIBLE


def test_unreadable_after_10_bad_ticks(tmp_path):
    e = make_engine(tmp_path, reads=[(None, None, None)] * 10)
    st = [e.tick().status for _ in range(10)]
    assert st[8] is StatusKind.OK and st[-1] is StatusKind.UNREADABLE


def test_paused_overrides(tmp_path):
    e = make_engine(tmp_path, window=None)
    e.pause()
    assert e.tick().status is StatusKind.PAUSED
    assert e.tick().stats.paused
    e.resume()
    assert e.tick().status is StatusKind.WAITING


def test_visit_persisted_on_shutdown(tmp_path):
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 120))
    for _ in range(120):
        e.tick()
    e.shutdown()
    assert len(VisitStore(tmp_path).load()) == 1
    assert not (tmp_path / "current_visit.json").exists()


def test_in_progress_flushed_every_60_ticks(tmp_path):
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 61))
    for _ in range(61):
        e.tick()
    assert (tmp_path / "current_visit.json").exists()


def test_tick_never_raises(tmp_path):
    def boom(w):
        raise RuntimeError("x")
    e = Engine(Settings(), VisitStore(tmp_path), clock=FakeClock(),
               prices_path=tmp_path / "prices.json",
               find=lambda t: object(), grab=boom, reader=FakeReader([]))
    assert e.tick() is not None
    assert e.tick() is not None


class FlakyStore(VisitStore):
    def __init__(self, d):
        super().__init__(d)
        self.fail = True

    def append(self, v):
        if self.fail:
            self.fail = False
            raise PermissionError("excel")
        super().append(v)


def test_excel_lock_visit_retained_and_retried(tmp_path):
    store = FlakyStore(tmp_path)
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 120), store=store)
    for _ in range(120):
        e.tick()
    e.shutdown()
    assert store.fail is False
    assert len(VisitStore(tmp_path).load()) == 1


def test_excel_lock_persists_at_shutdown_via_in_progress(tmp_path):
    class Locked(VisitStore):
        def append(self, v):
            raise PermissionError("excel")
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 120), store=Locked(tmp_path))
    for _ in range(120):
        e.tick()
    e.shutdown()
    assert (tmp_path / "current_visit.json").exists()
    VisitStore(tmp_path).recover_in_progress()
    assert len(VisitStore(tmp_path).load()) == 1


def test_known_maps_passed_to_reader(tmp_path, monkeypatch):
    import expstrip.worker as w
    VisitStore(tmp_path).append(Visit(MAP_A, 1_700_000_000, 1_700_000_100, 100, 500, 44, 44))
    seen = {}

    class R:
        def __init__(self, **kw):
            seen.update(kw)
    monkeypatch.setattr(w, "FrameReader", R)
    Engine(Settings(level_hint=44), VisitStore(tmp_path), prices_path=tmp_path / "prices.json")
    assert list(seen["known_maps"]) == ["R : A"] and seen["level_hint"] == 44


def test_engine_thread_start_stop(tmp_path):
    from PySide6.QtCore import QCoreApplication, Qt
    QCoreApplication.instance() or QCoreApplication([])
    e = make_engine(tmp_path, window=None)
    t = EngineThread(e, interval_ms=50)
    got = []
    t.snapshot_ready.connect(got.append, Qt.DirectConnection)
    t.start()
    time.sleep(0.3)
    t.requestInterruption()
    assert t.wait(2000)
    assert got


def test_pause_during_tick_ends_paused(tmp_path):
    import threading
    entered, release = threading.Event(), threading.Event()

    class BlockingReader:
        def read(self, frame):
            entered.set()
            release.wait(5)
            return (None, None, None)

    e = Engine(Settings(), VisitStore(tmp_path), clock=FakeClock(),
               prices_path=tmp_path / "prices.json",
               find=lambda t: object(), grab=lambda w: LIVE, reader=BlockingReader())
    th = threading.Thread(target=e.tick)
    th.start()
    assert entered.wait(5)
    p = threading.Thread(target=e.pause)
    p.start()
    release.set()
    th.join(5)
    p.join(5)
    assert e.tick().status is StatusKind.PAUSED


def test_failing_grab_becomes_unreadable_and_no_time_accrues(tmp_path):
    def boom(w):
        raise RuntimeError("x")
    e = Engine(Settings(), VisitStore(tmp_path), clock=FakeClock(),
               prices_path=tmp_path / "prices.json",
               find=lambda t: object(), grab=boom, reader=FakeReader([]))
    st = [e.tick() for _ in range(10)]
    assert st[8].status is StatusKind.OK and st[-1].status is StatusKind.UNREADABLE
    assert st[-1].stats.exp_per_hour is None and st[-1].stats.session == 0


def test_failing_reader_becomes_unreadable(tmp_path):
    class Bad:
        def read(self, f):
            raise ValueError("y")
    e = Engine(Settings(), VisitStore(tmp_path), clock=FakeClock(),
               prices_path=tmp_path / "prices.json",
               find=lambda t: object(), grab=lambda w: LIVE, reader=Bad())
    assert [e.tick().status for _ in range(10)][-1] is StatusKind.UNREADABLE


def test_retry_does_not_duplicate_appended_visits(tmp_path):
    class Store(VisitStore):
        def __init__(self, d):
            super().__init__(d)
            self.calls = 0
            self.good = []

        def append(self, v):
            self.calls += 1
            if v.exp_gained == 2 and self.calls <= 2:
                raise ValueError("weird")
            self.good.append(v)

    st = Store(tmp_path)
    e = make_engine(tmp_path, store=st)
    v1 = Visit(MAP_A, 1, 100, 100, 1, 44, 44)
    v2 = Visit(MAP_A, 1, 100, 100, 2, 44, 44)
    e._pending = [v1, v2]
    try:
        e._retry_pending()
    except ValueError:
        pass
    e._retry_pending()
    assert [v.exp_gained for v in st.good] == [1, 2]


def test_waiting_and_not_visible_beat_unreadable(tmp_path):
    win = [object()]
    frame = [LIVE]
    e = Engine(Settings(), VisitStore(tmp_path), clock=FakeClock(),
               prices_path=tmp_path / "prices.json",
               find=lambda t: win[0], grab=lambda w: frame[0],
               reader=FakeReader([(None, None, None)] * 30))
    for _ in range(10):
        e.tick()
    assert e.tick().status is StatusKind.UNREADABLE
    frame[0] = BLANK
    assert e.tick().status is StatusKind.NOT_VISIBLE
    frame[0] = LIVE
    for _ in range(10):
        e.tick()
    win[0] = None
    assert e.tick().status is StatusKind.WAITING


def test_rate_counts_time_when_exp_changes_every_few_seconds(tmp_path):
    """Regression: an unconfirmed frame (validator needs 2 equal frames) must not
    drop the elapsed time. EXP +30 every 3 s = 36,000/h; before the fix the
    tracker counted only ~1/3 of the time and showed ~3x the real rate."""
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 400, step_ticks=3))
    snap = None
    for _ in range(400):
        snap = e.tick()
    rate = snap.stats.exp_per_hour
    assert rate is not None and 34_000 <= rate <= 38_000


# -- meso wiring ---------------------------------------------------------------------

class FakeMeso:
    """Fake meso readers keyed by tick index (the n-th pickups call is tick n)."""

    def __init__(self, pickups=None, potions=None, inventory=None, fail=False, items=None):
        self.items = items or {}
        self.pickups, self.potions, self.inventory = pickups or {}, potions or {}, inventory or {}
        self.fail = fail
        self.tick = -1
        self.potion_calls = 0
        self.names = None
        self.resets = 0

    def readers(self):
        from expstrip.worker import MesoReaders
        fm = self

        class Pick:
            items: list = []

            def __call__(self, frame, scale):
                fm.tick += 1
                self.items = list(fm.items.get(fm.tick, []))
                if fm.fail:
                    raise RuntimeError("pickup boom")
                return list(fm.pickups.get(fm.tick, []))

            def reset(self):
                fm.resets += 1

        def potions(frame, scale, names):
            fm.potion_calls += 1
            fm.names = list(names)
            if fm.fail:
                raise RuntimeError("potion boom")
            return dict(fm.potions.get(fm.tick, {}))

        def inventory(frame, scale):
            if fm.fail:
                raise RuntimeError("inventory boom")
            return fm.inventory.get(fm.tick)

        return MesoReaders(Pick(), potions, inventory)


def test_engine_counts_pickups_and_potions(tmp_path):
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 30),
                    meso=FakeMeso(pickups={3: [12], 4: [11]},
                                  potions={0: {"Meat": 10}, 5: {"Meat": 10},
                                           10: {"Meat": 9}, 15: {"Meat": 9}}))
    for _ in range(20):
        snap = e.tick()
    assert (snap.stats.mesos_picked, snap.stats.potion_cost) == (23, 80)


def _tablecloth():
    from expstrip.vision.items import ItemLine
    return [ItemLine("Tablecloth", 2, "Etc")]


def test_engine_credits_trash_line(tmp_path):
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=FakeMeso(items={1: _tablecloth()}))
    for _ in range(4):
        snap = e.tick()
    assert snap.stats.mesos_net == 68


def test_engine_tally_survives_restart(tmp_path):
    a = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=FakeMeso(items={1: _tablecloth()}))
    for _ in range(4):
        a.tick()
    a.shutdown()
    b = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=FakeMeso())
    assert b._ledger.unsold_trash == 68


def test_reconciled_sale_lowers_saved_tally(tmp_path):
    fm = FakeMeso(items={2: _tablecloth()}, inventory={0: 1000, 1: 1000, 4: 1500, 5: 1500})
    a = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=fm)
    for _ in range(7):
        a.tick()
    assert a._ledger.unsold_trash < 68
    left = a._ledger.unsold_trash
    b = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=FakeMeso())
    assert b._ledger.unsold_trash == left


def test_items_ignored_while_paused(tmp_path):
    fm = FakeMeso(items={1: _tablecloth()})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=fm)
    e.tick()
    e.pause()
    for _ in range(3):
        e.tick()
    assert e._ledger.unsold_trash == 0


def test_engine_quickslots_every_5_ticks(tmp_path):
    fm = FakeMeso()
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 20), meso=fm)
    for _ in range(20):
        e.tick()
    assert fm.potion_calls == 4
    assert fm.names == list(DEFAULT_PRICES.potions)  # keys of the default prices file


def test_engine_inventory_reconciles_with_correction(tmp_path, caplog):
    # exact 1000 confirmed at ticks 1 (rebase); pickups 50 at tick 2; exact 1040 confirmed at tick 4
    fm = FakeMeso(pickups={2: [50]}, inventory={0: 1000, 1: 1000, 3: 1040, 4: 1040})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=fm)
    import logging
    with caplog.at_level(logging.INFO, logger="expstrip"):
        for _ in range(6):
            snap = e.tick()
    assert snap.stats.mesos_picked == 50 and snap.stats.mesos_net == 40
    assert any("reconcil" in r.getMessage() and "1050" in r.getMessage()
               and "1040" in r.getMessage() for r in caplog.records)


def test_meso_skipped_without_scale_and_when_no_window(tmp_path):
    fm = FakeMeso(pickups={0: [5]})
    e = Engine(Settings(), VisitStore(tmp_path), clock=FakeClock(),
               find=lambda t: object(), grab=lambda w: LIVE, reader=FakeReader([]),
               prices_path=tmp_path / "prices.json", meso_readers=fm.readers())
    e.tick()
    assert fm.tick == -1
    e2 = make_engine(tmp_path, window=None, meso=fm)
    e2.tick()
    assert fm.tick == -1
    e3 = make_engine(tmp_path, frame=BLANK, meso=fm)
    e3.tick()
    assert fm.tick == -1


def test_meso_reader_failure_does_not_stop_exp(tmp_path):
    fm = FakeMeso(fail=True)
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 120), meso=fm)
    for _ in range(120):
        snap = e.tick()
    assert snap.status is StatusKind.OK and snap.stats.session > 0
    assert fm.potion_calls > 0


def test_meso_not_touched_while_paused_and_resume_restarts(tmp_path):
    fm = FakeMeso(pickups={0: [7], 1: [9]})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 20), meso=fm)
    e.tick()
    e.pause()
    for _ in range(3):
        e.tick()
    assert fm.tick == 0 and fm.potion_calls == 1
    e.resume()
    assert fm.resets == 1
    assert e.tick().stats.mesos_picked == 16


def test_engine_reset_zeroes_and_saves_visit(tmp_path):
    fm = FakeMeso(pickups={5: [100]})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 120), meso=fm)
    for _ in range(120):
        e.tick()
    e.reset()
    assert fm.resets == 1
    assert len(VisitStore(tmp_path).load()) == 1
    # the in-progress file already holds the fresh visit, so recovery can't re-append the old one
    import json
    cur = json.loads((tmp_path / "current_visit.json").read_text(encoding="utf-8"))
    assert cur["exp_gained"] == 0 and cur["mesos_picked"] == 0
    snap = e.tick()
    assert snap.stats.session == 0 and snap.stats.mesos_net == 0
    VisitStore(tmp_path).recover_in_progress()
    assert len(VisitStore(tmp_path).load()) == 1


def test_map_change_close_rewrites_in_progress(tmp_path):
    MAP_B = MapName("R", "B")
    a = steady_gain(MAP_A, 125)
    reads = a + [(lv, ep, MAP_B) for lv, ep, _ in a[-1:] * 5]
    e = make_engine(tmp_path, reads=reads)
    for _ in range(125):
        e.tick()
    assert (tmp_path / "current_visit.json").exists()
    for _ in range(4):
        e.tick()
    assert len(VisitStore(tmp_path).load()) == 1
    VisitStore(tmp_path).recover_in_progress()
    assert len(VisitStore(tmp_path).load()) == 1


def test_prices_reloaded_when_file_changes(tmp_path):
    import json
    pp = tmp_path / "prices.json"
    pp.write_text(json.dumps({"potions": {"Meat": 80}}), encoding="utf-8")
    fm = FakeMeso(potions={0: {"Meat": 10}, 5: {"Meat": 10}, 10: {"Meat": 10},
                           15: {"Meat": 9}, 20: {"Meat": 9}})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 30), meso=fm, prices_path=pp)
    for _ in range(12):
        e.tick()
    pp.write_text(json.dumps({"potions": {"Meat": 100}}), encoding="utf-8")
    st = pp.stat()
    os.utime(pp, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    for _ in range(10):
        snap = e.tick()
    assert snap.stats.potion_cost == 100


def test_failed_prices_load_is_retried_even_if_mtime_is_unchanged(tmp_path):
    import json
    pp = tmp_path / "prices.json"
    pp.write_text(json.dumps({"potions": {"Meat": 80}}), encoding="utf-8")
    e = make_engine(tmp_path, reads=[], prices_path=pp)
    pp.write_text("{ half written", encoding="utf-8")
    st = pp.stat()
    bumped = st.st_mtime_ns + 5_000_000_000
    os.utime(pp, ns=(st.st_atime_ns, bumped))
    e._maybe_reload_prices()
    assert e._prices.potions == {"Meat": 80}
    pp.write_text(json.dumps({"potions": {"Meat": 99}}), encoding="utf-8")
    os.utime(pp, ns=(st.st_atime_ns, bumped))     # same mtime as the failed read
    e._maybe_reload_prices()
    assert e._prices.potions == {"Meat": 99}


def _loot_frame(fix_name: str | None):
    """A client frame whose pickup region holds a loot_a fixture crop (right-aligned)."""
    import cv2
    from pathlib import Path
    from expstrip.vision.pickups import pickup_rect
    frame = np.zeros((900, 1400, 3), dtype=np.uint8)
    if fix_name:
        crop = cv2.imread(str(Path(__file__).parent / "fixtures" / "items" / fix_name))
        r = pickup_rect(frame, 1.0)
        frame[r.y:r.y + crop.shape[0], r.x + r.w - crop.shape[1]:r.x + r.w] = crop
    return frame


def test_pickup_reader_items_cleared_each_call():
    from expstrip.worker import PickupReader
    names = ["Tablecloth", "Stirge Wing", "Meat", "Lemon"]
    pr = PickupReader(lambda: names)
    pr(_loot_frame("loot_a_0021.png"), 1.0)
    assert [i.name for i in pr.items] == ["Tablecloth"]
    pr(_loot_frame("loot_a_0021.png"), 1.0)  # region unchanged: nothing new, list emptied
    assert pr.items == []


def test_pickup_reader_without_names_is_meso_only():
    from expstrip.worker import PickupReader
    pr = PickupReader()
    pr(_loot_frame("loot_a_0021.png"), 1.0)
    assert pr.items == []


def test_mixed_stack_never_double_counts():
    import json
    from collections import Counter
    from pathlib import Path
    from expstrip.vision.pickups import pickup_rect, read_pickup_lines
    from expstrip.worker import PickupReader
    fix = Path(__file__).parent / "fixtures" / "items"
    truth = json.loads((fix / "truth.json").read_text())
    pr = PickupReader(lambda: truth["names"])
    items, want_items = Counter(), Counter()
    mesos, want_mesos = 0, 0
    import cv2
    for name, lines in truth["frames"].items():
        for k in range(4):  # frames without lines (a changing patch keeps them "changed")
            empty = _loot_frame(None)
            r = pickup_rect(empty, 1.0)
            empty[r.y:r.y + 40, r.x:r.x + 40 * (k + 1)] = 255 * (k % 2 == 0)
            pr(empty, 1.0)
        frame = _loot_frame(name)
        mesos += sum(pr(frame, 1.0))
        items.update(i.name for i in pr.items)
        want_items.update(n for n, _, _ in lines)
        crop = cv2.imread(str(fix / name))
        want_mesos += sum(a for a, _ in read_pickup_lines(crop, 1.0))
    assert items == want_items
    assert mesos == want_mesos > 0


def test_reset_keeps_exact_so_sale_drains_tally(tmp_path):
    fm = FakeMeso(items={1: _tablecloth()}, inventory={0: 1000, 1: 1000, 5: 1068, 6: 1068})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=fm)
    for _ in range(3):
        e.tick()
    assert e._ledger.unsold_trash == 68
    e.reset()
    assert e._ledger.last_exact == 1000
    for _ in range(4):
        snap = e.tick()
    assert e._ledger.unsold_trash == 0
    assert snap.stats.mesos_net == 0


def test_resume_drops_exact(tmp_path):
    fm = FakeMeso(inventory={0: 1000, 1: 1000})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=fm)
    e.tick()
    e.tick()
    assert e._ledger.last_exact == 1000
    e.resume()
    assert e._ledger.last_exact is None and e._ledger.carried_exact is None


def test_last_exact_persisted_and_carried_on_start(tmp_path):
    fm = FakeMeso(items={2: _tablecloth()}, inventory={0: 1000, 1: 1000})
    a = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=fm)
    for _ in range(3):
        a.tick()
    a.shutdown()
    b = make_engine(tmp_path, reads=steady_gain(MAP_A, 10),
                    meso=FakeMeso(inventory={0: 1068, 1: 1068}))
    assert b._ledger.unsold_trash == 68 and b._ledger.carried_exact == 1000
    b.tick()
    b.tick()
    assert b._ledger.unsold_trash == 0


def test_potion_tracked_from_raw_read_even_when_count_just_changed(tmp_path):
    fm = FakeMeso(potions={0: {"Lemon": 100}, 5: {"Lemon": 99}})
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 10), meso=fm)
    for _ in range(6):
        e.tick()
    assert "Lemon" in e._tracked


# ---- final review fixes ------------------------------------------------------
class LockedStore(VisitStore):
    locked = True

    def append(self, v):
        if self.locked:
            raise PermissionError("excel")
        super().append(v)


def _stash(tmp_path, secs=900):
    VisitStore(tmp_path).write_in_progress(Visit(MapName("R", "A"), 0, secs, secs, 9000, 44, 44))


def test_startup_recovery_with_locked_csv_does_not_raise_and_retries(tmp_path):   # I1
    _stash(tmp_path)
    store = LockedStore(tmp_path)
    e = make_engine(tmp_path, store=store)
    e.recover_in_progress()
    assert (tmp_path / "current_visit.json").exists()      # still safe on disk
    assert VisitStore(tmp_path).load() == []
    store.locked = False
    e._retry_pending()
    assert len(VisitStore(tmp_path).load()) == 1
    e._retry_pending(); e.shutdown()
    assert len(VisitStore(tmp_path).load()) == 1           # never twice
    assert not (tmp_path / "current_visit.json").exists()


def test_startup_recovery_unlocked_appends_and_clears(tmp_path):                  # I1
    _stash(tmp_path)
    e = make_engine(tmp_path)
    e.recover_in_progress()
    assert len(VisitStore(tmp_path).load()) == 1
    assert not (tmp_path / "current_visit.json").exists()


def test_startup_recovery_corrupt_and_short(tmp_path):                            # I1
    (tmp_path / "current_visit.json").write_text("{bad", encoding="utf-8")
    make_engine(tmp_path).recover_in_progress()
    assert not (tmp_path / "current_visit.json").exists()
    _stash(tmp_path, secs=30)
    make_engine(tmp_path).recover_in_progress()
    assert VisitStore(tmp_path).load() == [] and not (tmp_path / "current_visit.json").exists()


def test_flush_keeps_pending_visit_in_current_file(tmp_path):                     # M2
    store = LockedStore(tmp_path)
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 5), store=store)
    e._pending.append(Visit(MapName("R", "Old"), 0, 900, 900, 9000, 44, 44))
    for _ in range(3):
        e.tick()
    e._flush(e._clock())
    d = (tmp_path / "current_visit.json").read_text(encoding="utf-8")
    assert '"Old"' in d


def test_exp_stats_logged_once_a_minute(tmp_path, caplog):
    import logging
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 130))
    with caplog.at_level(logging.INFO, logger="expstrip"):
        for _ in range(130):
            e.tick()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("exp:")]
    assert len(lines) == 2                      # ticks 60 and 120
    assert "rate -" in lines[0]                 # under a minute of active play: no rate yet
    # 1100 EXP over 118 active s (the steps land every 10 s) = 33,559/h
    assert lines[1] == ("exp: map R : A | level 44 10.11% | 5m 1100 | rate 33559/h"
                        " | visit 1100 in 118s active | session 1100")


def test_read_zones_are_screen_rects_of_last_frame(tmp_path):
    from expstrip.share import STATUS_BAR
    from expstrip.win.window import GameWindow
    e = make_engine(tmp_path, window=GameWindow(1, 100, 200, 50, 50))
    assert e.read_zones() == ()
    e.tick()
    (name, r), = e.read_zones()
    assert name == STATUS_BAR and (r.x, r.y + r.h, r.w) == (100, 250, 50)


def test_read_zones_cleared_when_game_gone(tmp_path):
    from expstrip.win.window import GameWindow
    win = [GameWindow(1, 0, 0, 50, 50)]
    e = Engine(Settings(), VisitStore(tmp_path), clock=FakeClock(), find=lambda t: win[0],
               grab=lambda w: LIVE, reader=FakeReader([]), prices_path=tmp_path / "prices.json")
    e.tick()
    assert e.read_zones()
    win[0] = None
    e.tick()
    assert e.read_zones() == ()
