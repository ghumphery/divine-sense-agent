# -*- coding: utf-8 -*-
"""Build the v0.9.127.23/24 production dog sheets from the user-generated
5-action set in C:\\Users\\humphery\\dog_anim\\ (5 actions x 6 frames,
512x512 transparent PNGs). Re-run any time the source frames change
(v0.9.127.24 = source re-exported with interior transparent holes
filled back to white after an over-aggressive background removal).

  run / jump / sit / tongue / scratch -> gui\\assets\\dog_<act>_sheet.png

Each sheet is 900x120 (six 150x120 cells). Every frame is cropped to
the action's UNION bounding box (so relative motion inside the action
is preserved), scaled so the union height maps to 104 px, and pasted
bottom-aligned on a shared ground baseline.

The scratch frames have a "FRAME N of 6" caption baked into the bottom
of the artwork (frames 1/2/5/6; 3/4 are clean) — a bottom content band
separated from the dog by a vertical gap is treated as caption text
and dropped.
"""
import os

import numpy as np
from PIL import Image

SRC = r"C:\Users\humphery\dog_anim"
OUT_DIR = r"c:\Users\humphery\coding\qdrant-rag-agent\gui\assets"
ACTIONS = ["run", "jump", "sit", "tongue", "scratch"]
# v0.9.127.23 — jump_f4 has an AI glitch (the dog suddenly wears
# sunglasses mid-air); the jump sheet uses the clean 5 frames only.
FRAME_PICK = {"jump": [1, 2, 3, 5, 6]}
FRAMES = 6
CELL_W, CELL_H = 150, 120
TARGET_H = 104          # scaled content height (the dog body)
BASELINE = CELL_H - 4   # content bottom sits 4 px above the cell edge
ALPHA_MIN = 40
GAP_ROWS = 12           # min empty rows that separate a caption band


def drop_caption_band(alpha):
    """Zero out a bottom content band separated from the rest by a
    vertical gap (the baked-in "FRAME N of 6" caption)."""
    rows = (alpha > ALPHA_MIN).any(axis=1)
    yidx = np.where(rows)[0]
    if len(yidx) < 2:
        return alpha
    d = np.diff(yidx)
    gaps = np.where(d > GAP_ROWS)[0]
    if len(gaps) == 0:
        return alpha
    cut_from = int(yidx[gaps[-1] + 1])   # first row of the bottom band
    alpha[cut_from:, :] = 0
    return alpha


def frame_alpha(path):
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im)[:, :, 3].copy()
    return im, drop_caption_band(a)


def union_bbox(masks):
    ys, xs = [], []
    for m in masks:
        yidx, xidx = np.where(m)
        ys.append((yidx.min(), yidx.max()))
        xs.append((xidx.min(), xidx.max()))
    return (min(x[0] for x in xs), min(y[0] for y in ys),
            max(x[1] for x in xs), max(y[1] for y in ys))


def main():
    for act in ACTIONS:
        picks = FRAME_PICK.get(act, list(range(1, FRAMES + 1)))
        frames, masks = [], []
        for f in picks:
            im, a = frame_alpha(os.path.join(SRC, f"{act}_f{f}.png"))
            frames.append(im)
            masks.append(a > ALPHA_MIN)
        x0, y0, x1, y1 = union_bbox(masks)
        uw, uh = x1 - x0 + 1, y1 - y0 + 1
        scale = TARGET_H / uh
        sw, sh = round(uw * scale), round(uh * scale)
        n = len(picks)
        if sw > CELL_W:
            raise SystemExit(f"{act}: scaled width {sw} > cell {CELL_W}")
        sheet = Image.new("RGBA", (CELL_W * n, CELL_H), (0, 0, 0, 0))
        px0 = (CELL_W - sw) // 2
        py0 = BASELINE - sh
        for i, (im, a) in enumerate(zip(frames, masks)):
            crop = im.crop((x0, y0, x1 + 1, y1 + 1)).copy()
            crop.putalpha(Image.fromarray(a[y0:y1 + 1, x0:x1 + 1]))
            crop = crop.resize((sw, sh), Image.LANCZOS)
            sheet.paste(crop, (i * CELL_W + px0, py0), crop)
        out = os.path.join(OUT_DIR, f"dog_{act}_sheet.png")
        sheet.save(out)
        print(f"{act}: frames={picks} union=({x0},{y0})-({x1},{y1}) "
              f"{uw}x{uh} scale={scale:.3f} -> {out} {sheet.size}")
    print("DONE")


if __name__ == "__main__":
    main()
