# EXP Strip — MapleStory Classic EXP tracker overlay (design)

Date: 2026-11-04
Status: draft, awaiting approval

## 1. Intent

The player (Windows 10, plays **MapleStory Classic**, Nexon's official server) wants a small always-on-top overlay that answers two questions while grinding:

1. **Where is it most worth training?** They move between training spots and want a trustworthy EXP-per-hour number *per spot*, kept over days, so spots can be compared.
2. **How long until I level up?** At the current pace.

The overriding constraint is **ban safety**: the tool must be fully passive, reading only pixels from the screen, the way a screen recorder does.

## 2. Goals

- Read level, current EXP and EXP % from the game's bottom status bar, and the current map name from the top-left map title, by **screen capture only**.
- Show a compact strip overlay with: level, EXP/hour (at the current spot), time to next level, EXP in the last 5 minutes, EXP this session, and a thin progress bar for the level %.
- Track each **training spot** (the map name) automatically. Keep a history across sessions and days, and show it in a panel ranked by EXP/hour.
- Save the history to a CSV file in a folder next to the app, so it opens in Excel.
- Ship as a single double-clickable `.exe`.

## 3. Non-goals

- Tracking mesos, drops, HP/MP, or anything else besides EXP, level and map.
- OBS or streaming output.
- Multiple characters or multiple game windows at once.
- Any form of automation.

## 4. Anti-goals (a failure even if it "works")

The tool must never:

- Open a handle to the game process (`OpenProcess`, `ReadProcessMemory`, DLL injection, hooks, DirectX/overlay injection).
- Send keys, clicks or messages to the game window. That includes `SendMessage`/`PostMessage`, `SetForegroundWindow` on the game, and low-level keyboard or mouse hooks (`SetWindowsHookEx`).
- Sniff or touch network traffic. The app makes **no network calls at all**, including update checks and telemetry.
- Need administrator rights.
- Use a suspicious executable, process or window name (nothing like "hack", "bot" or "cheat"). Working name: **EXP Strip** (`ExpStrip.exe`).

The only allowed interaction with the game window is user32 *window queries* that do not open the process: `EnumWindows`/`FindWindow`, `GetWindowText`, `GetClientRect`, `ClientToScreen`, `IsIconic`. Pixels are obtained by capturing the **desktop** region where the game's client area is, not by capturing the game's window or process.

## 5. Constraints

- Windows 10 Pro 19045 (supports `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`).
- **Display mode.** The user's graphics options (`docs/reference/game-graphics-options.png`) show:
  - Resolution `2560 x 1440 ( Full Screen )`, with "Windowed: Enable Alt + Enter For Fullscreen" ticked. The user plays at 2560×1440, full screen, on a monitor of that size.
  - UI Size "Default Ratio".

  Whether this full screen is *exclusive* (an overlay can't show over it) or borderless is unknown. **The very first build step is a throwaway visibility check:** a plain always-on-top translucent window, plus one desktop capture of the game region, run while the game is in its current mode.
  - If the window shows and the capture is not black, proceed.
  - If not, the user switches to windowed mode: pick a non-"Full Screen" resolution, or toggle with Alt+Enter. The app then shows the hint "Game not visible — use windowed mode" whenever it detects a black or absent capture.

  An overlay cannot appear over exclusive fullscreen.
- The game window may be moved, resized or scaled. In the reference screenshot the game renders centered with black side areas, and the UI is scaled. Region detection must not depend on fixed screen coordinates or on one fixed resolution.
- Capture rate: about 1 Hz. Keep CPU use low.

## 6. What the game screen looks like

Reference screenshot: `docs/reference/game-screenshot-kerning.webp` (the user's own client).

- **Bottom status bar** (along the bottom of the game area):
  - `LV.` label followed by level digits drawn as **orange tiles**, one tile per digit (e.g. `4` `4`).
  - `EXP` label followed by white text `114,698 [26.78%]` on a dark grey background, above a yellow-green bar.
  - HP and MP use the same style (ignore them).
- **Map title.** This is in the minimap window, which comes in two forms:
  - **Collapsed:** one line, `Victoria Road : Kerning City Pharmacy` (`game-screenshot-kerning.webp`).
  - **Expanded:** a "MINI MAP" header, then two lines, `Victoria Road` / `Kerning City`, beside a map icon, with the map picture below it (`game-screenshot-minimap-expanded.webp`).

  In both forms the spot name is the map name, not the region name. Normalize both to `region : map`. Variable-width text.
- **Layout varies by map.** In a narrow map (the pharmacy) the play view is letterboxed in the centre with black sides. In a wide map (Kerning City) it fills the screen. The status bar is always bottom-centre, about 1166 px wide in the 2000-px-wide screenshots. Both screenshots are downscaled from the native 2560×1440, so the real pixel sizes are about 1.28× larger.
- Prior art the user showed (not to be copied visually): `docs/reference/prior-art-other-tracker.png`.

## 7. How it works

### 7.1 Finding the game

- Find the game window by title. The exact window title of the MapleStory Classic client is **unknown**: default to a case-insensitive substring match on `MapleStory`, and make it configurable in `settings.json`.
- Use the client rect converted to screen coordinates. If the window is minimized or missing, show "Waiting for game…" on the strip and pause tracking.

### 7.2 Locating the readable regions

- Locate the `EXP` label and the `LV.` label inside the bottom part of the client area with template matching (OpenCV), trying a range of scales, so different window sizes and UI scaling work.
- Once found, the EXP-text region and the level-digit region are taken at fixed offsets from those anchors (in scaled units). Re-locate them when the window rect changes, or when reads fail repeatedly.
- The map title is *not* at a fixed crop. The minimap is a draggable game window, and in the reference screenshot it sits at the screen's top-left corner, outside the centred game view. Locate it with its own anchor search over the whole client area: match the minimap title-bar frame or its buttons, in both collapsed and expanded forms. Cache the location and re-search when OCR fails or yields nothing. *(Designer's call.)*
- Do not assume the status bar and the minimap share one sub-rectangle. In the reference layout the client area spans the whole screen and the play view is letterboxed in the centre, so every anchor is searched for across the full client area.

### 7.3 Reading values

- **EXP and %, and level:** read them by digit template matching against the game's own digit glyphs (`0–9`, `,`, `.`, `%`, `[`, `]`). The game uses a fixed bitmap font, so this is more reliable and much lighter than general OCR. Build the glyph templates from screenshots of the user's client.
- **Map name:** variable text, so use **Windows' built-in OCR** (`Windows.Media.Ocr` via the `winsdk`/WinRT Python bindings). It is on-device and needs no extra downloads. Verified on the user's machine: the only OCR recognizer installed is `en-US`, which covers the English map names. Request `en-US` explicitly rather than the user-profile default. Normalize the result: trim it, and keep the part after `:` as the spot name while storing the full string too.
- **Validation:** a read is accepted only if it parses, and if the % agrees with the EXP number given the implied level requirement (requirement ≈ EXP ÷ %, which must be consistent with previous good reads at the same level). Ignore single-frame outliers. Require 2 consecutive agreeing reads before a value changes.
- **Our own overlay must not pollute the capture:** call `SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` on every overlay window. Even if the strip sits on top of the status bar, the capture sees the game underneath.

### 7.4 Tracking math

- **Gain per tick:** If the level is unchanged, `gain = exp_now − exp_prev`. If the level went up by one, `gain = (requirement_prev − exp_prev) + exp_now`, where `requirement_prev` comes from the last good reads at the old level (EXP ÷ %).
- **EXP going down at the same level** (e.g. death penalty) counts as a negative gain. Net EXP is the honest measure of a spot. *(Designer's call.)*
- **Active time** is wall time minus paused time minus idle time.
  - **Pause (⏸)** stops active time and ignores gains while paused.
  - **Auto-idle:** if EXP hasn't changed for 2 minutes, the tool treats the time since the last change as idle. It resumes automatically on the next change. *(Designer's call.)*
- **EXP/hour** on the strip is `gain at current spot visit ÷ active time at current spot visit`. Show `—` until at least 60 s of active time.
- **Level up in** is `(requirement − exp_now) ÷ EXP/hour`. If the current visit doesn't have enough data yet, fall back to the session rate.
- **5 min** is the net gain over the last 5 minutes of wall time.
- **Session** is the net gain since the app started. There is no reset button; restarting the app starts a new session. *(Designer's call.)*

### 7.5 Training spots (history)

- A **visit** starts when the map name changes, and ends on the next map change, on pause-and-close, or on app exit.
- Visits with less than **60 s of active time are dropped** (passing through towns, portals). *(Designer's call.)*
- Data folder: `data/` next to the exe. The file `data/visits.csv` gets one row appended per finished visit:
  `date, map_full, spot, start_time, end_time, active_seconds, exp_gained, level_start, level_end`
  - Use UTF-8 with BOM, so Excel opens it correctly.
  - Append only. Never rewrite past rows.
  - Also flush the current visit periodically (every minute, as an in-progress file), so a crash doesn't lose a long session.
- The **spots panel** aggregates all visits per spot:
  - EXP/hour = Σ exp ÷ Σ active hours, over all-time.
  - Total time.
  - Last visited.
  - Sorted by EXP/hour, with the best marked 🏆 and the current spot marked "now".
  - A small bar per row, proportional to EXP/hour.
- EXP/hour naturally changes as the player levels. All-time averaging is acceptable for v1, and "Last visited" gives the context. *(Designer's call.)*

### 7.6 Overlay UI

The chosen look is the **"compact strip"**. Reference mockup: `docs/reference/mockup-strip-with-history.html` (open it in a browser).

- A light cream pill (`#fbf7ef`) about 560×60 px, with an orange level block on the left (`#ff7a3d`).
- In the middle: four values in this order: **EXP/hour** (emphasized, orange), **Level up in**, **5 min**, **Session**. A thin orange progress bar for the level % sits underneath.
- On the right: **⏸** (pause/resume) and **☰** (toggle the spots panel).
- The **spots panel** opens below the strip in the same style. Clicking ☰ again closes it.
- The look must stay clearly different from the prior-art screenshot (that one has a dark brown/gold frame and a big green bar). Do not drift toward it.
- Status text replaces the values when needed: "Waiting for game…", "Can't read EXP — is the status bar visible?", "Paused".
- Window behavior:
  - Frameless, translucent background, always-on-top.
  - Not shown in the taskbar.
  - Draggable. Its position is saved in `settings.json`.
- **Click-through lock:** a tray icon menu offers *Lock (click-through) / Unlock*, *Pause*, *Open data folder* and *Quit*. When locked, the strip ignores the mouse, so it's unlocked from the tray. No global hotkeys or keyboard hooks.
- Text in the UI is English, matching the mockup.

### 7.7 Packaging

- Python 3.12, PySide6 (UI), OpenCV and NumPy (template matching), `mss` (desktop capture), `winsdk` (OCR), `pywin32` or ctypes (window queries, display affinity).
- Build a single-file exe with PyInstaller, named `ExpStrip.exe`.
- On first run, create `data/` and `settings.json` next to the exe.

## 8. Testing

- Unit-test the parsing, validation and tracking math against recorded sequences of values. Include level-up, death, idle, pause, map changes and visits shorter than 60 s.
- Test region detection and digit reading offline against real screenshots. Start with `docs/reference/game-screenshot-kerning.webp`. The existing screenshots are downscaled WebP, which is lossy. Before building glyph templates, the builder should capture or ask for **native 2560×1440 PNG** captures, taken with the app's own capture code during the visibility check. Get a few at different levels and EXP values so all digits 0–9 appear, with the minimap in both forms, and keep them as fixtures.
- A `--debug` flag saves the cropped regions and the parsed values, so misreads on the user's machine can be diagnosed.

## 9. Left to the builder

- Module layout, class design and threading model. The capture/read loop must stay off the UI thread.
- Exact template-matching thresholds, the scale range and the glyph extraction procedure.
- Tray icon art and the exact spacing and fonts, within the mockup's look.
- How "game not visible" (e.g. exclusive fullscreen, or the game covered by another window) is detected heuristically.

## 10. Open risks

- The MapleStory Classic window title and exact UI positions are unverified. The design relies on anchor search and configuration to absorb this.
- The map title is only visible if the minimap is open, in either collapsed or expanded form. If it's unreadable, the spot is recorded as `Unknown map`.
- Nexon's terms of service may prohibit third-party tools in general. Passive screen reading minimizes detection risk but cannot guarantee zero risk. The user has been told this.
