# Meso tracker stage 2: trash loot and found potions — design

Date: 2026-11-06
Status: approved 2026-11-06
Builds on:
- `docs/superpowers/specs/2026-11-04-exp-tracker-design.md` (EXP Strip). Its §4 Anti-goals still hold in full.
- `docs/superpowers/specs/2026-11-06-meso-tracker-design.md` (meso stage 1).

This document only adds to those two, or changes them where it says so.

## 1. Intent

Stage 1 shows the session's net mesos from mesos picked up and potions drunk. The player also picks up **trash loot** that they later sell to an NPC, and sometimes **potions dropped by mobs**. Today neither shows in the net until much later, if ever.

The player wants the strip to show what a round is *really* worth, the moment it happens. In the player's words: when they pick up a Tablecloth, it should simply add its value "as if I sold it". A Lemon found on the floor should be +150, and drinking it later −150.

The player does not care where the number comes from. They want one total: positive or negative.

## 2. Goals

- **Trash loot.** When an item from the price list's `trash` section is picked up, add its sell price × quantity to the net at once.
- **Found potions.** When a potion from the price list's `potions` section is picked up from the floor, add its shop price × quantity to the net at once. Drinking it still costs its price, as in stage 1. A found potion that is later drunk therefore nets to 0.
- **Selling the trash must never be counted twice.** The trash value was already credited at pickup, so the mesos received when selling it must not be added again. This must work without any action from the player.
- **The strip shows one total.** The breakdown is no longer shown on the strip; it is still saved per visit.
- **Per-visit history** gets the new numbers, so the web dashboard can use them later.

## 3. Non-goals

- Items that are not in the price list: not counted. This includes equipment, scrolls and rare drops, as in stage 1 §3.
- Reading the Etc or Use tab of the inventory to count items.
- Market prices or price history. The price is whatever the player wrote in `prices.json`.

## 4. Anti-goals

The same as stage 1 §4, without exception:

- Screen pixels and user32 window queries only.
- No process access, no input, no network.
- No admin rights, no suspicious names.
- The player never has to do anything for the tool. No reset after selling, and no key presses.
- CPU stays under 1% of a 16-thread CPU, measured on the real game. Stage 1 measured 0.58%.

## 5. What the screen shows

The item pickup lines appear in the same stack as the meso lines: bottom-right, right-aligned, white text with a dark outline, fading. Examples seen on the player's screen (`docs/reference/game-inventory-quickslots-pickups.webp`, `pickup-lines-closeup.png`, and the live run of 2026-11-06):

- `Tablecloth x1 earned. (Etc)`
- `Animal Fur Bun... x1 earned. (Etc)` — long names are cut with "..."
- `Lemon x1 earned. (Use)` — the player confirmed this format for dropped potions

Other lines in the same stack, which must not be confused with these: `You have gained mesos (+40)`, `You received EXP (+37)`, quest messages.

## 6. How it works

### 6.1 Price list

`data/prices.json` is unchanged in format. Stage 1 reserved the `trash` section; stage 2 uses it.

```json
{
  "potions": { "Meat": 80, "Lemon": 150 },
  "trash":   { "Tablecloth": 34, "Stirge Wing": 20 }
}
```

- `trash` holds NPC **sell** prices; `potions` holds shop **buy** prices.
- A found potion is credited at its `potions` price.
- The player extends the list as they fight new mobs. See §6.6 for what a new name needs.

### 6.2 Reading item lines (live)

- The tool reads item lines from the same region and on the same cadence as the meso lines: each tick, only when the region changed.
- It parses `<Name> x<N> earned. (<Tab>)`, where Tab is Etc or Use. If Name is in the matching price section, it credits price × N. Anything else is ignored.
- **Every item line is counted exactly once.** This is the same requirement and the same difficulty as for meso lines in stage 1 §6.2, including two equal lines in a row: two Tablecloths picked up one after the other are 2, not 1.
- A name cut with "..." is matched by its visible start, but only when that start is unique among the price list's names *(designer's call)*.
- **Rule for found potions:** a potion credited from an item line also raises the count on its quickslot key. Stage 1 treats a count increase as a purchase, which is free live and an expected mesos outflow in reconciliation (stage 1 §6.4). An increase that an item line already explained must **not** also be booked as a purchase. Otherwise the next inventory check would expect mesos that were never spent.
  - *(designer's call)* Each potion keeps a pool of **found units not yet seen on the key**. An item line adds N to it.
  - A key increase first uses up the pool, and only the rest is a purchase: purchase units = max(0, increase − pool).
  - The pool stays until used up, so the two may arrive in either order. A key increase seen before its item line is booked as a purchase. The item line that follows still credits the potion's value. The next inventory check then shows a small positive difference, which is booked as a correction. This is rare and harmless.
  - The pool is cleared on reset and on resume.
- **A found potion is credited only if it is tracked on a quickslot key** (stage 1 §6.3) at that moment *(designer's call)*. If it isn't on a key, its drinking is never seen, and crediting its pickup would inflate the net for good.
- **Names cut with "...":** the visible start must be at least 6 letters and unique among the price list's names. Otherwise the line is dropped *(designer's call)*.
- **While paused:** item lines are ignored, like meso lines. If trash picked up while paused is sold later, it shows up at the inventory check as a correction or "other" *(designer's call)*.

### 6.3 Not counting the sale twice: the "unsold trash" tally

- The tool keeps a running value, **unsold trash**:
  - Every trash credit adds to it.
  - It is never shown on the strip.
  - It is saved in `data/loot_tally.json` whenever it changes, so it survives a restart (`current_visit.json` is deleted on every clean shutdown, so it cannot carry it).
- At each exact inventory reading (stage 1 §6.4), the difference beyond expected is handled in this order:
  1. **Sale of already-counted trash.** A positive difference, up to the unsold-trash tally, is treated as the trash being sold. The tally goes down by that amount, and nothing is added to the net.
  2. Whatever is left over is handled exactly as in stage 1. Small means a correction; large means "other", which is not credited.
- The player usually sells all the trash at once, so the difference and the tally will mostly match closely. A partial sale works the same way.
- **Step 1 runs before the stage 1 tolerance check** *(designer's call)*. A full sale is often smaller than the 3,000 tolerance, and would otherwise be booked as a correction: counted twice.
  - The cost: while the player carries unsold trash, a positive difference from *missed meso pickups* is taken as a sale. The tally then drops too early.
  - This heals itself at the real sale. The tally is smaller than the sale, and the extra goes to step 2 as a correction.
- **Opening the inventory after selling is not required.** The trash value is already in the net from pickup time, so the strip is right without it. The tally only matters at the next exact reading, whenever that happens. If a gear purchase happens in the same shop visit, it lands in the same difference and partly offsets the sale. That is accepted.
- **Selling at a price different from the list:**
  - Getting less than listed: the tally keeps a small remainder, and it is absorbed by the next sale.
  - Getting more than listed: the extra falls through to step 2, as a small correction.
  - *(designer's call)*
- **Reset (stage 1 §6.6):** reset starts a new session and zeroes the net, but **keeps** the unsold-trash tally. The player still holds that trash, and its value was credited to the session before the reset. Without this, selling after a reset would land as a large "other" or as a correction *(designer's call)*.

### 6.4 What the player sees

- **This changes stage 1 §6.5.** The sub-line under the progress bar becomes `Mesos +12.4K · 41K/h`:
  - It is one total: mesos picked up + trash value + found potions − potions drunk + corrections.
  - Green when positive, red when negative, neutral at 0. `/h` is shown after 60 s of active time, as today.
  - The "picked up" and "potions" parts are removed from the strip.
- The spots panel's **Mesos/hour** column uses the same total.

### 6.5 History

- New columns in `data/visits.csv`, after the stage 1 ones: `loot_value, found_potions_value`.
- The formula becomes `mesos_net = mesos_picked + loot_value + found_potions_value − potion_cost + meso_correction`.
- The sale of trash that was already counted is not a column of its own. It only reduces the tally.
- Upgrade the file once, the same way stage 1 did: back it up, keep every row, and leave the new fields empty for old rows. Rows without the new fields count them as 0.
- Stage 1 rows, which have meso fields but no loot fields, stay in the Mesos/hour average with loot = 0. Rows from before stage 1 stay excluded, as today.
- `current_visit.json` also carries the new fields.

### 6.6 New items

- To track a new trash item, the player adds its name and price to `prices.json`.
- If reading a name needs per-name templates (see §8), the tool cannot read a new name until it has learned it. The design must then provide a simple way to learn it from a short recording of the player picking the item up. A developer step, such as running a tool on that recording, is acceptable in stage 2 *(designer's call)*.
- Until a name can be read, its pickups are simply not counted. They reach the net only through the next sale, as a correction or "other".
- As shipped (2026-11-07) no potion name template exists yet: found potions are credited once one is learned from a recording.

## 7. Testing

- **Before building (feasibility test):** this is step 1 of the plan, and it is a stop gate. No recording with item lines and known counts exists yet: `recordings/meso_a` and `meso_b` hold item lines only by chance, with no truth for them. The player records a new one with `tools/record_meso_session.py`, the same way as in stage 1. The session is about 5 minutes on the player's real game. The player picks up trash and, if one drops, a potion. The player tells us the counts at the start and at the end, from the Etc tab, or from a screenshot of it.
  - Sum the item lines read and compare with the real change.
  - If fewer than 90% of item pickups are captured, stop and report to the player before building the rest.
- Fixture-based tests for:
  - parsing item lines, and once-only counting with equal lines in a row;
  - names cut with "...";
  - ignoring meso, EXP and unknown lines;
  - a found potion not also booked as a purchase;
  - the unsold-trash tally at reconciliation: a full sale, a partial sale, getting more or less than listed, and the tally surviving a reset;
  - the CSV upgrade from the stage 1 header.
- All existing tests still pass. A live 10-minute check on the game, with a sale, before finishing.

## 8. Left to the builder

- How to read item names. Choose between:
  - per-name templates, like the meso prefix;
  - a letter glyph set;
  - Windows OCR on a cleaned crop. Stage 1 found OCR misread digits often; names may do better.
  - Lines are right-aligned, so the fixed suffix ` earned. (Etc)` / ` earned. (Use)` at the right edge is a natural anchor.
- How to share the line tracking between meso lines and item lines, so a line is never counted by both.
- Where the quantity `xN` sits, and its digits. These are probably the same font as the meso amounts. There is no comma template yet: meso amounts of 1,000 or more are skipped today. A quantity is never that large.

## 9. Open risks

- **Item names are longer and more varied than "gained mesos".** Reading may be less reliable than for meso lines. The §7 feasibility test measures this.
- **Many pickups at once** (a pile of loot) push lines up quickly, and some may be missed. Trash value then shows up only at the sale, as a correction or "other".
- **The found-potion versus purchase rule (§6.2)** depends on the item line and the key count agreeing. If the item line is missed, the increase is treated as a purchase, as in stage 1. That is harmless to the net, but the found potion isn't credited.

## 10. Feasibility recording (taken 2026-11-06)

`recordings/loot_a` (git-ignored), 21:38:25 + 300 s, made with `tools/record_meso_session.py`:
- Etc tab at the start: Tablecloth 0, Stirge Wing 0 (the player's words).
- Etc tab at the end: Tablecloth 40, Stirge Wing 15 (the player's screenshot; the end meso total was 1,058,437).
- So the session had 55 trash pickups.
- Party play: the pickup stack also holds many "You received EXP" and "Bonus EXP for PARTY" lines.
- The player had just bought potions; the keys show Meat 192 and Lemon 478.
