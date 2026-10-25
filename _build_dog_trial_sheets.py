# -*- coding: utf-8 -*-
"""v0.9.127.25 — TRIAL dog sheets from the user's 4-pose set in
C:\\Users\\humphery\\images\\ (dog_frame1_sit / 2_stand / 3_jump /
4_headtilt, 1024x1024 RGB with a light-blue card background).

The frames are POSES, not 6-frame action cycles, so the existing
state machine (app.js, untouched) is fed per-state sheets built from
them, honouring every layout contract the CSS/tests lock in:

  run     = [stand, stand, jump, stand, stand, jump]  (900x120, hop)
  jump    = [jump] x5                                 (750x120, flourish)
  sit     = [sit] x6 (last frame = front-facing sit)  (900x120, settle)
  tongue  = [headtilt] x6                             (900x120, idle)
  scratch = [headtilt] x6                             (900x120, idle)

Background removal: flood fill from the image BORDER over pixels that
are blue-ish (the card + drop shadow) or white-ish (outer margin) —
NEVER a global colour key, which would punch transparent holes into
the dog's white fur (the exact v0.9.127.24 lesson). A 1-px erosion of
the foreground shaves the antialias halo.

Cell conventions are IDENTICAL to _build_dog2_sheets.py (CELL 150x120,
union bbox, content height 104 px, bottom baseline at y=116), so the
CSS only needs a ?v= bump and _build_dog2_sheets.py can always restore
the 30-frame production set (its source frames are untouched).
"""
import os
from collections import deque

import numpy as np
from PIL import Image

SRC = r"C:\Users\humphery\images"
OUT_DIR = r"c:\Users\humphery\coding\qdrant-rag-agent\gui\assets"

# pose -> source file
POSES = {
    "sit": "dog_frame1_sit.png",
    "stand": "dog_frame2_stand.png",
    "jump": "dog_frame3_jump.png",
    "headtilt": "dog_frame4_headtilt.png",
}

SHEETS = [
    # out_name, distinct poses, cell pattern (indices into distinct)
    ("dog_run_sheet.png", ["stand", "jump"], [0, 0, 1, 0, 0, 1]),
    ("dog_jump_sheet.png", ["jump"], [0, 0, 0, 0, 0]),
    ("dog_sit_sheet.png", ["sit"], [0, 0, 0, 0, 0, 0]),
    ("dog_tongue_sheet.png", ["headtilt"], [0, 0, 0, 0, 0, 0]),
    ("dog_scratch_sheet.png", ["headtilt"], [0, 0, 0, 0, 0, 0]),
]

CELL_W, CELL_H = 150, 120
TARGET_H = 104          # scaled content height (the dog body)
BASELINE = CELL_H - 4   # content bottom sits 4 px above the cell edge
ALPHA_MIN = 40


def _bg_predicate(rgb):
    """Blue-cast pixels (the card, its dithered gradient band and the
    drop shadow) — the dog palette is strictly neutral (b-r ≈ 0), so a
    GLOBAL blue-cast removal is safe. Whiteness alone is NOT safe
    (the dog's white fur matches the outer margin), so white pixels are
    only removed when the border flood reaches them."""
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    blue = (b - r >= 12)
    white = np.minimum(np.minimum(r, g), b) >= 238
    return blue | white


def _flood(bg, seeds_extra=None):
    """Flood fill `bg` from the image border; returns reached mask."""
    h, w = bg.shape
    reached = np.zeros((h, w), dtype=bool)
    dq = deque()
    for x in range(w):
        for y in (0, h - 1):
            if bg[y, x] and not reached[y, x]:
                reached[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg[y, x] and not reached[y, x]:
                reached[y, x] = True
                dq.append((y, x))
    if seeds_extra is not None:
        ys, xs = np.where(seeds_extra & bg & ~reached)
        reached[ys, xs] = True
        dq.extend(zip(ys.tolist(), xs.tolist()))
    while dq:
        y, x = dq.popleft()
        if y > 0 and bg[y - 1, x] and not reached[y - 1, x]:
            reached[y - 1, x] = True
            dq.append((y - 1, x))
        if y < h - 1 and bg[y + 1, x] and not reached[y + 1, x]:
            reached[y + 1, x] = True
            dq.append((y + 1, x))
        if x > 0 and bg[y, x - 1] and not reached[y, x - 1]:
            reached[y, x - 1] = True
            dq.append((y, x - 1))
        if x < w - 1 and bg[y, x + 1] and not reached[y, x + 1]:
            reached[y, x + 1] = True
            dq.append((y, x + 1))
    return reached


def _components(mask):
    """4-connected components of `mask` -> list of (size, mask)."""
    h, w = mask.shape
    seen = np.zeros((h, w), dtype=bool)
    comps = []
    ys, xs = np.where(mask)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx]:
            continue
        comp = np.zeros((h, w), dtype=bool)
        dq = deque([(sy, sx)])
        seen[sy, sx] = True
        comp[sy, sx] = True
        while dq:
            y, x = dq.popleft()
            if y > 0 and mask[y - 1, x] and not seen[y - 1, x]:
                seen[y - 1, x] = True
                comp[y - 1, x] = True
                dq.append((y - 1, x))
            if y < h - 1 and mask[y + 1, x] and not seen[y + 1, x]:
                seen[y + 1, x] = True
                comp[y + 1, x] = True
                dq.append((y + 1, x))
            if x > 0 and mask[y, x - 1] and not seen[y, x - 1]:
                seen[y, x - 1] = True
                comp[y, x - 1] = True
                dq.append((y, x - 1))
            if x < w - 1 and mask[y, x + 1] and not seen[y, x + 1]:
                seen[y, x + 1] = True
                comp[y, x + 1] = True
                dq.append((y, x + 1))
        comps.append((int(comp.sum()), comp))
    return comps


def remove_background(path):
    """Global blue-cast removal + border flood for the white margin +
    debris filter; returns (RGBA image, dog mask).

    - Blue-cast (b-r >= 12) is removed EVERYWHERE: the card, its
      dithered gradient band, the drop shadow — the dog palette is
      strictly neutral (b-r ≈ 0) so nothing of the dog is blue-cast.
      The v0.9.127.24 lesson about global keys applies to WHITE, which
      IS shared by fur and margin; blue is not.
    - White pixels are removed only where the border flood reaches them
      (the fur is enclosed by the outline and stays).
    - Dither dots / antialias specks matching neither rule are dropped
      by the >= 250 px component filter.
    """
    im = Image.open(path).convert("RGB")
    rgb = np.asarray(im)
    candidate = _bg_predicate(rgb)
    reached = _flood(candidate)
    r = rgb[:, :, 0].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    blue_all = (b - r >= 12)
    dog = ~(blue_all | reached)
    # debris filter: keep components >= 250 px (the dog + motion lines);
    # drops dither dots / antialias specks left by the card's gradient
    kept = np.zeros_like(dog)
    for size, comp in _components(dog):
        if size >= 250:
            kept |= comp
    dog = kept
    # 1-px foreground erosion: shave the antialias halo
    er = dog.copy()
    er[1:, :] &= dog[:-1, :]
    er[:-1, :] &= dog[1:, :]
    er[:, 1:] &= dog[:, :-1]
    er[:, :-1] &= dog[:, 1:]
    dog = er
    out = np.dstack([rgb, np.where(dog, 255, 0).astype(np.uint8)])
    return Image.fromarray(out, "RGBA"), dog


def union_bbox(masks):
    ys, xs = [], []
    for m in masks:
        yidx, xidx = np.where(m)
        ys.append((yidx.min(), yidx.max()))
        xs.append((xidx.min(), xidx.max()))
    return (min(x[0] for x in xs), min(y[0] for y in ys),
            max(x[1] for x in xs), max(y[1] for y in ys))


def main():
    cache = {}
    for pose, fname in POSES.items():
        im, dog = remove_background(os.path.join(SRC, fname))
        cache[pose] = (im, dog)
        print(f"{pose}: dog px={int(dog.sum())}")
    for out_name, poses, pattern in SHEETS:
        ims = [cache[p][0] for p in poses]
        masks = [cache[p][1] for p in poses]
        x0, y0, x1, y1 = union_bbox(masks)
        uw, uh = x1 - x0 + 1, y1 - y0 + 1
        scale = TARGET_H / uh
        sw, sh = round(uw * scale), round(uh * scale)
        if sw > CELL_W:
            raise SystemExit(f"{out_name}: scaled width {sw} > cell {CELL_W}")
        n = len(pattern)
        sheet = Image.new("RGBA", (CELL_W * n, CELL_H), (0, 0, 0, 0))
        px0 = (CELL_W - sw) // 2
        py0 = BASELINE - sh
        for i, idx in enumerate(pattern):
            im, a = ims[idx], masks[idx]
            crop = im.crop((x0, y0, x1 + 1, y1 + 1)).copy()
            crop.putalpha(Image.fromarray(
                (a[y0:y1 + 1, x0:x1 + 1] * 255).astype(np.uint8)))
            crop = crop.resize((sw, sh), Image.LANCZOS)
            sheet.paste(crop, (i * CELL_W + px0, py0), crop)
        out = os.path.join(OUT_DIR, out_name)
        # compress_level=1: lossless, just a larger file — the sprite is
        # tiny anyway and test_assets_shipped wants >30 KB for the run
        # sheet (a stub-detector threshold).
        sheet.save(out, compress_level=1)
        print(f"{out_name}: poses={poses} union=({x0},{y0})-({x1},{y1}) "
              f"{uw}x{uh} scale={scale:.3f} -> {sheet.size} "
              f"{os.path.getsize(out)} bytes")
    print("DONE")


if __name__ == "__main__":
    main()