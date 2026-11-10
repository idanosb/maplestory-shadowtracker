"""Extract glyph and anchor templates from native 2560x1440 fixtures.

Glyphs: binarize a crop with the reader's own pipeline, split it into glyphs and pair them
in order with the known text from tests/fixtures/truth.json (spaces removed). A glyph is
saved to expstrip/templates/{exp,level}/ when its character is unseen (`6.png`) or when it
matches every stored variant of that character below --variant (`6_2.png`, `6_3.png`, ...):
the client upscales its UI, so one character renders a little differently at different
sub-pixel positions. --reset empties the glyph folders first.

Anchors: --anchor NAME=FIXTURE:x,y,w,h saves a colour crop as expstrip/templates/NAME.png.

Quickslot potions (Task 4):
- --icon NAME=SOURCE:x,y,w,h saves a colour crop as expstrip/templates/potions/NAME.png.
- --count SOURCE:NAME[=DIGITS] finds potion NAME's icon in SOURCE (scale 1.0), cuts the
  digit cells of its count with the reader's own geometry (glyphs.count_cells: the first
  cell where a stored template of the first digit fits best, if any) and saves
  unseen digits / new variants (below --variant) to expstrip/templates/count/. DIGITS
  defaults to the potion's count in tests/fixtures/meso_truth.json. --reset-count empties
  templates/count/ first.
Inventory mesos (Task 5):
- --anchor anchor_mesos=meso_full_01:389,828,68,32 saves the field-edge + MESOS label anchor.
- --mesos SOURCE[=TOTAL] cuts the field of the inventory window found in SOURCE (scale 1.0)
  and saves unseen glyphs / variants of TOTAL written with commas ("1,045,605") to
  expstrip/templates/mesos/. TOTAL defaults to "mesos" in tests/fixtures/meso_truth.json.
  --reset-mesos empties templates/mesos/ first.
SOURCE is a fixture name (tests/fixtures/SOURCE.png) or a path to a 2560x1440 PNG.

Examples (the boxes used to build the committed templates):
    python tools/extract_templates.py --reset session_02 session_03 session_04 session_06 \
        session_07 session_09 session_11 --exp 1402,1384,168,19 --lv 612,1390,52,33
    python tools/extract_templates.py \
        --anchor anchor_exp=session_06:1352,1380,49,24 \
        --anchor anchor_lv=session_06:553,1392,42,30 \
        --anchor anchor_minimap_world=session_06:482,6,70,26 \
        --anchor anchor_minimap_collapsed=session_06:431,6,50,26 \
        --anchor anchor_minimap_expanded=session_02:10,15,85,18
    (potion commands: see templates/README.md, "Quickslot counts")
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from _common import FIXTURES  # also puts the project root on sys.path

from expstrip.vision.glyphs import CHAR_TO_NAME, GlyphSet, count_cells, glyph_images, score, templates_dir


def _box(s: str) -> tuple[int, int, int, int]:
    x, y, w, h = (int(v) for v in s.split(","))
    return x, y, w, h


def _crop(img, box):
    x, y, w, h = box
    return img[y:y + h, x:x + w]


def extract_glyphs(img, box, text: str, mode: str, variant: float = 0.97, out_dir: Path | None = None) -> list[str]:
    """Save the glyphs of `text` found in `box` of `img` into `out_dir` (default: the
    committed templates/<mode>/). Returns the stems saved."""
    out_dir = Path(out_dir) if out_dir is not None else templates_dir() / mode
    out_dir.mkdir(parents=True, exist_ok=True)
    glyphs = glyph_images(_crop(img, box), mode)
    chars = text.replace(" ", "")
    if len(glyphs) != len(chars):
        raise SystemExit(f"{mode}: {len(glyphs)} glyphs but text {chars!r} has {len(chars)} characters; adjust the box")
    saved = []
    for g, ch in zip(glyphs, chars):
        known = GlyphSet.load(out_dir).templates.get(ch, [])
        if known and max(score(g, t) for t in known) >= variant:
            continue
        stem = CHAR_TO_NAME.get(ch, ch)
        path = out_dir / f"{stem}.png"
        k = 2
        while path.exists():
            path = out_dir / f"{stem}_{k}.png"
            k += 1
        cv2.imwrite(str(path), np.clip(g * 255.0 + 0.5, 0, 255).astype(np.uint8))
        saved.append(path.stem)
    return saved


def _load(source: str):
    path = Path(source) if source.lower().endswith(".png") else FIXTURES / f"{source}.png"
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"cannot read {path}")
    if img.shape[:2] != (1440, 2560):
        raise SystemExit(f"{path} is {img.shape[1]}x{img.shape[0]}; templates must come from native 2560x1440")
    return img


def _save_variant(img: np.ndarray, ch: str, out_dir: Path, variant: float) -> str | None:
    known = GlyphSet.load(out_dir).templates.get(ch, [])
    if known and max(score(img, t) for t in known) >= variant:
        return None
    stem = CHAR_TO_NAME.get(ch, ch)
    path = out_dir / f"{stem}.png"
    k = 2
    while path.exists():
        path = out_dir / f"{stem}_{k}.png"
        k += 1
    cv2.imwrite(str(path), np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8))
    return path.stem


def extract_count(img, name: str, digits: str, variant: float = 0.97, out_dir: Path | None = None) -> list[str]:
    """Save the digit cells of potion `name`'s quickslot count (known to read `digits`)."""
    from expstrip.vision.glyphs import to_native
    from expstrip.vision.quickslots import count_rect, find_icon

    out_dir = Path(out_dir) if out_dir is not None else templates_dir() / "count"
    out_dir.mkdir(parents=True, exist_ok=True)
    m = find_icon(img, 1.0, name)
    if m is None:
        raise SystemExit(f"icon {name} not found")
    try:
        cells = count_cells(to_native(count_rect(m.x, m.y, m.scale).crop(img), m.scale), digits,
                            GlyphSet.load(out_dir))
    except ValueError as e:
        raise SystemExit(f"{name}: {e}")
    if len(cells) != len(digits):
        raise SystemExit(f"{name}: found {len(cells)} cells for {digits!r}")
    return [st for c, ch in zip(cells, digits) if (st := _save_variant(c, ch, out_dir, variant))]


def extract_mesos(img, total: int, variant: float = 0.97, out_dir: Path | None = None) -> list[str]:
    """Save the glyphs of the inventory meso field of `img` (known to read `total`)."""
    from expstrip.vision.inventory import field_rect, find_anchor

    out_dir = Path(out_dir) if out_dir is not None else templates_dir() / "mesos"
    m = find_anchor(img, 1.0)
    if m is None:
        raise SystemExit("meso anchor not found")
    r = field_rect(m.x, m.y, m.scale)
    return extract_glyphs(img, (r.x, r.y, r.w, r.h), f"{total:,}", "mesos", variant, out_dir)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixtures", nargs="*", help="fixture names from truth.json, e.g. session_06")
    ap.add_argument("--exp", type=_box, help="x,y,w,h of the EXP value text (native pixels)")
    ap.add_argument("--lv", type=_box, help="x,y,w,h of the level digit tiles (native pixels)")
    ap.add_argument("--anchor", action="append", default=[], help="NAME=FIXTURE:x,y,w,h")
    ap.add_argument("--variant", type=float, default=0.97, help="save a new variant below this score (default 0.97)")
    ap.add_argument("--reset", action="store_true", help="delete existing glyph templates first")
    ap.add_argument("--icon", action="append", default=[], help="NAME=SOURCE:x,y,w,h (potion icon)")
    ap.add_argument("--count", action="append", default=[], help="SOURCE:NAME[=DIGITS] (quickslot count)")
    ap.add_argument("--reset-count", action="store_true", help="delete existing count templates first")
    ap.add_argument("--mesos", action="append", default=[], help="SOURCE[=TOTAL] (inventory meso field)")
    ap.add_argument("--reset-mesos", action="store_true", help="delete existing meso templates first")
    args = ap.parse_args()

    if args.reset:
        for mode in ("exp", "level"):
            for p in (templates_dir() / mode).glob("*.png"):
                p.unlink()
    truth = json.loads((FIXTURES / "truth.json").read_text(encoding="utf-8"))
    for name in args.fixtures:
        entry = truth[name]
        img = cv2.imread(str(FIXTURES / f"{name}.png"))
        if img.shape[:2] != (1440, 2560):
            raise SystemExit(f"{name} is {img.shape[1]}x{img.shape[0]}; templates must come from native 2560x1440")
        if args.exp and entry.get("exp_text"):
            print(name, "exp saved:", extract_glyphs(img, args.exp, entry["exp_text"], "exp", args.variant))
        if args.lv and entry.get("level") is not None:
            print(name, "level saved:", extract_glyphs(img, args.lv, str(entry["level"]), "level", args.variant))

    for spec in args.anchor:
        aname, rest = spec.split("=", 1)
        fixture, box = rest.split(":", 1)
        img = cv2.imread(str(FIXTURES / f"{fixture}.png"))
        path = templates_dir() / f"{aname}.png"
        cv2.imwrite(str(path), _crop(img, _box(box)))
        print("anchor saved:", path.name)

    for spec in args.icon:
        pname, rest = spec.split("=", 1)
        source, box = rest.rsplit(":", 1)
        folder = templates_dir() / "potions"
        folder.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(folder / f"{pname}.png"), _crop(_load(source), _box(box)))
        print("icon saved:", pname)

    if args.reset_count:
        for p in (templates_dir() / "count").glob("*.png"):
            p.unlink()
    meso_truth = None
    for spec in args.count:
        source, _, rest = spec.rpartition(":")
        pname, _, digits = rest.partition("=")
        if not digits:
            if meso_truth is None:
                meso_truth = json.loads((FIXTURES / "meso_truth.json").read_text(encoding="utf-8"))
            digits = str(meso_truth[source]["potions"][pname])
        print(source, pname, digits, "count saved:", extract_count(_load(source), pname, digits, args.variant))

    if args.reset_mesos:
        for p in (templates_dir() / "mesos").glob("*.png"):
            p.unlink()
    for spec in args.mesos:
        source, _, total = spec.partition("=")
        if not total:
            if meso_truth is None:
                meso_truth = json.loads((FIXTURES / "meso_truth.json").read_text(encoding="utf-8"))
            total = str(meso_truth[source]["mesos"])
        print(source, total, "mesos saved:", extract_mesos(_load(source), int(total), args.variant))


if __name__ == "__main__":
    main()
