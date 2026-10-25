# -*- coding: utf-8 -*-
"""v0.9.132.0 — dog sheets v5: ALL 20 behaviours from the CUTE cartoon
shih-tzu GIFs (the user preferred the dog3 art over the std paper style,
and the 2-frame std sheets read choppy). Same pipeline as
_build_dog3_sheets.py (border flood over near-white, >=40px component
filter, 1-px erosion, CELL 150x120 / content 104 / baseline y=116).

NEW in this pass — the four behaviours the dog3 era never had sheets
for, now sourced (contact-sheet verified):
  throw   = shih_throw_c   [0,1,2,1]  mirror  (f0-f2 fly LEFT -> flip;
                                       f3 faces the other way, EXCLUDED)
  belly   = shih_belly_c   [0,1,2,2]  no-mir (f0 crouch faces RIGHT;
                                       f1-f3 belly-up, body-agnostic;
                                       tan f3 dropped for colour unity)
  stretch = shih_stretch_c [0,1,2,1]  no-mir (play-bow faces RIGHT
                                       natively — head down on the right)
  tilt    = shih_tilt_c    [0,1,2,1]  no-mir (all frames frontal)

The other 16 keep the validated dog3 map (orders + mirror decisions
documented in _build_dog3_sheets.py)."""
import os
from collections import deque

import numpy as np
from PIL import Image

SRC = r"C:\Users\humphery\images"
OUT_DIR = r"c:\Users\humphery\coding\qdrant-rag-agent\gui\assets"

GIFS = {
    # key: (file, mirror) — validated dog3 decisions kept as-is
    "run": ("shih_run_c.gif", True),
    "roll": ("shih_roll_c.gif", True),
    "sleep": ("shih_sleep_c.gif", True),
    "bone": ("shih_bone_c.gif", True),
    "walk": ("shih_walk_c.gif", True),
    "hop": ("shih_hop_c.gif", True),
    "yawn": ("shih_yawn_c.gif", True),
    "tail": ("shih_tail_c.gif", False),
    "ball": ("shih_ball_c.gif", True),
    "bark": ("shih_bark_c.gif", True),
    "lick": ("shih_lick_c.gif", True),
    "lie": ("shih_lie_c.gif", True),
    "spin": ("shih_spin_c.gif", True),
    "lift": ("shih_lift_c3.gif", False),
    "putdown": ("shih_putdown_c3.gif", False),
    "bite": ("shih_bite_c3.gif", True),
    # v0.9.132.0 — the four NEW behaviours (orientation verified from
    # the contact sheet / zoom: throw flies LEFT -> mirror; belly f0
    # crouches RIGHT and the belly-up trio is body-agnostic; stretch
    # bows RIGHT natively; tilt is frontal throughout)
    "throw": ("shih_throw_c.gif", True),
    "belly": ("shih_belly_c.gif", False),
    "stretch": ("shih_stretch_c.gif", False),
    "tilt": ("shih_tilt_c.gif", False),
}

SHEETS = [
    ("dog_run_sheet.png", "run", [0, 2, 3, 0, 2, 3], "fit"),
    ("dog_walk_sheet.png", "walk", [1, 2, 1, 2, 1, 2], "fit"),
    ("dog_hop_sheet.png", "hop", [0, 0, 2, 3, 3], "union"),
    ("dog_spin_sheet.png", "spin", [0, 1, 2, 3], "union"),
    ("dog_ball_sheet.png", "ball", [0, 1, 2, 3, 2, 1], "union"),
    ("dog_bone_sheet.png", "bone", [0, 1, 0, 1, 0, 1], "union"),
    ("dog_lift_sheet.png", "lift", [0, 1, 2, 3], "union"),
    ("dog_putdown_sheet.png", "putdown", [0, 1, 2, 3], "union"),
    # the throw one-shot plays WHILE the JS fling arc moves the dog;
    # [0,1,2,1] ping-pongs the three left-facing fly poses
    ("dog_throw_sheet.png", "throw", [0, 1, 2, 1], "union"),
    # crouch -> roll over -> belly up (hold); tan f3 excluded
    ("dog_belly_sheet.png", "belly", [0, 1, 2, 2], "union"),
    ("dog_bark_sheet.png", "bark", [0, 2, 3, 2], "union"),
    ("dog_lick_sheet.png", "lick", [1, 2, 3, 2], "union"),
    ("dog_yawn_sheet.png", "yawn", [0, 1, 2, 3, 2, 1], "union"),
    ("dog_bite_sheet.png", "bite", [0, 1, 0, 3], "union"),
    ("dog_sleep_sheet.png", "sleep", [0, 1, 2, 3, 3, 3], "union"),
    ("dog_lie_sheet.png", "lie", [0, 1, 2, 3, 3, 3], "union"),
    # wake chain: bow -> up -> bow -> up (ends frontal, pre-yawn)
    ("dog_stretch_sheet.png", "stretch", [0, 1, 2, 1], "union"),
    # head-tilt wiggle: tilt-left -> neutral -> tilt-right -> neutral
    ("dog_tilt_sheet.png", "tilt", [0, 1, 2, 1], "union"),
    ("dog_roll_sheet.png", "roll", [0, 1, 2, 3, 3], "union"),
    ("dog_tail_sheet.png", "tail", [0, 1, 2, 3, 1, 2], "union"),
]

CELL_W, CELL_H = 150, 120
TARGET_H = 104          # scaled content height (the dog body)
BASELINE = CELL_H - 4   # content bottom sits 4 px above the cell edge
ALPHA_MIN = 40
WHITE_MIN = 240         # near-white flood threshold (bg is 253-255)
COMP_MIN = 40           # component filter: keep >= 40 px (keeps chew lines)


def _flood(bg):
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
    while dq:
        y, x = dq.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] \
                    and not reached[ny, nx]:
                reached[ny, nx] = True
                dq.append((ny, nx))
    return reached


def _components(mask):
    """Yield (size, component_mask) for every 4-connected component."""
    h, w = mask.shape
    seen = np.zeros((h, w), dtype=bool)
    for y0, x0 in zip(*np.where(mask)):
        if seen[y0, x0]:
            continue
        comp = np.zeros((h, w), dtype=bool)
        dq = deque([(y0, x0)])
        seen[y0, x0] = True
        comp[y0, x0] = True
        while dq:
            y, x = dq.popleft()
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] \
                        and not seen[ny, nx]:
                    seen[ny, nx] = True
                    comp[ny, nx] = True
                    dq.append((ny, nx))
        yield int(comp.sum()), comp


def load_gif_frames(path, mirror=True):
    """Extract every frame as RGB, optionally mirrored to face RIGHT."""
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    frames = []
    for i in range(n):
        im.seek(i)
        fr = im.convert("RGB")
        if mirror:
            fr = fr.transpose(Image.FLIP_LEFT_RIGHT)
        frames.append(fr)
    return frames


def remove_background(rgb):
    """Border-flood the near-white background; keep the outlined dog."""
    rgb = np.asarray(rgb)
    mn = rgb.min(axis=2).astype(np.int16)
    near_white = mn >= WHITE_MIN
    reached = _flood(near_white)
    dog = ~reached
    kept = np.zeros_like(dog)
    for size, comp in _components(dog):
        if size >= COMP_MIN:
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


def _frame_bbox(mask):
    yidx, xidx = np.where(mask)
    return int(xidx.min()), int(yidx.min()), \
        int(xidx.max()), int(yidx.max())


def build_fit_sheet(cells, n):
    """Per-frame AREA-equalized, bottom-aligned cells (anti-flicker).

    Each frame's content bbox is scaled by sqrt(ref_area / area_i) so
    the dog's apparent size stays constant, a common factor then fits
    everything into the cell, every frame is bottom-aligned on the
    baseline and centred horizontally.
    """
    bboxes = [_frame_bbox(a) for _, a in cells]
    areas = [(b[2] - b[0] + 1) * (b[3] - b[1] + 1) for b in bboxes]
    ref = float(np.median(areas))
    eq = [np.sqrt(ref / a) for a in areas]
    sw = [(b[2] - b[0] + 1) * e for b, e in zip(bboxes, eq)]
    sh = [(b[3] - b[1] + 1) * e for b, e in zip(bboxes, eq)]
    fit_w = min((CELL_W - 4) / max(sw), 2.0)
    fit_h = TARGET_H / max(sh)
    s = min(fit_w, fit_h)
    sheet = Image.new("RGBA", (CELL_W * n, CELL_H), (0, 0, 0, 0))
    for i, ((im, a), b, e) in enumerate(zip(cells, bboxes, eq)):
        x0, y0, x1, y1 = b
        crop = im.crop((x0, y0, x1 + 1, y1 + 1)).copy()
        crop.putalpha(Image.fromarray(
            (a[y0:y1 + 1, x0:x1 + 1] * 255).astype(np.uint8)))
        cw, ch = round((x1 - x0 + 1) * e * s), round((y1 - y0 + 1) * e * s)
        crop = crop.resize((cw, ch), Image.LANCZOS)
        sheet.paste(crop, (i * CELL_W + (CELL_W - cw) // 2,
                           BASELINE - ch), crop)
    return sheet, areas, s


def main():
    cache = {}
    for key, (fname, mirror) in GIFS.items():
        frames = load_gif_frames(os.path.join(SRC, fname), mirror=mirror)
        done = []
        for fr in frames:
            im, dog = remove_background(fr)
            done.append((im, dog))
        cache[key] = done
        print(f"{key}: {len(done)} frames processed")
    for out_name, key, order, mode in SHEETS:
        cells = [cache[key][i] for i in order]
        n = len(order)
        if mode == "fit":
            sheet, areas, s = build_fit_sheet(cells, n)
            print(f"{out_name}: {key}{order} fit-mode areas={areas} "
                  f"scale={s:.3f} -> {sheet.size}")
        else:
            masks = [c[1] for c in cells]
            x0, y0, x1, y1 = union_bbox(masks)
            uw, uh = x1 - x0 + 1, y1 - y0 + 1
            scale = min(TARGET_H / uh, (CELL_W - 4) / uw)
            sw, sh = round(uw * scale), round(uh * scale)
            if sw > CELL_W:
                raise SystemExit(
                    f"{out_name}: scaled width {sw} > cell {CELL_W}")
            sheet = Image.new("RGBA", (CELL_W * n, CELL_H), (0, 0, 0, 0))
            px0 = (CELL_W - sw) // 2
            py0 = BASELINE - sh
            for i, (im, a) in enumerate(cells):
                crop = im.crop((x0, y0, x1 + 1, y1 + 1)).copy()
                crop.putalpha(Image.fromarray(
                    (a[y0:y1 + 1, x0:x1 + 1] * 255).astype(np.uint8)))
                crop = crop.resize((sw, sh), Image.LANCZOS)
                sheet.paste(crop, (i * CELL_W + px0, py0), crop)
            print(f"{out_name}: {key}{order} union=({x0},{y0})-({x1},{y1}) "
                  f"{uw}x{uh} scale={scale:.3f} -> {sheet.size}")
        out = os.path.join(OUT_DIR, out_name)
        sheet.save(out, compress_level=1)
        print(f"    -> {os.path.getsize(out)} bytes")
    print("DONE")


if __name__ == "__main__":
    main()
CELL_W, CELL_H = 150, 120
TARGET_H = 104          # scaled content height (the dog body)
BASELINE = CELL_H - 4   # content bottom sits 4 px above the cell edge
ALPHA_MIN = 40
WHITE_MIN = 240         # near-white flood threshold (bg is 253-255)
COMP_MIN = 40           # component filter: keep >= 40 px (keeps chew lines)