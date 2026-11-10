"""The unsold-trash tally: kept in its own small file so it survives restarts."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _valid(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def load_tally_state(path: Path) -> tuple[int, int | None]:
    """(unsold trash, last exact mesos or None). Bad or missing parts fall back to 0 / None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tally = data["unsold_trash"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0, None
    if not _valid(tally):
        return 0, None
    last = data.get("last_exact")
    return tally, (last if _valid(last) else None)


def load_tally(path: Path) -> int:
    """The saved tally, or 0 when the file is missing, unreadable or not a non-negative int."""
    return load_tally_state(path)[0]


def save_tally(path: Path, value: int, last_exact: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = {"unsold_trash": int(value), "last_exact": None if last_exact is None else int(last_exact)}
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)
