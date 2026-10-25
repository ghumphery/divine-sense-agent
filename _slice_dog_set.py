# -*- coding: utf-8 -*-
"""Slice the v0.9.127.22 AI dog set into three production sheets (CELL
120px — the user asked for a BIGGER dog):
  dog2_raw_run_2.png  (13 dogs, rows of 4/4/4/1) -> dog_run_sheet.png
  dog2_raw_turn_1.png (3 dogs, one row)          -> dog_turn_sheet.png
  dog2_raw_sitf_1.png (3 dogs, one row)          -> dog_sitf_sheet.png
The AI does NOT align dogs to any grid, so slicing uses PROJECTION
SEGMENTATION instead of fixed cells: flood-fill the white background
away, split rows by y-projection gaps, split dogs within a row by
x-projection gaps, reading order rows top->bottom then left->right.
Each dog is then scaled to a uniform frame height on a shared baseline
(keeps perceived body size steady across frames).
"""
import io
import sys

from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

CELL = 120
UNIFORM_H = 100          # every frame scaled to this height
MAX_W = CELL - 4         # never overflow the cell
ALPHA_MIN = 40           # ignore faint antialias halos in projections
JOBS = [
    (r"gui\assets\dog2_raw_run_2.png",  r"gui\assets\dog_run_sheet.png"),
    (r"gui\assets\dog2_raw_turn_1.png", r"gui\assets\dog_turn_sheet.png"),
    (r"gui\assets\dog2_raw_sitf_1.png", r"gui\assets\dog_sitf_sheet.png"),
]


def flood_clear_white(img):
    px = img.load()
    W, H = img.size
    corner = px[2, 2][:3]
    print("  corner colour:", corner)
    if not (corner[0] > 240 and corner[1] > 240 and corner[2] > 240):
        return 0
    cleared = 0

    def near_white(p):
        r, g, b, a = p
        return r > 235 and g > 235 and b > 235

    visited = bytearray(W * H)
    stack = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= W or y >= H:
            continue
        idx = y * W + x
        if visited[idx]:
            continue
        visited[idx] = 1
        if not near_white(px[x, y]):
            continue
        px[x, y] = (255, 255, 255, 0)
        cleared += 1
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return cleared


def _bands(mask, min_gap):
    """Split 0/1 list into (start, end) bands separated by >= min_gap
    zeros (projection segmentation)."""
    bands = []
    start = None
    gap = 0
    for i, v in enumerate(mask):
        if v:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= min_gap:
                bands.append((start, i - gap + 1))
                start = None
    if start is not None:
        bands.append((start, len(mask)))
    return bands


def segment_dogs(img):
    """Return dog crops in reading order (rows top->bottom, left->right)."""
    W, H = img.size
    px = img.load()
    alpha = [[1 if px[x, y][3] >= ALPHA_MIN else 0
              for x in range(W)] for y in range(H)]
    row_mask = [1 if any(row) else 0 for row in alpha]
    rows = _bands(row_mask, min_gap=4)
    dogs = []
    for r0, r1 in rows:
        col_mask = [1 if any(alpha[y][x] for y in range(r0, r1)) else 0
                    for x in range(W)]
        for c0, c1 in _bands(col_mask, min_gap=3):
            sub = img.crop((c0, r0, c1, r1))
            bbox = sub.getbbox()
            if not bbox:
                continue
            sub = sub.crop(bbox)
            # antialias specks survive the flood — keep real dogs only
            if sub.size[0] >= 30 and sub.size[1] >= 30:
                dogs.append(sub)
    return dogs


for SRC, OUT in JOBS:
    img = Image.open(SRC).convert("RGBA")
    print("src:", SRC, img.size, img.mode)
    n = flood_clear_white(img)
    print("  background cleared px:", n)
    dogs = segment_dogs(img)
    print("  segmented dogs:", len(dogs))
    for i, d in enumerate(dogs):
        print("    dog %d: %dx%d" % (i, d.size[0], d.size[1]))

    sheet = Image.new("RGBA", (CELL * len(dogs), CELL), (0, 0, 0, 0))
    for idx, frame in enumerate(dogs):
        fw, fh = frame.size
        s = min(float(UNIFORM_H) / fh, float(MAX_W) / fw, 1.0)
        nw, nh = max(1, int(fw * s)), max(1, int(fh * s))
        frame = frame.resize((nw, nh), Image.LANCZOS)
        x0 = idx * CELL + max(0, min(CELL - nw, (CELL - nw) // 2))
        y0 = CELL - nh - 4      # shared baseline
        sheet.paste(frame, (x0, y0), frame)
        print("  frame %d: %dx%d -> %dx%d at (%d,%d)"
              % (idx, fw, fh, nw, nh, x0, y0))
    sheet.save(OUT)
    print("saved:", OUT, sheet.size)

print("done")