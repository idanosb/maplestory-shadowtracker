import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    window_title: str = "MapleStory"
    strip_x: int | None = None
    strip_y: int | None = None
    locked: bool = False
    level_hint: int | None = None  # used once when the level digits can't be read and no level is known yet
    show_in_share: bool = False    # strip and panel visible in screen shares (Discord, OBS); hidden by default


def save_settings(s: Settings, path: Path) -> None:
    Path(path).write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")


def load_settings(path: Path) -> Settings:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings must be an object")
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in raw.items() if k in known})
    except (OSError, ValueError, TypeError):
        s = Settings()
        try:
            save_settings(s, path)
        except OSError:
            pass
        return s
