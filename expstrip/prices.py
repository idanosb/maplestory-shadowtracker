import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("expstrip")


@dataclass
class Prices:
    potions: dict[str, int]
    trash: dict[str, int]


DEFAULT_PRICES = Prices(
    potions={"Meat": 80, "Lemon": 150, "Orange Potion": 150, "White Potion": 350,
             "Blue Potion": 220, "Magic Potion": 500, "Speed Potion": 500},
    trash={"Tablecloth": 34, "Stirge Wing": 20},
)


def _defaults() -> Prices:
    return Prices(dict(DEFAULT_PRICES.potions), dict(DEFAULT_PRICES.trash))


def _section(raw: dict, key: str, default: dict[str, int]) -> dict[str, int]:
    if key not in raw:
        return dict(default)
    sec = raw[key]
    if not isinstance(sec, dict):
        raise ValueError(f"{key} must be an object")
    for name, price in sec.items():
        if isinstance(price, bool) or not isinstance(price, int) or price < 0:
            raise ValueError(f"{key}.{name} must be a non-negative integer")
    return dict(sec)


def _write(prices: Prices, path: Path) -> None:
    data = {"potions": prices.potions, "trash": prices.trash}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_prices(path: Path, last_good: Prices | None = None) -> Prices:
    return load_prices_checked(path, last_good)[0]


def load_prices_checked(path: Path, last_good: Prices | None = None) -> tuple[Prices, bool]:
    """Like load_prices, plus whether the file itself was read successfully."""
    path = Path(path)
    if not path.exists():
        p = _defaults()
        try:
            _write(p, path)
        except OSError:
            log.warning("could not create prices file %s", path)
        return p, True
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("prices must be an object")
        return Prices(
            _section(raw, "potions", DEFAULT_PRICES.potions),
            _section(raw, "trash", DEFAULT_PRICES.trash),
        ), True
    except (OSError, ValueError) as e:
        log.warning("invalid prices file %s (%s); keeping %s", path, e,
                    "last good prices" if last_good is not None else "defaults")
        return (last_good if last_good is not None else _defaults()), False
