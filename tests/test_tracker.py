import pytest

from expstrip.reading import MapName, Reading
from expstrip.tracker import Tracker


def r(level, exp, pct, map="A"):
    return Reading(level, exp, pct, MapName("", map))


def test_gain_and_rate():
    closed = []; t = Tracker(0, closed.append)
    t.update(r(44, 100000, 23.35), 0)
    for i in range(1, 121): t.update(r(44, 100000 + i * 10, 23.35), i)   # +10/s for 120 s
    s = t.stats(120)
    assert s.session == 1200 and s.exp_per_hour == pytest.approx(36000)


def test_rate_hidden_under_60s():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0), 0); t.update(r(44, 50, 0.01), 30)
    assert t.stats(30).exp_per_hour is None


def test_level_up_known_requirement():
    t = Tracker(0, lambda v: None)
    t.update(r(44, 400000, 93.38), 0)     # req ~ 428,357
    t.update(r(45, 1000, 0.2), 10)
    assert t.stats(10).session == (round(400000 / 0.9338) - 400000) + 1000


def test_level_up_unknown_requirement_not_negative():
    t = Tracker(0, lambda v: None)
    t.update(r(44, 500, 0.5), 0)          # pct < 1, requirement not learned
    t.update(r(45, 300, 0.1), 10)
    assert t.stats(10).session == 300


def test_death_counts_negative():
    t = Tracker(0, lambda v: None); t.update(r(44, 10000, 5.0), 0); t.update(r(44, 9000, 4.5), 5)
    assert t.stats(5).session == -1000


def test_idle_period_excluded():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0), 0)
    t.update(r(44, 600, 0.3), 60)          # 60 s active
    for i in range(61, 361): t.update(r(44, 600, 0.3), i)   # 300 s no change
    t.update(r(44, 1200, 0.6), 361)        # change after long idle: idle dropped
    assert t.stats(361).exp_per_hour == pytest.approx(1200 / 60 * 3600)


def test_none_readings_accrue_nothing():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0), 0); t.update(r(44, 600, 0.3), 60)
    for i in range(61, 1000): t.update(None, i)
    t.update(r(44, 1200, 0.6), 1000); t.update(r(44, 1800, 0.9), 1060)
    assert t.stats(1060).exp_per_hour == pytest.approx(1800 / 120 * 3600)


def test_pause_ignores_gains():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0), 0); t.pause(10)
    t.update(r(44, 5000, 2.0), 50); t.resume(60); t.update(r(44, 5000, 2.0), 61)
    assert t.stats(61).session == 0 and t.stats(61).paused is False


def test_map_change_closes_visit_and_drops_short():
    closed = []; t = Tracker(0, closed.append)
    t.update(r(44, 0, 0.0, "A"), 0)
    for i in range(1, 91): t.update(r(44, i * 10, 0.0, "A"), i)
    t.update(r(44, 900, 0.0, "Town"), 91)
    for i in range(92, 100): t.update(r(44, 900, 0.0, "Town"), i)
    t.update(r(44, 900, 0.0, "B"), 100)
    assert [v.map.map for v in closed] == ["A"] and closed[0].exp_gained == 900


def test_last5_window():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0), 0)
    t.update(r(44, 1000, 0.5), 100); t.update(r(44, 1500, 0.7), 500)
    assert t.stats(500).last5 == 500


def test_level_up_in_uses_rate_and_requirement():
    t = Tracker(0, lambda v: None)
    t.update(r(44, 100000, 50.0), 0)       # req learned from latest reading
    for i in range(1, 121): t.update(r(44, 100000 + i * 10, 50.0), i)
    assert t.stats(120).level_up_in == pytest.approx((round(101200 / 0.5) - 101200) / 10)  # latest requirement wins


def test_level_up_in_none_without_requirement():
    t = Tracker(0, lambda v: None)
    t.update(r(44, 0, 0.5), 0)
    for i in range(1, 121): t.update(r(44, i * 10, 0.5), i)
    assert t.stats(120).level_up_in is None


def test_close_emits_and_snapshot():
    closed = []; t = Tracker(0, closed.append)
    t.update(r(44, 0, 0.0), 0)
    for i in range(1, 91): t.update(r(44, i * 10, 0.0), i)
    snap = t.current_visit_snapshot(90)
    assert snap.exp_gained == 900 and snap.start == 0 and snap.end == 90
    t.close(90)
    assert len(closed) == 1 and closed[0].level_start == closed[0].level_end == 44
    assert t.current_visit_snapshot(91) is None


def test_none_map_keeps_visit():
    t = Tracker(0, lambda v: None)
    t.update(r(44, 0, 0.0), 0)
    t.update(Reading(44, 100, 0.0, None), 5)
    assert t.current_visit_snapshot(5).exp_gained == 100


# ---- mesos / reset -----------------------------------------------------
from expstrip.mesos import MesoDelta

MAP_A = MapName("", "A")


def make_tracker_on_map_a(on_closed=None, active_seconds=0, exp_gain=0):
    t = Tracker(0, on_closed or (lambda v: None))
    t.update(r(44, 100000, 23.35), 0)
    if active_seconds:
        steps = active_seconds
        for i in range(1, steps + 1):
            t.update(r(44, 100000 + round(exp_gain * i / steps), 23.35), i)
    return t


def test_mesos_credit_session_and_visit():
    t = make_tracker_on_map_a()
    t.add_mesos(MesoDelta(picked=500, potion_cost=160, correction=20), now=100)
    t.add_mesos(MesoDelta(other=4_000_000), now=101)
    s = t.stats(101)
    assert (s.mesos_net, s.mesos_picked, s.potion_cost) == (360, 500, 160)


def test_reset_closes_visit_and_zeroes_session():
    closed = []
    t = make_tracker_on_map_a(on_closed=closed.append, active_seconds=120, exp_gain=900)
    t.add_mesos(MesoDelta(picked=77, other=5, reconciled=True), 121)
    t.reset(now=200)
    assert len(closed) == 1 and closed[0].exp_gained == 900
    assert closed[0].mesos_picked == 77 and closed[0].meso_other == 5 and closed[0].reconciliations == 1
    s = t.stats(201)
    assert s.session == 0 and s.last5 == 0 and s.mesos_net == 0 and s.map == MAP_A
    t.update(r(44, 100900, 23.35), 202)
    assert t.stats(202).session == 0
    assert t.current_visit_snapshot(202) is not None


def test_paused_ignores_mesos():
    t = make_tracker_on_map_a(); t.pause(10)
    t.add_mesos(MesoDelta(picked=50), 11)
    assert t.stats(12).mesos_picked == 0


def test_mesos_per_hour_needs_60s():
    t = make_tracker_on_map_a(active_seconds=30, exp_gain=10)
    t.add_mesos(MesoDelta(picked=1000), 30)
    assert t.stats(30).mesos_per_hour is None
    t = make_tracker_on_map_a(active_seconds=120, exp_gain=10)
    t.add_mesos(MesoDelta(picked=1000, other=9_999_999), 120)
    assert t.stats(120).mesos_per_hour == pytest.approx(30000)


def test_mesos_without_visit_update_session_only():
    t = Tracker(0, lambda v: None)
    t.add_mesos(MesoDelta(picked=10), 1)
    assert t.stats(1).mesos_picked == 10


def test_reset_starts_a_fresh_interval():
    t = make_tracker_on_map_a(active_seconds=10, exp_gain=100)
    t.reset(now=11)
    assert t._fresh is True and t._pending == 0.0


def test_mesos_net_includes_loot():
    from expstrip.tracker import Visit
    v = Visit(MAP_A, 0, 1, 1, 0, 44, 44, mesos_picked=100, potion_cost=30,
              meso_correction=5, loot_value=34, found_potions_value=150)
    assert v.mesos_net == 259


def test_add_mesos_loot_goes_to_session_and_visit():
    t = make_tracker_on_map_a()
    t.add_mesos(MesoDelta(loot=34, found_potions=150), now=10)
    s = t.stats(10)
    assert s.mesos_net == 184
    v = t.current_visit_snapshot(10)
    assert (v.loot_value, v.found_potions_value, v.mesos_net) == (34, 150, 184)
    t.reset(20)
    assert t.stats(21).mesos_net == 0


# ---- final review fixes ------------------------------------------------------
def ru(level, exp, pct):
    return Reading(level, exp, pct, None)


def test_level_misread_with_unchanged_pct_gains_nothing():           # M1
    t = Tracker(0, lambda v: None)
    t.update(r(44, 400000, 93.38), 0)
    t.update(r(45, 400000, 93.38), 10)
    assert t.stats(10).session == 0


def test_real_level_up_still_gains_when_pct_drops():                  # M1
    t = Tracker(0, lambda v: None)
    t.update(r(44, 400000, 93.38), 0)
    t.update(r(45, 1000, 0.2), 10)
    assert t.stats(10).session == (round(400000 / 0.9338) - 400000) + 1000


def test_never_read_map_opens_unknown_visit_with_rate():               # I2
    closed = []; t = Tracker(0, closed.append)
    t.update(ru(44, 100000, 23.35), 0)
    for i in range(1, 121):
        t.update(ru(44, 100000 + i * 10, 23.35), i)
    s = t.stats(120)
    assert s.exp_per_hour == pytest.approx(36000)
    t.update(r(44, 101200, 23.35, "Real"), 121)    # real name confirmed: Unknown closes
    assert len(closed) == 1 and closed[0].map.full == "Unknown map"
    assert closed[0].map.region == ""


def test_none_gap_inside_real_visit_keeps_visit():                     # I2
    closed = []; t = Tracker(0, closed.append)
    t.update(r(44, 1000, 1.0), 0)
    for i in range(1, 70):
        t.update(ru(44, 1000 + i, 1.0), i)
    t.close(70)
    assert [v.map.full for v in closed] == ["A"]


def test_rate_follows_the_last_5_active_minutes():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0), 0)
    for i in range(1, 301): t.update(r(44, i, 0.0), i)                  # slow: +1/s
    for i in range(301, 601): t.update(r(44, 300 + (i - 300) * 10, 0.0), i)  # fast: +10/s
    s = t.stats(600)
    assert s.exp_per_hour == pytest.approx(36000)    # the recent pace, not the 19,800 average
    assert s.last5 == 3000


def test_rate_window_skips_afk_time():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0), 0)
    for i in range(1, 241): t.update(r(44, i * 10, 0.0), i)             # 240 s at +10/s
    for i in range(241, 1000): t.update(None, i)                         # away: no active time
    t.update(r(44, 2400, 0.0), 1000)
    for i in range(1001, 1061): t.update(r(44, 2400 + (i - 1000) * 10, 0.0), i)   # 60 s more
    assert t.stats(1060).exp_per_hour == pytest.approx(36000)


def test_rate_starts_fresh_on_a_new_map():
    t = Tracker(0, lambda v: None); t.update(r(44, 0, 0.0, "A"), 0)
    for i in range(1, 201): t.update(r(44, i * 10, 0.0, "A"), i)        # A: +10/s
    for i in range(201, 321): t.update(r(44, 2000 + (i - 200) * 2, 0.0, "B"), i)  # B: +2/s
    assert t.stats(320).exp_per_hour == pytest.approx(7200)
