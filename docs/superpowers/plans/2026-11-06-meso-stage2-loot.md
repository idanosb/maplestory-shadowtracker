# Meso stage 2 (trash loot, found potions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Credit trash loot and floor-found potions from the item pickup lines at once. Never count the later trash sale twice. Show a single meso total on the strip.

**Architecture:**
- A new pure image module `expstrip/vision/items.py` reads `<Name> x<N> earned. (Etc|Use)` lines from the same region as the meso lines.
- `PickupCounter` becomes key-generic, so one counter tracks meso lines and item lines together and a line is never counted by both.
- `MesoLedger` gains loot credit, a per-potion found-unit pool, and the unsold-trash tally, which is consumed first at reconciliation.
- Tracker, history and the strip carry the new fields.

**Tech Stack:** Python 3.12, numpy, opencv-python, PySide6, pytest. Venv `.venv`; tests: `.venv/Scripts/python.exe -m pytest -q`. Build: `.venv/Scripts/pyinstaller.exe --noconfirm ExpStrip.spec`.

**Spec:** `docs/superpowers/specs/2026-11-06-meso-stage2-loot-design.md` (builds on `2026-11-06-meso-tracker-design.md`)

## Global Constraints

- Screen pixels and user32 window queries only. No process access, memory reads, injection or hooks, input, network, or admin rights. No suspicious names.
- The player never has to do anything for the tool: no reset after selling, no key presses.
- CPU under 1% of a 16-thread CPU on the real game. Stage 1 measured 0.58%.
- `prices.json` format is unchanged. `trash` holds NPC sell prices and `potions` holds shop buy prices. Defaults: Meat 80, Lemon 150, Tablecloth 34, Stirge Wing 20.
- Every item line is counted exactly once, and two equal lines in a row count 2.
- A name cut with "..." matches only if the visible start is ≥ 6 letters and unique among the price names.
- Item lines are ignored while paused.
- `visits.csv` new columns go after the stage 1 ones: `loot_value, found_potions_value`. `mesos_net = mesos_picked + loot_value + found_potions_value − potion_cost + meso_correction`. Upgrade once with a `.bak`, keeping every row, in UTF-8 with BOM.
- Strip sub-line: `Mesos +12.4K · 41K/h`. One total, green / red / neutral, `/h` after 60 s of active time.
- Feasibility gate: the item lines read on `recordings/loot_a` must capture ≥ 90% of the 55 true trash pickups (Tablecloth 40, Stirge Wing 15), i.e. ≥ 50. If not, stop and report to the player.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No push, no merge.

## Review Focus

1. **Party EXP lines** ("You received EXP (+20)", "Bonus EXP for PARTY (+4)") fill the stack in `loot_a`. They must never read as an item or a meso line. Test in Task 1.
2. **A pile of loot picked up within a second** (several item lines appear in one frame). Each must count once, and none should be lost to the line-tracking alignment. Test in Task 2.
3. **A trash sale smaller than the stage 1 tolerance (≤ 3,000)** must reduce the tally, not become a correction. Test in Task 3.
4. **Restarting the app between pickup and sale** must keep the unsold-trash tally. Otherwise the sale lands in "other" or as a correction. Test in Task 4.
5. **An unknown item line, or one whose name isn't in prices.json** (other mobs' drops), is ignored, with no crash and no credit. Test in Task 1.

## Rulings made while planning

- **Spec deviation:** the spec says the unsold-trash tally survives a restart "through `current_visit.json`". That file is deleted on every clean shutdown (`Engine._shutdown` → `write_in_progress(None)`), so the tally would not survive. The tally goes in its own file, `data/loot_tally.json`, which is written whenever the tally changes. The spec's intent (survive a restart) is kept. Task 4 updates the spec line.
- **One counter for both line kinds:** meso and item lines share one stack and scroll together. Feeding both to a single `PickupCounter` gives the shift alignment more lines to work with, and it rules out double counting.

---

### Task 1: Item-line reader and feasibility gate (STOP GATE)

**Files:**
- Create: `expstrip/vision/items.py`, `tools/extract_item_templates.py`, `tools/replay_items.py`, `expstrip/templates/items/` (suffix and name templates), `tests/test_items.py`, `tests/fixtures/items/` (pre-cropped pickup-region frames from `loot_a`, plus `truth.json`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class ItemLine: name: str; qty: int; tab: str  # "Etc" | "Use"`
  - `read_item_lines(crop: np.ndarray, scale: float, names: list[str]) -> list[tuple[ItemLine, float]]` — item lines in a `pickup_rect` crop as (line, line center y in native px), top to bottom. Only names in `names` are returned. Cut names follow the ≥6-letters-and-unique rule. Unknown, unreadable and non-item lines are left out.
  - `match_name(visible: str, names: list[str]) -> str | None` — the cut-name rule, as a pure string function.
- Consumes: `pickups.pickup_rect`, `pickups.ink`, `pickups._templates()` digit glyphs (the quantity digits are expected in the same font), `glyphs.to_native`.

The reading method is open (spec §8). Recommended: anchor on the right-aligned suffix templates ` earned. (Etc)` / ` earned. (Use)` (TM_CCOEFF_NORMED on `ink`, as for the meso prefix). Read the quantity digits right to left from the suffix, back to the `x`. Then score per-name templates (`templates/items/<Name>.png`, cut from `loot_a` by `tools/extract_item_templates.py`) ending just left of ` x`. A name with no template is unreadable and is skipped. That is the spec §6.6 "learn from a short recording" path.

- [ ] **Step 1:** Build `tools/replay_items.py recordings/loot_a`, mirroring `tools/replay_pickups.py`. It runs `PickupCounter` over the item lines of every `pickups_*.png` (keys `(name, qty)` per Task 2's generic counter, or a local equivalent until then). It prints the per-name totals and the ratio against the truth: Tablecloth 40, Stirge Wing 15, total 55.
- [ ] **Step 2:** Implement the reader and cut the templates from `loot_a`. Iterate until replay ≥ 50/55, or until it is clear it can't get there.
- [ ] **Step 3 (gate):** Record the replay output in the task report. **If < 50/55, STOP: report to the controller, who reports to the player. Build nothing else.**
- [ ] **Step 4: Tests in `tests/test_items.py`** using `tests/fixtures/items/` (frames picked from `loot_a`, with truth written by eye in `truth.json`):
  - `test_lines_match_truth[frame]` — each fixture frame's `read_item_lines` equals its truth list (name, qty, tab).
  - `test_party_exp_lines_are_not_items` — a frame with only "You received EXP" / "Bonus EXP for PARTY" lines → `[]`.
  - `test_meso_line_is_not_an_item` — a frame with a "You have gained mesos" line → that line is not returned.
  - `test_unknown_name_ignored` — `names=["Tablecloth"]` on a Stirge Wing frame → no Stirge Wing line.
  - `test_match_name`: `match_name("Tablecloth", N) == "Tablecloth"`; `match_name("Tablec...", N) == "Tablecloth"` (≥6 letters, unique); `match_name("Tab...", N) is None` (<6); with `N = ["Stirge Wing", "Stirge Fang"]`, `match_name("Stirge...", N) is None` (not unique).
  - `test_blank_crop` → `[]`.
- [ ] **Step 5:** Run `.venv/Scripts/python.exe -m pytest tests/test_items.py -q` → PASS; the full suite → PASS.
- [ ] **Step 6:** Commit: `feat: item pickup line reader (trash, potions)`.

### Task 2: One counter for meso and item lines

**Files:**
- Modify: `expstrip/vision/pickups.py` (`PickupCounter`), `expstrip/worker.py` (`PickupReader`)
- Test: `tests/test_pickups.py`, `tests/test_worker.py`

**Interfaces:**
- Produces:
  - `PickupCounter.feed(lines: list[tuple[Hashable, float]]) -> list[Hashable]` — the same algorithm, with track keys compared by equality instead of being int amounts. Existing int-key callers keep working.
  - `PickupReader.__call__(frame, scale) -> list[int]` stays the meso API. New: `PickupReader.items -> list[ItemLine]` holds the item lines that were new in the last call, emptied at each call.
  - `PickupReader(names: Callable[[], list[str]])` — the names to read: trash names plus potion names from the current prices.
- Consumes: Task 1's `read_item_lines`, `ItemLine`.

`PickupReader` feeds one counter with keys `("m", amount)` for meso lines and `("i", ItemLine)` for item lines. Both are read from the same changed region.

- [ ] **Step 1: Failing tests:**
  - `test_counter_generic_keys` — `feed([(("i", L("Tablecloth",1,"Etc")), 100.0)])` returns that key. A second identical feed returns `[]`. After a shift of one line up, with a new identical line below, it returns one more.
  - `test_pile_of_items_in_one_frame_each_counted` — three item lines appear at once below two old meso lines, so all three are returned.
  - `test_mixed_stack_never_double_counts` — replaying a `loot_a` fixture sequence through `PickupReader` gives meso and item totals equal to the sums of the per-frame truth.
  - Existing `test_pickups.py` tests unchanged and passing.
- [ ] **Step 2:** Implement, then run `tests/test_pickups.py tests/test_worker.py -q` → PASS, and the full suite → PASS.
- [ ] **Step 3:** Commit: `feat: one pickup counter for meso and item lines`.

### Task 3: Ledger — loot credit, found potions, unsold-trash tally

**Files:**
- Modify: `expstrip/mesos.py`
- Test: `tests/test_mesos.py`

**Interfaces:**
- Produces:
  - `MesoDelta` gains `loot: int = 0` and `found_potions: int = 0`.
  - `MesoLedger.__init__(prices: dict[str, int], trash: dict[str, int] | None = None)`.
  - `MesoLedger.set_prices(prices, trash=None)`.
  - `MesoLedger.on_items(items: list[ItemLine], tracked: set[str]) -> MesoDelta`.
  - `MesoLedger.unsold_trash: int`, a property with a setter for restoring.
  - `MesoLedger.restart()` clears the found pools but keeps `unsold_trash` (spec §6.3, reset).
- Behaviour:
  - A trash line credits `trash[name] × qty` to `loot` and adds it to `unsold_trash`.
  - A Use line for a potion in `tracked` (seen on a key) credits `prices[name] × qty` to `found_potions` and adds `qty` to that potion's found pool. One not in `tracked` credits nothing.
  - In `on_potion_counts`, an increase first consumes the pool, and only `max(0, increase − pool)` is booked to `bought_value_since`.
  - In `on_exact_mesos`, after `diff` is computed and before the tolerance check: if `diff > 0`, `sold = min(diff, unsold_trash)`; `unsold_trash -= sold`; `diff -= sold`. Then the stage 1 rules apply. A remaining `diff == 0` with `sold > 0` returns `MesoDelta(reconciled=True)`.

- [ ] **Step 1: Failing tests:**
  - `test_trash_credited_at_pickup`: trash `{"Tablecloth": 34, "Stirge Wing": 20}`; `on_items([L("Tablecloth",3,"Etc")], set()) == MesoDelta(loot=102)`; `unsold_trash == 102`.
  - `test_found_potion_credited_only_if_tracked`: `on_items([L("Lemon",1,"Use")], {"Lemon"}) == MesoDelta(found_potions=150)`; with `tracked=set()` → `MesoDelta()`.
  - `test_found_potion_not_booked_as_purchase`: counts Lemon 100 → item Lemon ×1 → counts 101 → `bought_value_since == 0`. Then counts 111 → `bought_value_since == 1500`.
  - `test_key_increase_before_item_line`: counts 100 → 101 (booked 150 as a purchase) → item ×1 credits 150. The next exact reading gives a small positive correction of 150.
  - `test_full_sale_reduces_tally_not_net`: baseline 100,000; trash 1,020 credited; exact 101,020 → `MesoDelta(reconciled=True)`, `unsold_trash == 0`.
  - `test_small_sale_under_tolerance_is_not_a_correction`: tally 680; exact +680 → correction 0, tally 0.
  - `test_partial_sale`: tally 1,020; exact +500 → tally 520, correction 0.
  - `test_sold_for_more_than_listed`: tally 1,020; exact +1,100 → tally 0, correction 80.
  - `test_reset_keeps_tally_clears_pool`: tally 500, pool Lemon 2; `restart()` → `unsold_trash == 500`, the pool is empty.
  - `test_drinking_found_potion_nets_zero`: item Lemon ×1 (+150), then counts 101 → 100 (−150) → total net 0.
- [ ] **Step 2:** Implement, then run `tests/test_mesos.py -q` → PASS, and the full suite → PASS.
- [ ] **Step 3:** Commit: `feat: ledger credits loot and found potions, tally absorbs trash sales`.

### Task 4: Tracker, history and tally persistence

**Files:**
- Modify: `expstrip/tracker.py`, `expstrip/history.py`, `docs/superpowers/specs/2026-11-06-meso-stage2-loot-design.md` (the tally-file line, per the ruling)
- Create: `expstrip/loot_tally.py` — `load_tally(path: Path) -> int` (0 if missing or broken) and `save_tally(path: Path, value: int) -> None` (temp file + `os.replace`)
- Test: `tests/test_tracker.py`, `tests/test_history.py`, `tests/test_loot_tally.py`

**Interfaces:**
- Produces:
  - `Visit` gains `loot_value: int | None = None` and `found_potions_value: int | None = None`.
  - `mesos_net` uses the spec formula, treating `None` as 0 for the new fields.
  - `Stats` keeps `mesos_net` and `mesos_per_hour`, and removes `mesos_picked` and `potion_cost` from what the strip needs (keep them if other code still reads them).
  - `Tracker.add_mesos` adds `d.loot` / `d.found_potions` to the session and the visit.
  - `history._COLUMNS` = stage 1 columns + `["loot_value", "found_potions_value"]`.
  - A one-time upgrade from the stage 1 header (and still from the pre-meso header), with `.bak`, using stage 1's crash-safe path. `current_visit.json` carries the new fields.
- [ ] **Step 1: Failing tests:**
  - `test_mesos_net_includes_loot`: `Visit(..., mesos_picked=100, potion_cost=30, meso_correction=5, loot_value=34, found_potions_value=150).mesos_net == 259`.
  - `test_stage1_row_counts_loot_as_zero`: a row with stage 1 fields and no loot fields loads with `mesos_net == picked − cost + corr`, and stays in the Mesos/hour average.
  - `test_upgrade_from_stage1_header`: a stage 1 file → new header, rows kept, `.bak` written, new fields empty.
  - `test_upgrade_from_pre_meso_header` still passes.
  - `test_in_progress_round_trip_with_loot`.
  - `test_tally_round_trip` and `test_tally_missing_or_broken_is_zero`.
  - `test_add_mesos_loot_goes_to_session_and_visit`.
- [ ] **Step 2:** Implement, then run the affected tests → PASS, and the full suite → PASS.
- [ ] **Step 3:** Commit: `feat: loot fields in visits and history, tally file`.

### Task 5: Engine wiring and strip

**Files:**
- Modify: `expstrip/worker.py`, `expstrip/ui/strip.py`, `tools/ui_preview.py`
- Test: `tests/test_worker.py`, `tests/test_strip.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Engine:
  - The ledger is built with `prices.potions` and `prices.trash`, and its tally is restored from `data/loot_tally.json` (path next to `prices_path`).
  - Each tick, after pickups: `self._ledger.on_items(reader.items, tracked)`. `tracked` = names in the last confirmed potion counts.
  - The tally is saved whenever `unsold_trash` changes.
  - Prices reload passes `trash`.
  - Item lines are ignored while paused, as pickups are today.
- Strip: the sub-line becomes `Mesos <net> · <mph>/h` (`format_k`, signed, colored as today). The "picked up" and "potions" parts are removed.
- [ ] **Step 1: Failing tests:**
  - `test_engine_credits_trash_line` — a fake `meso_readers` whose pickups reader exposes `items=[L("Tablecloth",2,"Etc")]` → the session net is +68.
  - `test_engine_tally_survives_restart` — engine A credits 68 and shuts down; engine B, on the same data dir, starts with `unsold_trash == 68`.
  - `test_items_ignored_while_paused`.
  - `test_strip_subline_single_total` — the sub-line text matches `^Mesos [+−-]?[\d.]+K? · (—|[+−-]?[\d.]+K?/h)$`, with no "picked up" and no "potions".
- [ ] **Step 2:** Implement, then run the affected tests → PASS, and the full suite → PASS. Regenerate the previews with `tools/ui_preview.py` for the report.
- [ ] **Step 3:** Commit: `feat: engine reads item lines, strip shows one meso total`.

### Task 6: Package, README, live check

**Files:**
- Modify: `expstrip/__main__.py` (`--selftest` also checks `templates/items/` and the suffix templates), `README.md` (he + en: trash credited at pickup, found potions, selling is not counted twice, adding a new item needs a template from a recording), `ExpStrip.spec` (comment only, if the templates folder is already bundled)
- [ ] **Step 1:** Selftest additions. Build: `.venv/Scripts/pyinstaller.exe --noconfirm ExpStrip.spec`. Then `dist/ExpStrip.exe --selftest` → `data/selftest.txt` starts with `selftest OK` and lists the item template counts.
- [ ] **Step 2: Live check (user checkpoint):** 10 minutes on the real game with `dist\ExpStrip.exe --debug`.
  - Pick up trash: the strip net rises by the item's price.
  - Sell the trash at an NPC, then open the inventory: the log shows the reconciliation, the net does not jump, and the tally goes to about 0.
  - Leave the app open at the end, and measure CPU: CPU seconds ÷ uptime ÷ 16 < 1%.
- [ ] **Step 3:** Commit: `build: package stage 2 loot tracking`.
