# Meso tracker for EXP Strip — design

Date: 2026-11-06
Status: draft, awaiting approval
Builds on: `docs/superpowers/specs/2026-11-04-exp-tracker-design.md` (the EXP Strip spec). Everything there still holds, above all §4 Anti-goals. This document only adds to it.

## 1. Intent

While grinding, the player wants to know **whether they are making or losing mesos, and by how much**. The money comes in as mesos picked up off the floor, and goes out as potions drunk. They also want to compare training spots by **mesos per hour**, next to the EXP/hour the tool already shows.

This is **stage 1** of a larger meso picture:

- **Stage 1 (this document):** mesos + potions.
- **Stage 2 (later, separate design):** trash loot that the player sells to NPCs, valued from a price list.
- **Later still (separate project):** a web dashboard with charts and analysis, built on the saved history.

## 2. Goals

- Show, live, the session's **net mesos**: mesos picked up minus the value of potions drunk, plus corrections (§6.4). Show it as positive (green) or negative (red).
- Show the breakdown: picked up, potions, and mesos per hour at the current spot.
- Add a **Mesos/hour** column per spot to the spots panel.
- Correct the live number to the exact value whenever the player happens to open the inventory.
- Save the meso numbers per visit, so stage 2 and the later web dashboard can use them.
- Let the player edit the price list in a plain file next to the app.

## 3. Non-goals (stage 1)

- Trash loot and any other items: not counted at all in stage 1.
- Rare or valuable drops: never counted, in any stage. They are random, and one lucky drop would distort a spot.
- Player trades, the Free Market, equipment purchases, quest rewards: not modeled (see §6.4 for how they're kept out).
- Potion purchases as an expense: a potion costs money **when it is drunk**, not when it is bought.

## 4. Anti-goals

The same as the EXP spec §4, with no exceptions:

- Screen pixels and user32 window queries only.
- No process access, no input to the game, no network.
- No admin rights, no suspicious names.

**The player never has to do anything for the tool.** It must not require opening the inventory, pressing a key, or arranging windows. Opening the inventory only *improves* accuracy (§6.4).

Keep CPU use low. The player asked explicitly that it not "load" the computer. Current measured load: about 0.3% of a 16-thread CPU. The target after this feature is under 1%, and it must be measured on the real game before finishing.

## 5. What the screen shows

These are the player's own screenshots, at 2560×1440 but shown downscaled:

- `docs/reference/game-inventory-quickslots-pickups.webp` — the full game screen with the inventory open, the quickslots, and the pickup lines.
- `docs/reference/pickup-lines-closeup.png` — close-up of the pickup lines.

**Pickup lines.** "You have gained mesos (+12)", one line per pickup.
- Fixed place: the bottom-right of the game area, above the quickslots and below the party HP list.
- The text is white with a dark outline, partly transparent. It sits over a moving, varied background.
- New lines appear at the bottom and older ones move up and fade out after a few seconds. About five are visible at once.
- The oldest line can overlap the party HP list.

**Quickslots.** A 4×3 grid of keys at the bottom-right, left of the Cash Shop / Menu / Shortcut buttons.
- A potion key shows the potion's icon with a small count in its bottom-left corner. The digits are white with a dark outline, and the count is the total of that potion across all inventory stacks. For example, Meat = 107 = 13 + 94, and Lemon = 288 = 95 + 100 + 93.
- On the reference screenshot Meat and Lemon are on the second row. The player may move them.

**Inventory window** ("ITEM INVENTORY", tabs Equip/Use/Set Up/Etc/Cash/Deco).
- The player can drag it anywhere on screen.
- At its bottom there's a white field with the meso total and the label "MESOS", for example "1,039,695". The field is shown on every tab.

## 6. How it works

### 6.1 Price list

A file `data/prices.json` next to the app, which the player edits:

```json
{
  "potions": { "Meat": 80, "Lemon": 150 },
  "trash":   { "Tablecloth": 34, "Stirge Wing": 20 }
}
```

- Potions are the shop prices the player pays: Meat 80 and Lemon 150 (from `docs/reference/shop-meat-price.png` and `shop-lemon-price.png`). Only these two potions are tracked for now. Other potions on the keys (the orange and blue ones) are ignored until the player adds them.
- The `trash` section is stored for stage 2 and is **not used in stage 1**. Prices: Tablecloth 34 and Stirge Wing 20, from the NPC shop screenshots `shop-tablecloth-price.png` and `shop-stirge-wing-price.png`. *(Designer's call: these are the prices the shop shows. The player sells these items there.)*
- If the file is missing, create it with these defaults. If it's invalid, keep the last good values and log a warning; never crash.

### 6.2 Mesos picked up (live)

- The tool reads the pickup-line region each tick (about 1 Hz). To keep the load low, it runs text recognition **only when that region changed** since the last tick.
- It parses lines of the form `You have gained mesos (+N)`. Each new pickup adds `+N` to "picked up".
- **Every pickup is counted exactly once.** Lines stay on screen for several ticks and move up as new lines arrive. Two pickups of the same amount, such as +13 and +13, are normal and must not merge into one. How to track lines across ticks is left to the builder, but it must be tested against recorded sequences of real frames.
- Other line types (items, EXP, quest messages) are ignored in stage 1.

### 6.3 Potions (live)

- Every 5 s the tool looks at the quickslot grid. It finds each tracked potion by its icon and reads the count on that key.
- **A decrease** in a potion's count means potions were drunk: cost = units × price.
- **An increase** means potions were bought, or picked up as a drop. It adds no value and no cost live; see §6.4 for how it's used during reconciliation.
- **A potion that isn't on any key** cannot be tracked live. Show nothing for it and don't guess.
- The same potion on two keys shows the same total twice. Count it once.

### 6.4 Exact check when the inventory is open (reconciliation)

- Each tick, a cheap check looks for the inventory window anywhere on screen. When it is visible, the tool reads the exact meso total from it.
- **Starting point:** the first exact reading of a session becomes the **baseline**. The meso total itself is not needed for the net; only the changes are.
- **On each later exact reading:**
  - expected = previous exact mesos + mesos picked up since then − (potion increases since then × price). The last term is an estimate of what was spent buying potions.
  - difference = actual − expected
  - **Small difference**: |difference| ≤ max(3,000, 25% of the mesos picked up since the previous exact reading) *(designer's call)*. It scales so that a long stretch between inventory opens, with a missed burst of pickups, still counts as a correction. It is treated as missed pickups or rounding, and is added to the net as a **correction**.
  - **Large difference**: something unrelated to training happened (a trade, a sale, an item purchase). It is recorded as **"other"** and **not** added to the net or to any spot.
- **Potion purchases are never an expense**, live or in reconciliation. Buying potions moves mesos into potions of the same value. The `− potion increases × price` term only explains why the wallet went down, so the purchase does not show up as a difference. The cost is counted when the potion is drunk (§6.3).
- **Which visit gets a correction or "other":** the visit of the map where the exact reading happens, even if the window since the previous reading spanned several maps *(designer's call)*.
- **While paused:** no reconciliation is credited. After resume, the next exact reading becomes a fresh baseline *(designer's call)*.
- **No "approximate" marker** on the strip in stage 1. The live number is shown as-is, before the first inventory check too *(designer's call; keep the strip simple)*.
- Keep a running count of reconciliations and their differences. That shows how reliable the pickup lines really are; log it, and save it per visit (§6.6).
- Stage 1 does not model trash sales. Selling trash will therefore usually show up as a large difference and land in "other". *(Designer's call; stage 2 replaces this.)*

### 6.5 What the player sees

The chosen layout is **option B**, mockup `docs/reference/mockup-mesos-option-b.html`.

- **A new small line inside the strip, under the progress bar:** `Mesos +12.4K · picked up +15.6K · potions −3.2K · 41K/h`
  - Mesos = session net. It is green when positive, red when negative, and neutral at 0.
  - Picked up = session sum of meso pickups.
  - Potions = session potion cost (shown negative).
  - /h = net mesos per active hour at the current spot visit. Show it only after 60 s of active time, like EXP/hour; until then show `—`.
  - Use the same number formatting as the existing strip values.
- **The spots panel** gets a **Mesos/hour** column after EXP/hour. It is signed and colored: Σ net mesos ÷ Σ active hours, over all-time, per spot. It uses the same active-hours denominator as EXP/hour. Visits saved before this feature have no meso data. Exclude them from the meso average and show `—` for a spot with no meso data at all.
- **The strip grows taller by one line.** Keep the existing style. The look must still stay clearly different from the prior-art tracker.
- **Pause (⏸)** also pauses meso tracking. While paused, pickups and potion changes are ignored. On resume, re-read the potion counts as a new reference.

### 6.6 Session, visits and history

- **Session** = since the app started, or since the last **reset**.
- **Reset button (new, the player asked for it):** a small reset control on the strip, next to ⏸ and ☰. It starts a new session for EXP **and** mesos together:
  - Session EXP, 5 min, session meso net, picked up and potion cost all go back to zero.
  - The current visit is closed and saved (if it reached the 60 s minimum), and a new visit starts on the same map *(designer's call)*. That way the history stays consistent.
  - The potion counts on the keys are re-read as the new reference.
  - The next exact inventory reading becomes the new baseline. Opening the inventory after a reset is **not** required; it only enables corrections.
  - The spots history is never touched by a reset.
  - The control must be hard to hit by accident (for example, a confirm click or a press-and-hold) *(designer's call)*.
- **Per visit**, meso pickups, potion cost and corrections go to the visit of the current map, the same way EXP gain does. "Other" is recorded but not credited to the spot.
- **New columns in `data/visits.csv`:** `mesos_picked, potion_cost, meso_correction, meso_other, mesos_net, reconciliations`
  - `mesos_net = mesos_picked − potion_cost + meso_correction`
  - The file already has rows with the old header. Upgrade it once: rewrite the file with the new header and keep every old row, leaving the new fields empty for them. Back up the original file first (e.g. `visits.csv.bak`). After the upgrade, the file is append-only again.
  - Keep UTF-8 with BOM.
- The in-progress file (`current_visit.json`) also carries the meso fields.
- The history must stay easy to load later, for the web dashboard: one row per visit and plain numbers.

## 7. Testing

- **Before building (feasibility test):** run a 5-minute recording on the player's real game.
  - The player opens the inventory at the start and at the end.
  - Desktop captures are taken at 1 Hz and kept as test fixtures.
  - Sum the pickups read from the lines and compare with the actual change in mesos in the inventory.
  - The result decides whether the pickup lines are good enough as the live source. This is step 1 of the plan. If the lines capture less than 90% of the real change, stop and report to the player before building the rest *(designer's call)*; the reconciliation in §6.4 still keeps the session number correct whenever they open the inventory.
- Fixture-based tests for: pickup-line parsing and once-only counting (including two equal amounts in a row), quickslot icon and count reading, inventory meso reading at different window positions, and the reconciliation math (small difference vs large difference).
- The existing tests must all still pass. Old `visits.csv` files must upgrade without losing rows.

## 8. Left to the builder

- How to find the pickup region and the quickslot grid relative to the existing anchors (the EXP/LV status bar, or the Cash Shop/Menu buttons).
- How to read the text: Windows OCR (already used for map names), or digit templates for the counts and the meso total. The meso field and the key counts use small fixed fonts, which suits the glyph approach already used for EXP.
- How to track pickup lines across ticks so each is counted once.
- Icon templates for Meat and Lemon, cut from the player's screenshots. Like the EXP reading, all new regions, glyphs and icons must work across window sizes and UI scale (multi-scale matching relative to the found anchors, as in the EXP spec).
- Exact styling of the new line and the reset control, within the existing theme. The reset icon must be painted like the existing ⏸/☰ icons, not an emoji.

## 9. Open risks

- **Pickup lines may be missed** when many pickups happen at once, or when they fade over a bright background. The §7 feasibility test measures this; reconciliation limits the damage.
- **Quickslot counts** are small outlined digits over icons. They may need their own glyph set.
- **The inventory window may partly cover other regions** (it can be dragged over the minimap or the status bar). The existing EXP reading must keep working when it does.
