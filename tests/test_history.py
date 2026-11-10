import pytest

from expstrip.history import VisitStore
from expstrip.reading import MapName
from expstrip.tracker import Visit


def test_append_and_load_roundtrip_with_comma_and_quote(tmp_path):   # Review Focus 5
    st = VisitStore(tmp_path)
    v = Visit(MapName("Region", 'Big, "Bad" Map'), 1_762_000_000, 1_762_000_600, 600, 12000, 44, 44)
    st.append(v); st.append(v)
    assert st.load() == [v, v]
    assert (tmp_path / "visits.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_summaries_ranked(tmp_path):
    st = VisitStore(tmp_path)
    st.append(Visit(MapName("", "A"), 0, 3600, 3600, 50000, 44, 44))
    st.append(Visit(MapName("", "B"), 0, 1800, 1800, 40000, 44, 44))
    st.append(Visit(MapName("", "A"), 4000, 7600, 3600, 70000, 44, 45))
    s = st.summaries()
    assert [x.spot for x in s] == ["B", "A"] and s[0].is_best and not s[1].is_best
    assert s[1].exp_per_hour == pytest.approx(60000) and s[1].total_seconds == 7200


def test_recover_in_progress(tmp_path):
    st = VisitStore(tmp_path)
    st.write_in_progress(Visit(MapName("", "A"), 0, 900, 900, 9000, 44, 44))
    VisitStore(tmp_path).recover_in_progress()
    assert len(st.load()) == 1 and not (tmp_path / "current_visit.json").exists()


def test_recover_short_in_progress_dropped(tmp_path):
    st = VisitStore(tmp_path)
    st.write_in_progress(Visit(MapName("", "A"), 0, 30, 30, 100, 44, 44))
    st.recover_in_progress()
    assert st.load() == []


# ---- meso columns / csv upgrade -------------------------------------------
import csv as _csv
import os

OLD_HEADER = ["date", "map_full", "spot", "start_time", "end_time",
              "active_seconds", "exp_gained", "level_start", "level_end"]


def write_old_csv(d, rows=3):
    with open(d / "visits.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(OLD_HEADER)
        for i in range(rows):
            w.writerow(["2026-01-0%d" % (i + 1), "A", "A", "2026-01-0%dT10:00:00" % (i + 1),
                        "2026-01-0%dT11:00:00" % (i + 1), 3600, 1000, 44, 44])


def read_header(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return next(_csv.reader(f))


def visit_with_mesos(picked, cost, corr, active=600, other=0, recon=0, map_name="A", start=1_762_000_000):
    v = Visit(MapName("", map_name), start, start + active, active, 100, 44, 44)
    v.mesos_picked, v.potion_cost, v.meso_correction = picked, cost, corr
    v.meso_other, v.reconciliations = other, recon
    return v


def make_replace_raise_permission_error(monkeypatch):
    def boom(*a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(os, "replace", boom)


def test_old_csv_upgraded_with_backup(tmp_path):
    write_old_csv(tmp_path, rows=3)
    store = VisitStore(tmp_path)
    assert (tmp_path / "visits.csv.bak").exists()
    vs = store.load()
    assert len(vs) == 3 and vs[0].mesos_picked is None
    store.append(visit_with_mesos(picked=500, cost=160, corr=20))
    header = read_header(tmp_path / "visits.csv")
    assert header[-8:] == ["mesos_picked", "potion_cost", "meso_correction", "meso_other", "mesos_net", "reconciliations", "loot_value", "found_potions_value"]
    assert store.load()[-1].mesos_net == 360
    assert (tmp_path / "visits.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_upgrade_when_locked_keeps_rows(tmp_path, monkeypatch):
    write_old_csv(tmp_path, rows=3); make_replace_raise_permission_error(monkeypatch)
    store = VisitStore(tmp_path)
    assert len(store.load()) == 3
    with pytest.raises(PermissionError):
        store.append(visit_with_mesos(500, 160, 20))
    assert read_header(tmp_path / "visits.csv") == OLD_HEADER
    assert len(store.load()) == 3
    monkeypatch.undo()
    store.append(visit_with_mesos(500, 160, 20))
    assert len(store.load()) == 4 and read_header(tmp_path / "visits.csv")[-1] == "found_potions_value"


def test_summary_meso_rate_skips_old_visits(tmp_path):
    st = VisitStore(tmp_path)
    st.append(Visit(MapName("", "A"), 0, 3600, 3600, 1000, 44, 44))
    st.append(visit_with_mesos(41000, 0, 0, active=3600, other=5_000_000))
    s = st.summaries()
    assert s[0].mesos_per_hour == pytest.approx(41000)


def test_summary_meso_rate_none_without_data(tmp_path):
    st = VisitStore(tmp_path)
    st.append(Visit(MapName("", "A"), 0, 3600, 3600, 1000, 44, 44))
    assert st.summaries()[0].mesos_per_hour is None


def test_in_progress_carries_mesos_and_tolerates_missing(tmp_path):
    st = VisitStore(tmp_path)
    st.write_in_progress(visit_with_mesos(500, 160, 20, active=900, other=7, recon=2))
    VisitStore(tmp_path).recover_in_progress()
    v = st.load()[0]
    assert (v.mesos_picked, v.potion_cost, v.meso_correction, v.meso_other, v.reconciliations) == (500, 160, 20, 7, 2)
    (tmp_path / "current_visit.json").write_text(
        '{"region":"","map":"B","start":0,"end":900,"active_seconds":900,"exp_gained":1,"level_start":1,"level_end":1}',
        encoding="utf-8")
    VisitStore(tmp_path).recover_in_progress()
    assert st.load()[-1].mesos_picked is None


def _old_ansi_csv(tmp_path, rows):
    head = "date,map_full,spot,start_time,end_time,active_seconds,exp_gained,level_start,level_end\n"
    lines = [f"2026-01-01,Café : Spot{i},Spot{i},2026-01-01T10:00:00,2026-01-01T10:10:00,600,1000,44,44\n"
             for i in range(rows)]
    (tmp_path / "visits.csv").write_bytes((head + "".join(lines)).encode("cp1252"))


@pytest.mark.parametrize("rows", [3, 200])   # under and over 8 KB
def test_old_ansi_csv_upgrades_without_losing_rows(tmp_path, rows):
    _old_ansi_csv(tmp_path, rows)
    assert (tmp_path / "visits.csv").stat().st_size > (8192 if rows > 100 else 0)
    st = VisitStore(tmp_path)
    assert not (tmp_path / "visits.csv.tmp").exists()
    first = (tmp_path / "visits.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert first.endswith("found_potions_value")
    vs = st.load()
    assert len(vs) == rows and vs[0].map.full == "Café : Spot0"
    st.append(Visit(MapName("", "B"), 0, 600, 600, 5, 44, 44, 1, 2, 3, 4, 1))
    assert len(st.load()) == rows + 1


def test_old_ansi_csv_load_without_upgrade(tmp_path):
    _old_ansi_csv(tmp_path, 2)
    st = VisitStore(tmp_path)
    (tmp_path / "visits.csv").write_bytes(
        "date,map_full\n2026-01-01,Café\n".encode("cp1252"))   # not upgradable, still loadable
    assert st.load() == []


def test_failed_upgrade_never_crashes_and_leaves_no_tmp(tmp_path, monkeypatch):
    _old_ansi_csv(tmp_path, 3)
    import expstrip.history as h
    monkeypatch.setattr(h.os, "replace", lambda *a: (_ for _ in ()).throw(UnicodeDecodeError("x", b"", 0, 1, "y")))
    VisitStore(tmp_path)
    assert not (tmp_path / "visits.csv.tmp").exists()


# ---- stage 2 (loot) columns -------------------------------------------------
STAGE1_HEADER = OLD_HEADER + ["mesos_picked", "potion_cost", "meso_correction",
                              "meso_other", "mesos_net", "reconciliations"]


def write_stage1_csv(d, rows=2):
    with open(d / "visits.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(STAGE1_HEADER)
        for i in range(rows):
            w.writerow(["2026-01-0%d" % (i + 1), "A", "A", "2026-01-0%dT10:00:00" % (i + 1),
                        "2026-01-0%dT11:00:00" % (i + 1), 3600, 1000, 44, 44,
                        500, 100, 10, 0, 410, 1])


def test_stage1_row_counts_loot_as_zero(tmp_path):
    write_stage1_csv(tmp_path)
    st = VisitStore(tmp_path)
    vs = st.load()
    assert vs[0].loot_value is None and vs[0].found_potions_value is None
    assert vs[0].mesos_net == 410
    assert st.summaries()[0].mesos_per_hour == pytest.approx(410)


def test_upgrade_from_stage1_header(tmp_path):
    write_stage1_csv(tmp_path, rows=2)
    st = VisitStore(tmp_path)
    assert (tmp_path / "visits.csv.stage1.bak").exists()
    assert read_header(tmp_path / "visits.csv") == STAGE1_HEADER + ["loot_value", "found_potions_value"]
    with open(tmp_path / "visits.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.reader(f))[1:]
    assert len(rows) == 2 and rows[0][-2:] == ["", ""] and rows[0][9] == "500"
    assert len(st.load()) == 2


def test_stage1_upgrade_keeps_existing_stage1_bak(tmp_path):
    write_stage1_csv(tmp_path)
    (tmp_path / "visits.csv.bak").write_text("precious", encoding="utf-8")
    VisitStore(tmp_path)
    assert (tmp_path / "visits.csv.bak").read_text(encoding="utf-8") == "precious"
    assert (tmp_path / "visits.csv.stage1.bak").exists()


def test_loot_round_trip_csv(tmp_path):
    st = VisitStore(tmp_path)
    v = visit_with_mesos(100, 30, 5)
    v.loot_value, v.found_potions_value = 34, 150
    st.append(v)
    got = st.load()[0]
    assert (got.loot_value, got.found_potions_value, got.mesos_net) == (34, 150, 259)


def test_in_progress_round_trip_with_loot(tmp_path):
    st = VisitStore(tmp_path)
    v = visit_with_mesos(100, 30, 5, active=900)
    v.loot_value, v.found_potions_value = 34, 150
    st.write_in_progress(v)
    VisitStore(tmp_path).recover_in_progress()
    got = st.load()[0]
    assert (got.loot_value, got.found_potions_value, got.mesos_net) == (34, 150, 259)


def test_in_progress_write_is_atomic(tmp_path):                       # M3
    st = VisitStore(tmp_path)
    v = Visit(MapName("R", "A"), 0, 900, 900, 9000, 44, 44)
    st.write_in_progress(v)
    assert list(tmp_path.glob("*.tmp")) == []
    assert st.load_in_progress() == v


def test_in_progress_write_uses_replace(tmp_path, monkeypatch):       # M3
    import os
    calls = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append((str(a), str(b))), real(a, b))[1])
    VisitStore(tmp_path).write_in_progress(Visit(MapName("", "A"), 0, 900, 900, 1, 44, 44))
    assert len(calls) == 1 and calls[0][1].endswith("current_visit.json")


def test_load_in_progress_corrupt_returns_none_and_deletes(tmp_path):
    (tmp_path / "current_visit.json").write_text("{bad", encoding="utf-8")
    assert VisitStore(tmp_path).load_in_progress() is None
    assert not (tmp_path / "current_visit.json").exists()
