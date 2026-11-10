# Templates

All templates were extracted at **native scale 1.0 = 2560×1440 client captures**
(`tests/fixtures/session_*.png`) with `tools/extract_templates.py`. `anchors.find`
reports `Match.scale` relative to this, and `glyphs.read` resizes crops by `1/scale`
back to it.

Regenerate:

```
.venv\Scripts\python tools\extract_templates.py --reset session_02 session_03 session_04 session_06 session_07 session_09 session_11 --exp 1402,1384,168,19 --lv 612,1390,52,33
.venv\Scripts\python tools\extract_templates.py --anchor anchor_exp=session_06:1352,1380,49,24 --anchor anchor_lv=session_06:553,1392,42,30 --anchor anchor_minimap_world=session_06:482,6,70,26 --anchor anchor_minimap_collapsed=session_06:431,6,50,26 --anchor anchor_minimap_expanded=session_02:10,15,85,18
```

## Anchors (colour crops; matched in grayscale)

| File | Content | Native box (x, y, w, h) | Source |
|---|---|---|---|
| `anchor_exp.png` | `EXP.` label of the status bar | 1352, 1380, 49, 24 | session_06 |
| `anchor_lv.png` | `LV.` label of the status bar | 553, 1392, 42, 30 | session_06 |
| `anchor_minimap_world.png` | orange `WORLD` button. **Shared minimap anchor**: present in both forms (score ≥ 0.94 in all fixtures) | 482, 6, 70, 26 | session_06 |
| `anchor_minimap_collapsed.png` | the `–` `+` button pair in the collapsed form (grey `–`, blue `+`) | 431, 6, 50, 26 | session_06 |
| `anchor_minimap_expanded.png` | the `MINI MAP` header text (expanded form only) | 10, 15, 85, 18 | session_02 |

Minimap form detection (for Task 8): find `anchor_minimap_world` (default `min_score` 0.8).
Expanded if `anchor_minimap_expanded` is found (score 1.00 in expanded fixtures, ≈ 0.52 in
collapsed ones). The collapsed pair scores 1.00 on collapsed and 0.88 on the expanded
buttons, because the two forms swap the button colours (expanded: blue `–`, grey `+`); on a
full frame it also hits 0.82–0.85 on other UI at the top right (x ≈ 2421). So use it only with
`min_score=0.95` (ideally inside a top-left region), or treat "world found and not expanded"
as collapsed.

Value regions relative to anchors at scale 1.0 (multiply offsets and sizes by `Match.scale`):
EXP value text = exp anchor + (50, 4), size 168×19; level tiles = LV anchor + (59, −2), size 52×33.

## Glyphs

Grayscale "ink" images (0..255, white = glyph) spanning the text line's ink rows, with a
1 px margin on each side. The filename stem is the character; `comma` `dot` `pct` `lbr`
`rbr` map to `,` `.` `%` `[` `]`. A `_<n>` suffix marks an extra variant of the same
character (`6_2.png`): the client upscales its UI, so one character renders slightly
differently at different sub-pixel positions. A variant is stored only when it matches the
existing variants below 0.97.

- `exp/` (14 px line height): `0`–`9`, `,` `.` `%` `[` `]`, 24 files including variants.
- `level/`: `4` (2 variants) from the fixtures; `5` `6` `7` learned at runtime on the author's
  client (Lv 45-47) and bundled. A digit with no template is guessed from the `exp/` digits resized
  to the tile glyph (`frame_reader.guess_level_digit`) and then learned into `data/glyphs/level/`.

## Pickup lines (`pickup/`)

Region (`expstrip.vision.pickups.pickup_rect`), native px at scale 1.0, measured from the
client's **bottom-right corner** and multiplied by `Regions.scale`: right edge 2 px left of
the client's right edge, width 422; bottom edge 276 px above the client's bottom edge,
height 242. On the 2560×1440 client that is x 2136..2558, y 922..1164. Measured on
`recordings/meso_b` (crop origin 1536, 792): the lines are right-aligned and end at client
x ≈ 2548; they sit in fixed slots 26.5 px apart, the newest (bottom) one centred at client
y ≈ 1146 and older ones at 1146 − 26.5·k. About 6 lines are visible at once; the region
holds 9 slots.

Templates are grayscale "ink" images (white-tophat of the darkest channel, normalised to the
line's 99.5th percentile), 22 px tall (`LINE_H`):

- `prefix.png`: the 150 columns ending at the `+` of `...gained mesos (+` (`PREFIX_W`).
- `0`–`9` (12 px: a 10 px glyph cell with a 1 px margin each side; digits sit at a 13 px
  pitch) and `rpar` (`)`, 8 px), with `_n` variants for sub-pixel renderings.
- No `comma` yet: no pickup ≥ 1,000 occurred in the recordings, so such lines are skipped.

Regenerate (samples are FRAME:Y:AMOUNT, labeled by eye; Y is the line's centre row inside the
region):

```
.venv\Scripts\python tools\extract_pickup_templates.py recordings\meso_b --reset 9:224:79 5:224:68 10:171:79 23:224:42 32:224:44 37:224:75 39:198:74 45:198:69 72:172:48 89:198:80 103:224:67 109:198:45 120:223:74 146:119:70 147:223:47 159:145:78 177:171:43 189:224:71 217:224:71 162:224:75 166:224:68
```

## Item pickup lines (`items/`)

Read by `expstrip.vision.items.read_item_lines` from the same region and ink image as the meso
lines (`Tablecloth x1 earned. (Etc)`). Measured on `recordings/loot_a`: the line ends at the
same right edge as the meso lines; the qty digit cell ends 6 px left of the suffix; the name
template ends 5 px (`NAME_GAP`) left of the `x` cell. All templates are 22 px tall ink images like `pickup/`:

- `suffix_Etc.png`, `suffix_Use.png`: ` earned. (Etc)` / ` earned. (Use)`, 130 / 141 px
  (`SUFFIX_W`), from 1 px left of the `e` to 1 px right of the `)`.
- `x.png` (`_n` variants) and `qty1.png`: the `x` and the qty `1` (the item lines' `1` renders
  a little differently from the meso `1`; other qty digits use the `pickup/` digits).
- `<visible name>.png`: one per item name, ending just left of the `x`. A name the game cuts
  has the visible text as file name (`Animal Fur Bun....png`), resolved by `match_name`.
  Names not in the price list (Blue/Orange Potion, Iron/Adamantium Ore, Animal Fur Bun...)
  are kept as distractors: without them, an `Orange Potion` line matched `Blue Potion` at 0.78.

A name without a template is not read. To learn a new name, record a short session picking it
up (`tools/record_meso_session.py`) and add a sample for it. Regenerate (samples are
FRAME:Y:NAME:QTY:TAB, labeled by eye; Y is the line's centre row inside the region; the first
sample of each tab gives its suffix):

```
.venv\Scripts\python tools\extract_item_templates.py recordings\loot_a --reset "43:172:Stirge Wing:1:Etc" "43:119:Blue Potion:1:Use" "83:172:Tablecloth:1:Etc" "146:171:Orange Potion:1:Use" "96:198:Iron Ore:1:Etc" "242:225:Adamantium Ore:1:Etc" "49:120:Animal Fur Bun...:1:Etc"
```

## Quickslot counts (`potions/`, `count/`)

Read by `expstrip.vision.quickslots.read_potion_counts` (glyphs mode `"count"`).
Native px at scale 1.0; on the 2560×1440 client the keys are 60×57 px at a ~66.3 px pitch,
row 2 (keys 1–4) at y 1181..1237, key 1 at x 1761.

- `potions/<Name>.png` (colour, matched in grayscale): the key rows 22..35 and columns 2..57
  of the key holding the potion — the icon between the key label (left out, so the potion
  may sit on any key) and the count. `Meat` = meso_full_01 box 1763,1203,56,14; `Lemon` =
  1829,1203,56,14. Self-match ≈ 1.0, best other position ≤ 0.75 (`ICON_MIN_SCORE` 0.85).
  `Orange Potion` and `Magic Potion` were cut (same box, key 3 / key 4) from a user screenshot
  of the quickslots at scale 1.0, grid placed from the Meat match; they read 143 / 8 on
  recordings/loot_a/full_0031.png. `Speed Potion` (PgUp) and `Sniper Potion` (Del) were cut
  with `quickslots.key_icon` from a live capture (recordings/live_a/full_0020.png). Other
  potions are learned at runtime by `expstrip.icon_learner` into `data/glyphs/potions/`.
- Same-shape vials (Magic blue, Sniper pink, Speed red) match each other in grey at up to
  0.98, so a grey match must also pass a colour check (`COLOR_MAX_DIFF`: mean B/G/R within 8).
- Count variants `1_6`, `2_4` (Sniper "12") and `1_7`, `9_3`, `2_5` (Orange "192") come from the
  same live capture (`tools/extract_templates.py` `extract_count`). A digit on the key's white
  background (Speed "10") is kept apart from it only by 4-connected labelling: its outline has
  diagonal gaps, and with 8-connectivity the strokes joined the background and "10" read "1".
  An empty cell must never be saved as a template (it reads as "0" everywhere).
- Count box: 6 px left of the icon template and 12 px below its top, 66×25 (digit band of
  19 rows plus 3 rows of slack above and below).
- `count/`: one 19×14 soft cell per digit (white strokes enclosed by the dark outline;
  key background, icons and thick bright patches removed), `_n` variants for sub-pixel
  phases and icon backgrounds. Digits advance 44/3 px, a `1` only 32/3 px.

Regenerate (labels read by eye; fixtures take their counts from `tests/fixtures/meso_truth.json`):

```
.venv\Scripts\python tools\extract_templates.py --icon Meat=meso_full_01:1763,1203,56,14 --icon Lemon=meso_full_01:1829,1203,56,14
.venv\Scripts\python tools\extract_templates.py --reset-count --count meso_full_01:Meat --count meso_full_01:Lemon --count meso_full_11:Meat --count meso_full_11:Lemon --count meso_full_21:Meat --count meso_full_21:Lemon --count meso_full_31:Meat --count meso_full_31:Lemon --count recordings/meso_b/full_0002.png:Lemon=271 --count recordings/meso_b/full_0022.png:Meat=83 --count recordings/meso_b/full_0022.png:Lemon=261 --count recordings/meso_b/full_0015.png:Lemon=264 --count recordings/meso_b/full_0017.png:Lemon=263 --count recordings/meso_b/full_0016.png:Meat=87 --count recordings/meso_b/full_0005.png:Lemon=270 --count recordings/meso_b/full_0023.png:Lemon=260 --count recordings/meso_b/full_0027.png:Meat=81 --count recordings/meso_b/full_0006.png:Lemon=269 --count recordings/meso_b/full_0017.png:Meat=86 --count recordings/meso_a/full_0001.png:Meat=103 --count recordings/meso_a/full_0008.png:Meat=100
```

## Inventory mesos (`anchor_mesos.png`, `mesos/`)

`anchor_mesos.png` (68x32 at native 2560x1440) is the right border of the white meso field plus the
"MESOS" label of the ITEM INVENTORY window; the field interior (264x23) sits at (-267, +5) from its
top-left. `mesos/` holds dark-on-white glyphs (digits and `comma`) cut from that field; every glyph
is cut over the line's full ink rows (the comma descends), so a total below 1,000 (no comma) does
not read. Regenerate:

```
.venv\Scripts\python tools\extract_templates.py --anchor anchor_mesos=meso_full_01:389,828,68,32
.venv\Scripts\python tools\extract_templates.py --reset-mesos --mesos meso_full_01 --mesos meso_full_11 --mesos meso_full_21 --mesos recordings/meso_b/full_0018.png=1047924 --mesos recordings/meso_b/full_0020.png=1048184
```
