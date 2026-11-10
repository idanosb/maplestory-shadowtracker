import pytest

from expstrip.mesos import MesoDelta, MesoLedger
from expstrip.vision.items import ItemLine
from expstrip.prices import DEFAULT_PRICES, Prices, load_prices


def test_default_prices_file_created(tmp_path):
    p = load_prices(tmp_path / "prices.json")
    assert p.potions == {"Meat": 80, "Lemon": 150, "Orange Potion": 150, "White Potion": 350,
                         "Blue Potion": 220, "Magic Potion": 500, "Speed Potion": 500}
    assert p.trash == {"Tablecloth": 34, "Stirge Wing": 20}
    assert (tmp_path / "prices.json").exists()


@pytest.mark.parametrize("text", ["{not json", '{"potions": {"Meat": -5}}', '{"potions": {"Meat": "x"}}', "[]"])
def test_broken_prices_keep_last_good(tmp_path, text, caplog):
    f = tmp_path / "prices.json"; f.write_text(text, encoding="utf-8")
    good = Prices({"Meat": 70}, {})
    assert load_prices(f, last_good=good) == good
    assert load_prices(f).potions == DEFAULT_PRICES.potions
    assert f.read_text(encoding="utf-8") == text
    assert "prices" in caplog.text.lower()


def test_partial_prices_fall_back_per_section(tmp_path):
    f = tmp_path / "prices.json"; f.write_text('{"potions": {"Meat": 90}}', encoding="utf-8")
    p = load_prices(f)
    assert p.potions == {"Meat": 90}
    assert p.trash == DEFAULT_PRICES.trash


def test_pickups_sum():
    assert MesoLedger({"Meat": 80}).on_pickups([12, 11, 12]) == MesoDelta(picked=35)


def test_potion_decrease_costs_increase_does_not():
    L = MesoLedger({"Meat": 80, "Lemon": 150})
    assert L.on_potion_counts({"Meat": 107, "Lemon": 288}) == MesoDelta()
    assert L.on_potion_counts({"Meat": 105, "Lemon": 288}) == MesoDelta(potion_cost=160)
    assert L.on_potion_counts({"Meat": 205, "Lemon": 287}) == MesoDelta(potion_cost=150)


def test_untracked_or_missing_potion_ignored():
    L = MesoLedger({"Meat": 80})
    L.on_potion_counts({"Meat": 10, "Orange": 82})
    assert L.on_potion_counts({"Orange": 70}) == MesoDelta()


def test_first_exact_is_baseline_then_small_diff_is_correction():
    L = MesoLedger({"Meat": 80})
    assert L.on_exact_mesos(1_000_000) == MesoDelta()
    L.on_pickups([500])
    assert L.on_exact_mesos(1_000_620) == MesoDelta(correction=120, reconciled=True)


def test_purchase_explains_wallet_drop():
    L = MesoLedger({"Meat": 80})
    L.on_potion_counts({"Meat": 10}); L.on_exact_mesos(100_000)
    L.on_potion_counts({"Meat": 110})
    assert L.on_exact_mesos(92_000) == MesoDelta(reconciled=True)


def test_large_diff_is_other_and_tolerance_scales():
    L = MesoLedger({}); L.on_exact_mesos(0)
    assert L.on_exact_mesos(4_000_000) == MesoDelta(other=4_000_000, reconciled=True)
    L.on_pickups([20_000])
    assert L.on_exact_mesos(4_024_900) == MesoDelta(correction=4_900, reconciled=True)


def test_restart_makes_next_exact_a_baseline():
    L = MesoLedger({}); L.on_exact_mesos(500_000); L.restart()
    assert L.on_exact_mesos(300_000) == MesoDelta()


def test_inventory_left_open_with_nothing_new_is_not_a_reconciliation():
    # Live run: the inventory stayed open and every tick logged a +0 "reconciliation".
    L = MesoLedger({"Meat": 80})
    L.on_exact_mesos(1_065_481)
    assert L.on_exact_mesos(1_065_481) == MesoDelta()
    L.on_pickups([40])
    assert L.on_exact_mesos(1_065_521) == MesoDelta(reconciled=True)
    assert L.on_exact_mesos(1_065_521) == MesoDelta()
    assert L.on_exact_mesos(1_065_400) == MesoDelta(correction=-121, reconciled=True)


def test_huge_potion_drop_is_a_misread_and_costs_nothing(caplog):
    L = MesoLedger({"Lemon": 150})
    L.on_potion_counts({"Lemon": 184})
    with caplog.at_level("WARNING"):
        assert L.on_potion_counts({"Lemon": 18}) == MesoDelta()
    assert "Lemon" in caplog.text
    assert L.on_potion_counts({"Lemon": 184}) == MesoDelta()
    assert L.bought_value_since == 0   # the recovery is not a purchase either


def test_realistic_potion_drop_still_costs():
    L = MesoLedger({"Lemon": 150})
    L.on_potion_counts({"Lemon": 184})
    assert L.on_potion_counts({"Lemon": 170}) == MesoDelta(potion_cost=14 * 150)


def test_unpriced_then_repriced_potion_has_no_phantom_cost():
    L = MesoLedger({"Lemon": 150, "Meat": 80})
    L.on_potion_counts({"Lemon": 100})
    L.set_prices({"Meat": 80})
    L.on_potion_counts({"Lemon": 50})
    L.set_prices({"Lemon": 150, "Meat": 80})
    assert L.on_potion_counts({"Lemon": 50}) == MesoDelta()


def test_prices_file_with_bom_is_loaded(tmp_path):
    p = tmp_path / "prices.json"
    p.write_bytes(b"\xef\xbb\xbf" + b'{"potions": {"Meat": 99}}')
    assert load_prices(p).potions == {"Meat": 99}


def L(name, qty, tab):
    return ItemLine(name, qty, tab)


def _loot_ledger():
    return MesoLedger({"Meat": 80, "Lemon": 150}, {"Tablecloth": 34, "Stirge Wing": 20})


def test_trash_credited_at_pickup():
    led = _loot_ledger()
    assert led.on_items([L("Tablecloth", 3, "Etc")], set()) == MesoDelta(loot=102)
    assert led.unsold_trash == 102


def test_found_potion_credited_at_shop_price_on_a_key_or_not():
    led = _loot_ledger()
    assert led.on_items([L("Lemon", 1, "Use")], {"Lemon"}) == MesoDelta(found_potions=150)
    off = _loot_ledger()
    assert off.on_items([L("Lemon", 1, "Use")], set()) == MesoDelta(found_potions=150)
    assert off._found == {} and off.unsold_trash == 0  # no key to watch, nothing to sell


def test_potion_on_etc_tab_credits_nothing():
    led = _loot_ledger()
    assert led.on_items([L("Lemon", 1, "Etc")], {"Lemon"}) == MesoDelta()


def test_trash_value_does_not_depend_on_tab():
    assert _loot_ledger().on_items([L("Tablecloth", 1, "Use")], set()) == MesoDelta(loot=34)


def test_potion_off_the_keys_counts_at_shop_price_even_if_also_trash():
    led = MesoLedger({"Orange Potion": 150}, {"Orange Potion": 40})
    assert led.on_items([L("Orange Potion", 3, "Use")], set()) == MesoDelta(found_potions=450)
    assert led.unsold_trash == 0


def test_potion_only_in_trash_counts_as_trash():
    led = MesoLedger({}, {"White Potion": 80})
    assert led.on_items([L("White Potion", 1, "Use")], set()) == MesoDelta(loot=80)


def test_found_potion_not_booked_as_purchase():
    led = _loot_ledger()
    led.on_potion_counts({"Lemon": 100})
    led.on_items([L("Lemon", 1, "Use")], {"Lemon"})
    led.on_potion_counts({"Lemon": 101})
    assert led.bought_value_since == 0
    led.on_potion_counts({"Lemon": 111})
    assert led.bought_value_since == 1500


def test_key_increase_before_item_line():
    led = _loot_ledger()
    led.on_exact_mesos(10000)
    led.on_potion_counts({"Lemon": 100})
    led.on_potion_counts({"Lemon": 101})
    assert led.bought_value_since == 150
    assert led.on_items([L("Lemon", 1, "Use")], {"Lemon"}) == MesoDelta(found_potions=150)
    assert led.on_exact_mesos(10000) == MesoDelta(correction=150, reconciled=True)


def test_full_sale_reduces_tally_not_net():
    led = _loot_ledger()
    led.on_exact_mesos(100000)
    led.on_items([L("Tablecloth", 30, "Etc")], set())
    assert led.unsold_trash == 1020
    assert led.on_exact_mesos(101020) == MesoDelta(reconciled=True)
    assert led.unsold_trash == 0


def test_small_sale_under_tolerance_is_not_a_correction():
    led = _loot_ledger()
    led.on_exact_mesos(100000)
    led.on_items([L("Tablecloth", 20, "Etc")], set())
    assert led.unsold_trash == 680
    assert led.on_exact_mesos(100680).correction == 0
    assert led.unsold_trash == 0


def test_partial_sale():
    led = _loot_ledger()
    led.on_exact_mesos(100000)
    led.on_items([L("Tablecloth", 30, "Etc")], set())
    assert led.on_exact_mesos(100500).correction == 0
    assert led.unsold_trash == 520


def test_sold_for_more_than_listed():
    led = _loot_ledger()
    led.on_exact_mesos(100000)
    led.on_items([L("Tablecloth", 30, "Etc")], set())
    assert led.on_exact_mesos(101100).correction == 80
    assert led.unsold_trash == 0


def test_reset_keeps_tally_clears_pool():
    led = _loot_ledger()
    led.unsold_trash = 500
    led.on_items([L("Lemon", 2, "Use")], {"Lemon"})
    led.restart()
    assert led.unsold_trash == 500
    assert led._found == {}


def test_drinking_found_potion_nets_zero():
    led = _loot_ledger()
    led.on_potion_counts({"Lemon": 101})
    found = led.on_items([L("Lemon", 1, "Use")], {"Lemon"}).found_potions
    cost = led.on_potion_counts({"Lemon": 100}).potion_cost
    assert found - cost == 0


def test_sale_after_reset_drains_tally():
    led = _loot_ledger()
    led.on_exact_mesos(100000)
    led.unsold_trash = 4000
    led.restart(keep_exact=True)
    assert led.last_exact == 100000
    assert led.on_exact_mesos(104000) == MesoDelta(reconciled=True)
    assert led.unsold_trash == 0
    # a later missed pickup is a correction, not absorbed
    led.on_pickups([2000])
    assert led.on_exact_mesos(104000).correction == -2000


def test_plain_restart_drops_exact_and_carried():
    led = _loot_ledger()
    led.on_exact_mesos(100000)
    led.carried_exact = 5
    led.restart()
    assert led.last_exact is None and led.carried_exact is None


def test_carried_exact_drains_tally_at_first_reading():
    led = _loot_ledger()
    led.unsold_trash = 4000
    led.carried_exact = 100000
    assert led.on_exact_mesos(103000) == MesoDelta()
    assert led.unsold_trash == 1000
    assert led.carried_exact is None and led.last_exact == 103000
    assert led.picked_since == 0


def test_carried_drain_capped_at_tally():
    led = _loot_ledger()
    led.unsold_trash = 500
    led.carried_exact = 100000
    assert led.on_exact_mesos(150000) == MesoDelta()
    assert led.unsold_trash == 0


def test_carried_lower_value_just_rebases():
    led = _loot_ledger()
    led.unsold_trash = 4000
    led.carried_exact = 100000
    assert led.on_exact_mesos(90000) == MesoDelta()
    assert led.unsold_trash == 4000
    assert led.carried_exact is None
    assert led.on_exact_mesos(90000 + 4000).reconciled


def test_exact_reference_prefers_last_exact_then_carried():
    led = _loot_ledger()
    assert led.exact_reference is None
    led.carried_exact = 7
    assert led.exact_reference == 7
    led.on_exact_mesos(9)
    assert led.exact_reference == 9


def test_carried_drain_ignores_this_runs_pickups_and_trash():
    # App start with 4000 of old trash; before the first inventory reading the player picks
    # up 500 mesos and 340 of new trash, sells nothing. The tally must stay 4340.
    led = _loot_ledger()
    led.unsold_trash = 4000
    led.carried_exact = 100000
    led.on_pickups([500])
    led.on_items([L("Tablecloth", 10, "Etc")], set())
    assert led.on_exact_mesos(100500) == MesoDelta()
    assert led.unsold_trash == 4340


def test_carried_drain_only_takes_old_trash():
    # Old trash 1000 sold while the app was off, then 340 of new trash picked up: the drain is
    # capped at the old 1000, the new 340 stays in the tally.
    led = _loot_ledger()
    led.unsold_trash = 1000
    led.carried_exact = 100000
    led.on_items([L("Tablecloth", 10, "Etc")], set())
    assert led.on_exact_mesos(105000) == MesoDelta()
    assert led.unsold_trash == 340
