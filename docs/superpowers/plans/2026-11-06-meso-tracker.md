# Meso Tracker (stage 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live net-meso tracking to EXP Strip. The net is mesos picked up minus potions drunk, plus corrections from the inventory. Show it as option B (a sub-line in the strip) and as a Mesos/hour column in the spots panel, add a reset button for the EXP and meso session together, and save meso fields per visit.

**Architecture:** Three new screen readers, each a pure function of a frame:
- the pickup lines (Windows OCR plus a line tracker that counts each pickup once)
- the quickslot potion counts (icon templates and count glyphs)
- the inventory meso field (anchor template and digit glyphs)

A pure `MesoLedger` turns their outputs into `MesoDelta`s. `Tracker` adds those deltas to the session and to the current visit, alongside EXP. `Engine` schedules the readers on the existing 1 Hz tick. `VisitStore` gains columns, with a one-time upgrade of the old file.

**Tech Stack:** The existing project only: Python 3.12, PySide6, opencv-python, numpy, mss, winsdk OCR, pytest, PyInstaller. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-06-meso-tracker-design.md` (builds on `docs/superpowers/specs/2026-11-04-exp-tracker-design.md`)

**Branch:** create `feat/meso-tracker` from the tip of `feat/exp-strip`.

## Global Constraints

- **Inputs:** screen pixels via desktop capture and user32 window queries only.
- **Never:**
  - open the game process, read its memory, inject or hook
  - send keys or messages to the game
  - touch the network
  - need admin rights
  - use suspicious names ("hack", "bot", "cheat")
- The player never has to do anything for the tool. Opening the inventory is optional and only adds corrections.
- **Prices:** Meat 80, Lemon 150 (the only tracked potions). Trash prices (Tablecloth 34, Stirge Wing 20) are stored but unused in stage 1.
- **Reconciliation tolerance:** |difference| ≤ max(3000, 25% of mesos picked up since the previous exact reading) counts as a correction; anything larger is "other".
- **Potions:**
  - Costed when drunk (the count goes down).
  - A count going up costs nothing; in reconciliation it only explains mesos spent.
- **Cadence:** pickups every tick (OCR only when the region changed); quickslots every 5 s; inventory check every tick.
- **CPU:** the whole app under 1% of a 16-thread CPU on the real game, measured in Task 9.
- **Rate display:** Meso /h is shown only after 60 s of active visit time, else `—`. Numbers use the existing `format_k`.
- **visits.csv new columns, appended in this order:** `mesos_picked, potion_cost, meso_correction, meso_other, mesos_net, reconciliations`, where `mesos_net = mesos_picked − potion_cost + meso_correction`.
  - Old files are upgraded once, with a `.bak` copy and no lost rows.
  - UTF-8 with BOM, append-only after the upgrade.
- **Reset:** zeroes session EXP, 5 min and all session meso numbers.
  - It closes and saves the current visit (≥60 s rule), then starts a new visit on the same map.
  - It never touches the history.
  - A single click must not reset.
- **Strip sub-line copy:** `Mesos {net} · picked up {picked} · potions {−cost} · {rate}/h`. Net is green when positive, red when negative, and muted at 0.
- **Icons are painted** like the existing ⏸/☰, never emoji.
- Every task keeps the full existing test suite green.

## Review Focus

1. **A one-frame potion-count misread** (e.g. 107 read as 1070) must not create a cost or a purchase. A count is used only after two consecutive agreeing reads (Task 4 test).
2. **An inventory reading right after start, resume or reset** becomes a baseline, with no correction or "other" (Task 1 test).
3. **visits.csv locked by Excel during the upgrade:** no crash, no lost rows, retried later (Task 6 test).
4. **A broken prices.json** (typo, a negative or non-numeric price): defaults or the last good values are used, a warning is logged, no crash (Task 1 test).
5. **Two pickups of the same amount in a row, a burst of pickups within one tick, and the top line fading out:** each pickup counted exactly once (Task 3 test).

---

### Task 1: Prices file and the meso ledger (pure logic)

**Files:**
- Create: `expstrip/prices.py`, `expstrip/mesos.py`
- Test: `tests/test_mesos.py`

**Interfaces:**
- Produces:
  - `Prices(potions: dict[str, int], trash: dict[str, int])` (dataclass)
  - `load_prices(path: Path, last_good: Prices | None = None) -> Prices`
  - `DEFAULT_PRICES`
  - `MesoDelta(picked: int = 0, potion_cost: int = 0, correction: int = 0, other: int = 0, reconciled: bool = False)` (dataclass)
  - `MesoLedger(prices: dict[str, int])` with these methods:
    - `.on_pickups(amounts: list[int]) -> MesoDelta`
    - `.on_potion_counts(counts: dict[str, int]) -> MesoDelta`
    - `.on_exact_mesos(mesos: int) -> MesoDelta`
    - `.restart() -> None`: used for resume and reset. It forgets the potion reference and the baseline.
    - `.set_prices(prices: dict[str, int]) -> None`

- [ ] **Step 1: Write the failing tests**

```python
def test_default_prices_file_created(tmp_path):
    p = load_prices(tmp_path / "prices.json")
    assert p.potions == {"Meat": 80, "Lemon": 150}
    assert p.trash == {"Tablecloth": 34, "Stirge Wing": 20}
    assert (tmp_path / "prices.json").exists()

@pytest.mark.parametrize("text", ["{not json", '{"potions": {"Meat": -5}}', '{"potions": {"Meat": "x"}}', "[]"])
def test_broken_prices_keep_last_good(tmp_path, text, caplog):
    f = tmp_path / "prices.json"; f.write_text(text, encoding="utf-8")
    good = Prices({"Meat": 70}, {})
    assert load_prices(f, last_good=good) == good
    assert load_prices(f).potions == DEFAULT_PRICES.potions
    assert "prices" in caplog.text.lower()

def test_pickups_sum():
    assert MesoLedger({"Meat": 80}).on_pickups([12, 11, 12]) == MesoDelta(picked=35)

def test_potion_decrease_costs_increase_does_not():
    L = MesoLedger({"Meat": 80, "Lemon": 150})
    assert L.on_potion_counts({"Meat": 107, "Lemon": 288}) == MesoDelta()   # first read = reference
    assert L.on_potion_counts({"Meat": 105, "Lemon": 288}) == MesoDelta(potion_cost=160)
    assert L.on_potion_counts({"Meat": 205, "Lemon": 287}) == MesoDelta(potion_cost=150)  # +100 bought: no cost

def test_untracked_or_missing_potion_ignored():
    L = MesoLedger({"Meat": 80})
    L.on_potion_counts({"Meat": 10, "Orange": 82})
    assert L.on_potion_counts({"Orange": 70}) == MesoDelta()   # Meat off the keys: nothing guessed

def test_first_exact_is_baseline_then_small_diff_is_correction():
    L = MesoLedger({"Meat": 80})
    assert L.on_exact_mesos(1_000_000) == MesoDelta()
    L.on_pickups([500])
    assert L.on_exact_mesos(1_000_620) == MesoDelta(correction=120, reconciled=True)

def test_purchase_explains_wallet_drop():
    L = MesoLedger({"Meat": 80})
    L.on_potion_counts({"Meat": 10}); L.on_exact_mesos(100_000)
    L.on_potion_counts({"Meat": 110})                         # bought 100 = 8,000
    assert L.on_exact_mesos(92_000) == MesoDelta(reconciled=True)

def test_large_diff_is_other_and_tolerance_scales():
    L = MesoLedger({}); L.on_exact_mesos(0)
    assert L.on_exact_mesos(4_000_000) == MesoDelta(other=4_000_000, reconciled=True)
    L.on_pickups([20_000])                                    # tolerance = max(3000, 5000)
    assert L.on_exact_mesos(4_024_900) == MesoDelta(correction=4_900, reconciled=True)

def test_restart_makes_next_exact_a_baseline():
    L = MesoLedger({}); L.on_exact_mesos(500_000); L.restart()
    assert L.on_exact_mesos(300_000) == MesoDelta()
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_mesos.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `expstrip/prices.py` and `expstrip/mesos.py`.
  - Prices: valid means a JSON object whose `potions`/`trash` are maps from name to a non-negative int. Missing sections fall back to defaults. Log through the `expstrip` logger.
  - Ledger: keep the reconciliation state as `last_exact`, `picked_since` and `bought_value_since`.
- [ ] **Step 4: Run** the full suite. Expected: all pass.
- [ ] **Step 5: Commit** with message `feat: prices file and meso ledger`.

### Task 2: Recording tool and the feasibility recording (user checkpoint)

**Files:**
- Create: `tools/record_meso_session.py`
- Modify: `.gitignore` (add `recordings/`)

**Interfaces:**
- Produces:
  - recordings in `recordings/<name>/`:
    - `pickups_0001.png`… at 1 Hz: a crop of the client area covering its right 40% × bottom 45%
    - `full_0001.png`… every 10 s, plus one at start and one at end
    - `index.csv` with `frame,time,kind`

- [ ] **Step 1: Write the tool.**
  - It reuses `find_game_window` and `grab` from `expstrip`, and `--delay` and the beep from `tools/capture_fixture.py`.
  - Arguments: `name`, `--minutes 5`.
  - It prints the frame count at the end.
  - No game interaction beyond capture.
- [ ] **Step 2: Dry run** with `--minutes 0.2`. Check that the crops show the pickup area and the quickslots, and that the full frames are 2560×1440.
- [ ] **Step 3: User checkpoint (controller).** Ask the player to:
  1. open the inventory
  2. start `tools\record_meso_session.py meso_a --minutes 5`
  3. click into the game and grind normally, with the inventory open for the first ~10 s
  4. reopen the inventory for the last ~10 s

  Record in the ledger the two meso totals visible in the first and last full frames with the inventory showing.
- [ ] **Step 4: Commit** the tool and `.gitignore` with message `tools: meso session recorder`. Recordings stay out of git.

### Task 3: Pickup-line reader, once-only counting, and the feasibility gate

**Files:**
- Create: `expstrip/vision/pickups.py`
- Create: `tests/test_pickups.py`
- Create: `tests/fixtures/pickups/` — a labeled subsequence of ≥60 consecutive crops from `recordings/meso_a`, plus `truth.json` listing each new pickup amount by frame, labeled by eye
- Modify: `expstrip/templates/README.md` (region offsets)

**Interfaces:**
- Consumes: `ocr_lines(img) -> list[str]` (`expstrip.vision.ocr`); `Regions.scale` (`expstrip.vision.layout`)
- Produces:
  - `parse_pickup(text: str) -> int | None`
  - `pickup_rect(frame: np.ndarray, scale: float) -> Rect | None`
  - `read_pickup_lines(crop: np.ndarray, scale: float) -> list[tuple[int, float]]`: amounts with each line's center y, top to bottom
  - `PickupCounter()` with `.feed(lines: list[tuple[int, float]]) -> list[int]`, which returns only new pickups, and `.reset()`
  - `region_changed(prev: np.ndarray | None, cur: np.ndarray) -> bool`

- [ ] **Step 1: Write the failing tests.**

```python
@pytest.mark.parametrize("text,val", [("You have gained mesos (+12)", 12), ("You have gained mesos (+1,203)", 1203),
                                      ("You have gained an item (Lemon)", None), ("You have gained mesos (+1", None)])
def test_parse_pickup(text, val): assert parse_pickup(text) == val

def test_counter_equal_amounts_counted_twice():
    c = PickupCounter()
    assert c.feed([(13, 100.0)]) == [13]
    assert c.feed([(13, 80.0), (13, 100.0)]) == [13]       # older line moved up, a new +13 below

def test_counter_burst_and_fade():
    c = PickupCounter()
    c.feed([(12, 60.0), (11, 80.0), (12, 100.0)])
    assert c.feed([(11, 40.0), (12, 60.0), (10, 80.0), (10, 100.0)]) == [10, 10]   # top faded, two new
    assert c.feed([(11, 40.0), (12, 60.0), (10, 80.0), (10, 100.0)]) == []          # unchanged frame

def test_fixture_sequence_matches_truth():
    # replays tests/fixtures/pickups/*.png through pickup_rect/read_pickup_lines/PickupCounter
    # and asserts the counted amounts equal truth.json per frame
    ...
```

- [ ] **Step 2: Run** the new tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The region is fixed relative to the client's bottom-right; derive its offsets from the recordings and record them in `templates/README.md`.
  - Before OCR, keep only bright low-saturation text pixels and upscale 2×; tune on the recordings.
  - The counter aligns the previous and current line lists by amount sequence and y-shift. Its rules:
    - A line that keeps its y with an unchanged amount is old.
    - A shift up by one line height means new lines arrived at the bottom.
- [ ] **Step 4: Feasibility gate (controller).**
  1. Replay all of `recordings/meso_a` through the reader.
  2. Compute Σ pickups.
  3. Compare it with the inventory difference from Task 2 Step 3, adjusted for potions bought during the recording: there should be none, so ask the player.
  4. Ledger the ratio.
  5. If below 0.90, **stop and report to the player** (spec §7).
- [ ] **Step 5: Run** the full suite, then commit with message `feat: pickup-line reader with once-only counting`.

### Task 4: Quickslot potion counts

**Files:**
- Create: `expstrip/vision/quickslots.py`
- Create: `expstrip/templates/potions/Meat.png`, `expstrip/templates/potions/Lemon.png`, `expstrip/templates/count/*.png`
- Modify: `expstrip/vision/glyphs.py` (new mode `"count"`: white digits with a dark outline over an icon)
- Test: `tests/test_quickslots.py`
- Create: `tests/fixtures/meso_full_*.png`, 3–4 full frames from the recording, plus entries in a `tests/fixtures/meso_truth.json`

**Interfaces:**
- Produces:
  - `read_potion_counts(frame: np.ndarray, scale: float, names: list[str]) -> dict[str, int]`: only potions found on keys, counted once even if on two keys
  - `CountConfirmer()` with `.feed(counts: dict[str, int]) -> dict[str, int]`: returns a potion's count only when it equals the previous read

- [ ] **Step 1: Write the failing tests.**

```python
@pytest.mark.parametrize("name", MESO_FIXTURES)
def test_counts_match_truth(name):
    assert read_potion_counts(load(name), 1.0, ["Meat", "Lemon"]) == TRUTH[name]["potions"]  # e.g. {"Meat": 107, "Lemon": 288}

def test_blank_frame_gives_no_counts():
    assert read_potion_counts(np.zeros((1440, 2560, 3), np.uint8), 1.0, ["Meat"]) == {}

def test_one_frame_misread_not_used():
    c = CountConfirmer()
    assert c.feed({"Meat": 107}) == {}
    assert c.feed({"Meat": 107}) == {"Meat": 107}
    assert c.feed({"Meat": 1070}) == {}
    assert c.feed({"Meat": 106}) == {}
    assert c.feed({"Meat": 106}) == {"Meat": 106}
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Cut the icon and count templates from the recording with `tools/extract_templates.py` (extend it).
  - Find icons with `anchors.find` near `scale` inside the bottom-right quarter.
  - Read the count from the icon's bottom-left corner.
- [ ] **Step 4: Run** the full suite, then commit with message `feat: quickslot potion counts`.

### Task 5: Inventory meso reader

**Files:**
- Create: `expstrip/vision/inventory.py`, `expstrip/templates/anchor_mesos.png`, `expstrip/templates/mesos/*.png`
- Modify: `expstrip/vision/glyphs.py` (new mode `"mesos"`: dark digits on a white field)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Produces:
  - `read_inventory_mesos(frame: np.ndarray, scale: float) -> int | None`: None when the inventory isn't visible
  - `MesosConfirmer` with `.feed(v: int | None) -> int | None`: returns a value only on two consecutive equal reads

- [ ] **Step 1: Write the failing tests.**

```python
@pytest.mark.parametrize("name", [n for n in MESO_FIXTURES if TRUTH[n]["mesos"] is not None])
def test_mesos_read(name):
    assert read_inventory_mesos(load(name), 1.0) == TRUTH[name]["mesos"]          # e.g. 1039695

def test_inventory_anywhere():
    f = load(FIRST_WITH_INVENTORY)
    moved = shift_inventory(f, dx=-300, dy=400)   # test helper: cut the window and paste it elsewhere on the same frame
    assert read_inventory_mesos(moved, 1.0) == TRUTH[FIRST_WITH_INVENTORY]["mesos"]

def test_no_inventory_gives_none():
    assert read_inventory_mesos(load(FIRST_WITHOUT_INVENTORY), 1.0) is None
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Single-scale match of `anchor_mesos` at `scale` over the whole frame.
  - The digit field sits left of the anchor. Strip the commas.
  - Time one call on a 2560×1440 frame and log it in the report; target ≤ 15 ms.
- [ ] **Step 4: Run** the full suite, then commit with message `feat: inventory meso reader`.

### Task 6: Visit meso fields, session stats, reset, and CSV upgrade

**Files:**
- Modify: `expstrip/tracker.py`, `expstrip/history.py`
- Test: `tests/test_tracker.py`, `tests/test_history.py`

**Interfaces:**
- Consumes: `MesoDelta` (Task 1)
- Produces:
  - `Visit` gains `mesos_picked`, `potion_cost`, `meso_correction`, `meso_other`, `reconciliations`, each `int | None = None`. New visits start them at 0; None means a visit saved before this feature. Property `Visit.mesos_net -> int | None`.
  - `Tracker` gains:
    - `.add_mesos(d: MesoDelta, now: float) -> None`, ignored while paused
    - `.reset(now: float) -> None`, which closes the visit through `on_visit_closed` (≥60 s rule), zeroes the session, and opens a new visit on the same map
  - `Stats` gains `mesos_net: int`, `mesos_picked: int`, `potion_cost: int` and `mesos_per_hour: float | None` (visit net ÷ visit active time, ≥60 s).
  - `SpotSummary` gains `mesos_per_hour: float | None`, computed over visits that have meso data only.
  - `VisitStore.__init__` upgrades an old-header CSV once.

- [ ] **Step 1: Write the failing tests.**

```python
def test_mesos_credit_session_and_visit():          # tracker
    t = make_tracker_on_map_a()
    t.add_mesos(MesoDelta(picked=500, potion_cost=160, correction=20), now=100)
    t.add_mesos(MesoDelta(other=4_000_000), now=101)
    s = t.stats(101)
    assert (s.mesos_net, s.mesos_picked, s.potion_cost) == (360, 500, 160)

def test_reset_closes_visit_and_zeroes_session():
    closed = []; t = make_tracker_on_map_a(on_closed=closed.append, active_seconds=120, exp_gain=900)
    t.reset(now=200)
    assert len(closed) == 1 and closed[0].exp_gained == 900
    s = t.stats(201)
    assert s.session == 0 and s.last5 == 0 and s.mesos_net == 0 and s.map == MAP_A

def test_paused_ignores_mesos():
    t = make_tracker_on_map_a(); t.pause(10)
    t.add_mesos(MesoDelta(picked=50), 11)
    assert t.stats(12).mesos_picked == 0

def test_old_csv_upgraded_with_backup(tmp_path):     # history
    write_old_csv(tmp_path, rows=3)
    store = VisitStore(tmp_path)
    assert (tmp_path / "visits.csv.bak").exists()
    vs = store.load()
    assert len(vs) == 3 and vs[0].mesos_picked is None
    store.append(visit_with_mesos(picked=500, cost=160, corr=20))
    header = read_header(tmp_path / "visits.csv")
    assert header[-6:] == ["mesos_picked", "potion_cost", "meso_correction", "meso_other", "mesos_net", "reconciliations"]
    assert store.load()[-1].mesos_net == 360

def test_upgrade_when_locked_keeps_rows(tmp_path, monkeypatch):
    write_old_csv(tmp_path, rows=3); make_replace_raise_permission_error(monkeypatch)
    store = VisitStore(tmp_path)                       # no crash
    assert len(store.load()) == 3                       # old rows still readable
    # the next append retries the upgrade (spec: no lost rows)

def test_summary_meso_rate_skips_old_visits(tmp_path):
    ...  # one old visit with no meso data + one new visit with 3600 s and net 41000 -> mesos_per_hour == 41000
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The upgrade writes to a temp file, then `os.replace`, keeping the BOM.
  - `current_visit.json` carries the new fields; recovery tolerates them missing.
- [ ] **Step 4: Run** the full suite, then commit with message `feat: meso fields per visit, session reset, csv upgrade`.

### Task 7: Engine wiring

**Files:**
- Modify: `expstrip/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5 and 6.
- Produces:
  - `Engine(..., prices_path: Path | None = None, meso_readers: MesoReaders | None = None)`, where `MesoReaders` is a dataclass of three callables (pickups, potions, inventory) so tests can inject fakes
  - `Engine.reset() -> None`, which takes the lock and calls `Tracker.reset`, `MesoLedger.restart` and `PickupCounter.reset`
  - `Engine.resume()` also calls `MesoLedger.restart`

- [ ] **Step 1: Write the failing tests.**

```python
def test_engine_counts_pickups_and_potions(tmp_path):
    e = make_engine(tmp_path, reads=steady_gain(MAP_A, 30),
                    meso=FakeMeso(pickups={3: [12], 4: [11]}, potions={0: {"Meat": 10}, 5: {"Meat": 10}, 10: {"Meat": 9}, 15: {"Meat": 9}}))
    for _ in range(20): snap = e.tick()
    assert (snap.stats.mesos_picked, snap.stats.potion_cost) == (23, 80)

def test_engine_quickslots_every_5_ticks(tmp_path):
    fm = FakeMeso(); e = make_engine(tmp_path, reads=steady_gain(MAP_A, 20), meso=fm)
    for _ in range(20): e.tick()
    assert fm.potion_calls == 4

def test_engine_reset_zeroes_and_saves_visit(tmp_path):
    ...  # 120 ticks of gain on MAP_A, reset(), next snapshot: session 0, mesos_net 0, visits.csv has 1 row

def test_prices_reloaded_when_file_changes(tmp_path):
    ...  # edit prices.json mid-run (Meat 80 -> 100); the next potion decrease costs 100
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Each tick, under the lock and only when the game window is found and the frame isn't blank:
    - pickups: crop, then `region_changed`, then OCR, then counter, then ledger, then tracker
    - every 5th tick, quickslots: `CountConfirmer`, then ledger
    - inventory: `MesosConfirmer`, then ledger
  - Reload prices when the file's mtime changes, using `load_prices(path, last_good)`.
  - Every reader call is wrapped like the existing `_safe`, so a reader failure never stops EXP tracking.
  - Log each reconciliation (expected, actual, difference, and whether it became a correction or "other") through the `expstrip` logger.
  - The meso state is not touched while paused.
- [ ] **Step 4: Run** the full suite, then commit with message `feat: engine runs meso readers`.

### Task 8: UI — strip sub-line, reset button, Mesos/hour column

**Files:**
- Modify: `expstrip/ui/strip.py`, `expstrip/ui/spots_panel.py`, `expstrip/ui/theme.py`, `expstrip/__main__.py`, `expstrip/ui/tray.py` (add a "Reset session" menu item too)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `Stats` meso fields (Task 6); `Engine.reset` (Task 7)
- Produces:
  - `StripWindow.reset_requested: Signal()`
  - `StripWindow.reset_button`: the painted reset `_IconButton`, kind `"reset"`
  - `StripWindow.meso_text() -> str` (plain text of the sub-line, for tests)
  - `StripWindow.meso_net_style() -> str`: the net value's stylesheet, for color tests
  - `SpotsPanel.row_texts()` rows gain a Mesos/hour cell after EXP/hour

- [ ] **Step 1: Write the failing tests.**

```python
def test_meso_line_text_and_color(qtbot):
    strip.apply(snapshot(mesos_net=12_400, mesos_picked=15_600, potion_cost=3_200, mesos_per_hour=41_000))
    assert strip.meso_text() == "Mesos +12.4K · picked up +15.6K · potions -3.2K · 41K/h"
    assert theme.POSITIVE in strip.meso_net_style()

def test_meso_rate_dash_before_60s(qtbot):
    strip.apply(snapshot(mesos_net=-800, mesos_picked=0, potion_cost=800, mesos_per_hour=None))
    assert strip.meso_text().endswith("—/h") and theme.NEGATIVE in strip.meso_net_style()

def test_single_click_does_not_reset(qtbot):
    with qtbot.assertNotEmitted(strip.reset_requested): qtbot.mouseClick(strip.reset_button, Qt.LeftButton)

def test_second_click_within_3s_resets(qtbot):
    qtbot.mouseClick(strip.reset_button, Qt.LeftButton)
    with qtbot.waitSignal(strip.reset_requested): qtbot.mouseClick(strip.reset_button, Qt.LeftButton)

def test_confirm_state_expires(qtbot):
    qtbot.mouseClick(strip.reset_button, Qt.LeftButton); qtbot.wait(3100)
    with qtbot.assertNotEmitted(strip.reset_requested): qtbot.mouseClick(strip.reset_button, Qt.LeftButton)

def test_panel_meso_column(qtbot):
    panel.apply([summary("Monkey Swamp I", exp=98_200, mesos=41_000), summary("Old", exp=10_000, mesos=None)], None)
    assert [r[2] for r in panel.row_texts()] == ["+41K", "—"]
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Follow the option B mockup `docs/reference/mockup-mesos-option-b.html`.
  - Add `theme.POSITIVE = "#2f9e44"` and `theme.NEGATIVE = "#d6336c"`.
  - The reset icon is a painted circular arrow. In its confirm state it turns orange with the tooltip "Click again to reset".
  - Wire `reset_requested` and the tray item to `Engine.reset` with a queued call to the worker, the same way as pause.
  - Update `tools/ui_preview.py` so the new line can be checked visually; the controller checks the render.
- [ ] **Step 4: Run** the full suite, then commit with message `feat: meso line, reset button, Mesos/hour column`.

### Task 9: Packaging, CPU measurement, and live check (user checkpoint)

**Files:**
- Modify: `ExpStrip.spec` (bundle `templates/potions`, `templates/count`, `templates/mesos`, `anchor_mesos.png`)
- Modify: `README.md` (Hebrew and English: what the meso line means, prices.json, reset)
- Modify: `expstrip/__main__.py` `--selftest` (also loads the new templates)

- [ ] **Step 1: Build** with `.venv/Scripts/pyinstaller --noconfirm ExpStrip.spec`, then run `dist/ExpStrip.exe --selftest`. Expected: `selftest OK`.
- [ ] **Step 2: User checkpoint (controller).** The player runs `dist\ExpStrip.exe --debug` for 10 minutes of grinding, opening the inventory at the start and the end. Check:
  - The meso line updates live.
  - Opening the inventory reconciles with a small correction (log line).
  - The reset button needs two clicks.
  - The Mesos/hour column appears after the visit closes.
- [ ] **Step 3: Measure CPU** over 10 minutes of that run, with `Get-Process ExpStrip` CPU seconds ÷ wall seconds ÷ 16. Expected: under 1%. Ledger the number. If over, throttle the inventory check to every 2 s and re-measure.
- [ ] **Step 4: Commit** with message `build: package meso tracker`.
