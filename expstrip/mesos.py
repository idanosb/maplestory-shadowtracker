import logging
from dataclasses import dataclass

from expstrip.vision.items import ItemLine

log = logging.getLogger(__name__)

BASE_TOLERANCE = 3000
PICKUP_TOLERANCE_FRACTION = 0.25
MAX_POTION_DROP = 20   # more than this between two reads is a misread, not drinking


@dataclass
class MesoDelta:
    picked: int = 0
    potion_cost: int = 0
    correction: int = 0
    other: int = 0
    reconciled: bool = False
    loot: int = 0
    found_potions: int = 0


class MesoLedger:
    def __init__(self, prices: dict[str, int], trash: dict[str, int] | None = None):
        self.prices = dict(prices)
        self.trash = dict(trash or {})
        self._unsold_trash = 0
        self.carried_exact: int | None = None   # last exact value saved by a previous run
        self.restart()

    @property
    def unsold_trash(self) -> int:
        return self._unsold_trash

    @unsold_trash.setter
    def unsold_trash(self, value: int) -> None:
        self._unsold_trash = max(0, int(value))

    @property
    def exact_reference(self) -> int | None:
        """The exact-mesos value worth saving: the live one, else the one carried from last run."""
        return self.last_exact if self.last_exact is not None else self.carried_exact

    def restart(self, keep_exact: bool = False) -> None:
        """Drop per-session state. keep_exact (reset) keeps the exact-mesos reference so a
        sale made before the next reading is still seen; resume and app start drop it."""
        keep = ((self.last_exact, self.picked_since, self.bought_value_since, self.trash_since)
                if keep_exact else None)
        if not keep_exact:
            self.carried_exact = None
        self._ref: dict[str, int] = {}
        self._suspect: dict[str, int] = {}   # count before a discarded drop
        self._found: dict[str, int] = {}     # potion units picked up, not yet seen on the key
        if keep is None:
            self.last_exact: int | None = None
            self.picked_since = 0
            self.bought_value_since = 0
            self.trash_since = 0     # trash credited since the last exact reading
        else:
            self.last_exact, self.picked_since, self.bought_value_since, self.trash_since = keep

    def set_prices(self, prices: dict[str, int], trash: dict[str, int] | None = None) -> None:
        self.prices = dict(prices)
        if trash is not None:
            self.trash = dict(trash)
        for name in [n for n in self._ref if n not in self.prices]:
            del self._ref[name]
            self._suspect.pop(name, None)

    def on_pickups(self, amounts: list[int]) -> MesoDelta:
        total = sum(amounts)
        self.picked_since += total
        return MesoDelta(picked=total)

    def on_items(self, items: list[ItemLine], tracked: set[str]) -> MesoDelta:
        loot = found = 0
        for it in items:
            if it.tab == "Use" and it.name in self.prices:
                # a potion is worth its shop price, on a key or not: it saves buying one later
                found += self.prices[it.name] * it.qty
                if it.name in tracked:  # its key will show the rise: not a purchase
                    self._found[it.name] = self._found.get(it.name, 0) + it.qty
            elif it.name in self.trash:
                # loot to sell: worth its sell price
                value = self.trash[it.name] * it.qty
                loot += value
                self._unsold_trash += value
                self.trash_since += value
        return MesoDelta(loot=loot, found_potions=found)

    def on_potion_counts(self, counts: dict[str, int]) -> MesoDelta:
        cost = 0
        for name, n in counts.items():
            price = self.prices.get(name)
            if price is None:
                continue
            prev = self._ref.get(name)
            if prev is not None:
                if prev - n > MAX_POTION_DROP:
                    log.warning("%s count fell %d -> %d; treated as a misread, no cost",
                                name, prev, n)
                    self._suspect[name] = prev
                elif n < prev:
                    cost += (prev - n) * price
                    self._suspect.pop(name, None)
                elif n > prev:
                    pool = self._found.get(name, 0)
                    used = min(pool, n - prev)
                    if used:
                        self._found[name] = pool - used
                    extra = n - prev - used
                    if extra <= 0 or n <= self._suspect.get(name, -1):
                        pass    # found, or back after a misread: not a purchase
                    else:
                        self.bought_value_since += extra * price
                    self._suspect.pop(name, None)
            self._ref[name] = n
        return MesoDelta(potion_cost=cost)

    def on_exact_mesos(self, mesos: int) -> MesoDelta:
        if self.last_exact is None:
            carried, self.carried_exact = self.carried_exact, None
            if carried is not None:
                # a sale made while the app was off: drain the tally, nothing else. Mesos
                # picked up and trash credited since start are this run's, not that sale.
                sold = mesos - (carried + self.picked_since - self.bought_value_since)
                old_trash = max(0, self._unsold_trash - self.trash_since)
                if sold > 0:
                    self._unsold_trash -= min(sold, old_trash)
            self._rebase(mesos)
            return MesoDelta()
        if mesos == self.last_exact and not self.picked_since and not self.bought_value_since:
            # The inventory stayed open and nothing happened: not a new reconciliation.
            return MesoDelta()
        expected = self.last_exact + self.picked_since - self.bought_value_since
        diff = mesos - expected
        sold = 0
        if diff > 0:
            sold = min(diff, self._unsold_trash)
            self._unsold_trash -= sold
            diff -= sold
        tol = max(BASE_TOLERANCE, int(self.picked_since * PICKUP_TOLERANCE_FRACTION))
        self._rebase(mesos)
        if abs(diff) <= tol:
            return MesoDelta(correction=diff, reconciled=True)
        return MesoDelta(other=diff, reconciled=True)

    def _rebase(self, mesos: int) -> None:
        self.last_exact = mesos
        self.picked_since = 0
        self.bought_value_since = 0
        self.trash_since = 0
