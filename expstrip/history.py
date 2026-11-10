"""Append-only CSV visit history, in-progress recovery, and per-spot summaries."""
from __future__ import annotations

import csv
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from expstrip.reading import MapName
from expstrip.tracker import Visit

_OLD_COLUMNS = ["date", "map_full", "spot", "start_time", "end_time",
                "active_seconds", "exp_gained", "level_start", "level_end"]
_MESO_COLUMNS = ["mesos_picked", "potion_cost", "meso_correction", "meso_other",
                 "mesos_net", "reconciliations"]
_LOOT_COLUMNS = ["loot_value", "found_potions_value"]
_STAGE1_COLUMNS = _OLD_COLUMNS + _MESO_COLUMNS
_COLUMNS = _STAGE1_COLUMNS + _LOOT_COLUMNS

log = logging.getLogger(__name__)


@dataclass
class SpotSummary:
    map_full: str
    spot: str
    exp_per_hour: float
    total_seconds: float
    last_visit: datetime
    is_best: bool
    mesos_per_hour: float | None = None


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="seconds")


def _epoch(text: str) -> float:
    dt = datetime.fromisoformat(text)
    try:
        return dt.timestamp()
    except (OSError, OverflowError, ValueError):
        # Windows cannot convert local times that map before 1970-01-01 UTC.
        return (dt + timedelta(days=2)).timestamp() - 2 * 86400


def _num(x: float) -> float | int:
    r = round(x, 1)
    return int(r) if r == int(r) else r


def _opt_int(text: str | None) -> int | None:
    if text is None or text.strip() == "":
        return None
    return int(text)


def _cell(x: int | None) -> int | str:
    return "" if x is None else x


def _split_map(full: str) -> MapName:
    if " : " in full:
        region, name = full.split(" : ", 1)
        return MapName(region, name)
    return MapName("", full)


class VisitStore:
    def __init__(self, data_dir: Path) -> None:
        self._dir = Path(data_dir)
        self._csv = self._dir / "visits.csv"
        self._current = self._dir / "current_visit.json"
        try:
            self._upgrade_csv()
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            log.warning("visits.csv upgrade deferred (%s); old rows stay readable", e)
            self._csv.with_name(self._csv.name + ".tmp").unlink(missing_ok=True)

    def _encoding(self) -> str:
        """utf-8-sig, or cp1252 for a file re-saved as ANSI (Excel)."""
        try:
            self._csv.read_bytes().decode("utf-8-sig")
            return "utf-8-sig"
        except UnicodeDecodeError:
            return "cp1252"

    def _open_read(self):
        return open(self._csv, encoding=self._encoding(), errors="replace", newline="")

    def _header(self) -> list[str] | None:
        try:
            if not self._csv.exists() or self._csv.stat().st_size == 0:
                return None
            with self._open_read() as f:
                return next(csv.reader(f), None)
        except (OSError, UnicodeDecodeError, csv.Error):
            return None

    def _upgrade_csv(self) -> None:
        """Rewrite a pre-meso or stage 1 CSV under the current header. Raises OSError if it cannot."""
        header = self._header()
        if header == _OLD_COLUMNS:
            bak_suffix = ".bak"
        elif header == _STAGE1_COLUMNS:
            bak_suffix = ".stage1.bak"      # keeps the pre-meso .bak, if any
        else:
            return
        pad = len(_COLUMNS) - len(header)
        bak = self._csv.with_name(self._csv.name + bak_suffix)
        if not bak.exists():
            shutil.copy2(self._csv, bak)
        tmp = self._csv.with_name(self._csv.name + ".tmp")
        try:
            with self._open_read() as src,                     open(tmp, "w", encoding="utf-8-sig", newline="") as dst:
                rd = csv.reader(src)
                w = csv.writer(dst)
                next(rd, None)
                w.writerow(_COLUMNS)
                for row in rd:
                    if row:
                        w.writerow(row + [""] * pad)
            os.replace(tmp, self._csv)
        except (OSError, UnicodeDecodeError, csv.Error):
            tmp.unlink(missing_ok=True)
            raise

    def append(self, v: Visit) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._upgrade_csv()     # never write a new-format row under an old header
        new = not self._csv.exists() or self._csv.stat().st_size == 0
        enc = "utf-8-sig" if new else "utf-8"
        with open(self._csv, "w" if new else "a", encoding=enc, newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(_COLUMNS)
            w.writerow([
                datetime.fromtimestamp(v.start).strftime("%Y-%m-%d"),
                v.map.full, v.map.map, _iso(v.start), _iso(v.end),
                _num(v.active_seconds), v.exp_gained, v.level_start, v.level_end,
                _cell(v.mesos_picked), _cell(v.potion_cost), _cell(v.meso_correction),
                _cell(v.meso_other), _cell(v.mesos_net), _cell(v.reconciliations),
                _cell(v.loot_value), _cell(v.found_potions_value),
            ])

    def load(self) -> list[Visit]:
        if not self._csv.exists():
            return []
        out: list[Visit] = []
        with self._open_read() as f:
            for row in csv.DictReader(f):
                try:
                    out.append(Visit(
                        map=_split_map(row["map_full"]),
                        start=_epoch(row["start_time"]),
                        end=_epoch(row["end_time"]),
                        active_seconds=float(row["active_seconds"]),
                        exp_gained=int(row["exp_gained"]),
                        level_start=int(row["level_start"]),
                        level_end=int(row["level_end"]),
                        mesos_picked=_opt_int(row.get("mesos_picked")),
                        potion_cost=_opt_int(row.get("potion_cost")),
                        meso_correction=_opt_int(row.get("meso_correction")),
                        meso_other=_opt_int(row.get("meso_other")),
                        reconciliations=_opt_int(row.get("reconciliations")),
                        loot_value=_opt_int(row.get("loot_value")),
                        found_potions_value=_opt_int(row.get("found_potions_value")),
                    ))
                except (KeyError, TypeError, ValueError, OSError, OverflowError):
                    continue
        return out

    def write_in_progress(self, v: Visit | None) -> None:
        if v is None:
            self._current.unlink(missing_ok=True)
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {"region": v.map.region, "map": v.map.map, "start": v.start,
                "end": v.end, "active_seconds": v.active_seconds,
                "exp_gained": v.exp_gained, "level_start": v.level_start,
                "level_end": v.level_end,
                "mesos_picked": v.mesos_picked, "potion_cost": v.potion_cost,
                "meso_correction": v.meso_correction, "meso_other": v.meso_other,
                "reconciliations": v.reconciliations,
                "loot_value": v.loot_value,
                "found_potions_value": v.found_potions_value}
        tmp = self._current.with_name(self._current.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self._current)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def load_in_progress(self) -> Visit | None:
        """The saved in-progress visit, or None. A corrupt file is deleted."""
        if not self._current.exists():
            return None
        try:
            d = json.loads(self._current.read_text(encoding="utf-8"))
            v = Visit(MapName(d["region"], d["map"]), float(d["start"]), float(d["end"]),
                      float(d["active_seconds"]), int(d["exp_gained"]),
                      int(d["level_start"]), int(d["level_end"]),
                      **{k: (None if d.get(k) is None else int(d[k])) for k in
                         ("mesos_picked", "potion_cost", "meso_correction", "meso_other",
                          "reconciliations", "loot_value", "found_potions_value")})
        except (ValueError, KeyError, TypeError):
            self._current.unlink(missing_ok=True)
            return None
        return v

    def recover_in_progress(self, min_visit: float = 60) -> None:
        v = self.load_in_progress()
        if v is not None and v.active_seconds >= min_visit:
            self.append(v)
        self._current.unlink(missing_ok=True)

    def summaries(self) -> list[SpotSummary]:
        groups: dict[str, list[Visit]] = {}
        for v in self.load():
            groups.setdefault(v.map.full, []).append(v)
        out: list[SpotSummary] = []
        for full, vs in groups.items():
            secs = sum(v.active_seconds for v in vs)
            exp = sum(v.exp_gained for v in vs)
            rate = exp / (secs / 3600) if secs > 0 else 0.0
            mv = [v for v in vs if v.mesos_net is not None]
            msecs = sum(v.active_seconds for v in mv)
            mph = sum(v.mesos_net for v in mv) / (msecs / 3600) if mv and msecs > 0 else None
            out.append(SpotSummary(full, vs[0].map.map, rate, secs,
                                   datetime.fromtimestamp(max(v.end for v in vs)), False, mph))
        out.sort(key=lambda s: s.exp_per_hour, reverse=True)
        if out:
            out[0].is_best = True
        return out
