# -*- coding: utf-8 -*-
"""Slice the AI-generated 2x4 gallop sheet (dog_ai_raw_2.png) into the
production 8-frame horizontal sprite (dog_sprite.png, 608x76).
Keeps each frame's own vertical offset (the gallop bounce), fits each
frame inside a 76x76 cell, and flood-fill-removes the white background.
"""
import io
import sys

from PIL import Image, ImageDraw

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

SRC = r"gui\assets\dog_ai_raw_2.png"
OUT = r"gui\assets\dog_sprite.png"
CELL = 76
COLS, ROWS = 4, 2

img = Image.open(SRC).convert("RGBA")
W, H = img.size
print("src:", img.size, img.mode)

# ── flood-fill the white background away from the four corners ──
px = img.load()
corner = px[2, 2][:3]
print("corner colour:", corner)
if corner[0] > 240 and corner[1] > 240 and corner[2] > 240:
    WHITE = (255, 255, 255, 255)
    visited = bytearray(W * H)
    stack = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]
    # sample: treat "near-white AND connected to the border" as bg
    def near_white(p):
        r, g, b, a = p
        return r > 235 and g > 235 and b > 235
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
        stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    print("background removed (flood fill)")

# ── equal 2x4 grid slice, keep per-frame vertical position ──
cw, ch = W // COLS, H // ROWS
sheet = Image.new("RGBA", (CELL * (COLS * ROWS), CELL), (0, 0, 0, 0))
out_i = 0
for r in range(ROWS):
    for c in range(COLS):
        gx0, gy0 = c * cw, r * ch
        cell_img = img.crop((gx0, gy0, gx0 + cw, gy0 + ch))
        bbox = cell_img.getbbox()
        if not bbox:
            print("WARN: empty cell r%d c%d" % (r, c))
            continue
        fx0, fy0, fx1, fy1 = bbox
        fw, fh = fx1 - fx0, fy1 - fy0
        s = min(64.0 / fh, 74.0 / fw, 1.0)   # UNIFORM frame height
        nw, nh = max(1, int(fw * s)), max(1, int(fh * s))
        frame = cell_img.crop(bbox).resize((nw, nh), Image.LANCZOS)
        # ground the frame on a shared baseline (AI drew the two rows
        # at different sizes; uniform height + baseline keeps the
        # perceived body size steady across the gallop cycle)
        idx = out_i
        x0 = idx * CELL + max(0, min(CELL - nw, (CELL - nw) // 2))
        y0 = CELL - nh - 3
        sheet.paste(frame, (x0, y0), frame)
        out_i += 1
        print("frame r%d c%d: bbox=%dx%d -> %dx%d at (%d,%d)"
              % (r, c, fw, fh, nw, nh, x0, y0))

print("frames placed:", out_i)
sheet.save(OUT)
print("saved:", OUT, sheet.size)