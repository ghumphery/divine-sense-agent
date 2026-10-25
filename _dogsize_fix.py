"""v0.9.133.4 — dog _bw asset SIZE NORMALIZATION (full rebuild).

User report: the dog's visible size jumps between actions. Root cause:
the 8 v0.9.133.1 AI assets shipped with three DIFFERENT canvases
(512x512 / 1024x1024 / 300x300) and are painted with
`background-size: 100% 100%` into the 150x120 (5:4) .dog-skin box:
  * non-5:4 canvases get non-uniformly stretched (dogs squashed),
  * the dog bbox occupy ratio varies wildly (27%..47%) -> the sprite
    visibly changes size on every action switch.

Pipeline (source of truth = gui/assets/_bw_orig/, the pre-debg copies
backed up in v0.9.133.2):
  1. corner flood-fill de-background (TOL=40, corners majority vote,
     1px feather erode) — the v0.9.133.2 params;
  2. normalize: uniform 500x400 canvas (5:4 == the skin box, so
     `100% 100%` becomes proportion-preserving), dog union-bbox long
     edge scaled to 78% of the canvas width (78% = cross-sheet median
     long-edge ratio of the 17 built sheets), centered horizontally,
     bottom on the 96.7% ground line; one common scale+offset per file
     (per-frame motion offsets preserved);
  3. GIF frames saved as P with index 255 reserved for transparency
     (alpha <= 128 -> index 255), disposal=2 — this is the step the
     first normalization pass got wrong (alpha silently dropped).

Sheets (dog_*_sheet.png) are NOT touched: cell content heights already
agree at 104/120 across 16/17 assets (belly 85 / lie 96 / throw 91 are
natural poses).
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageSequence

ASSETS = "gui/assets"
ORIG = os.path.join(ASSETS, "_bw_orig")
OUT_W, OUT_H = 500, 400
TARGET = 0.78 * OUT_W
GROUND_Y = int(OUT_H * 0.967)
TOL = 40

FILES = [
    "dog_dizzy_bw.gif", "dog_lick_bw.gif", "dog_lie_bw.gif",
    "dog_throw_bw.gif", "dog_yawn_bw.gif",
    "dog_happy_bw.png", "dog_lift_bw.png", "dog_sleep_bw.png",
]


def load_orig(name):
    path = os.path.join(ORIG, name)
    im = Image.open(path)
    durs = []
    frames = []
    if name.lower().endswith(".gif"):
        for fr in ImageSequence.Iterator(im):
            durs.append(int(im.info.get("duration", 90) or 90))
            frames.append(fr.convert("RGBA"))
    else:
        frames = [im.convert("RGBA")]
        durs = [90]
    return frames, durs


def corner_bg(ra):
    rgb = ra.convert("RGB")
    cs = [rgb.getpixel(c) for c in
          [(0, 0), (rgb.width - 1, 0), (0, rgb.height - 1),
           (rgb.width - 1, rgb.height - 1)]]
    # majority vote
    best, bestc = None, -1
    for c in set(cs):
        n = cs.count(c)
        if n > bestc:
            best, bestc = c, n
    return best


def de_bg(ra):
    """Flood-fill the connected background from all four corners."""
    bg = corner_bg(ra)
    im = ra.copy()
    w, h = im.size
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        try:
            ImageDraw.floodfill(
                im, seed, (0, 0, 0, 0), thresh=TOL, border=None)
        except Exception:
            pass
    # 1px feather: erode the alpha edge
    a = im.split()[-1].filter(ImageFilter.MinFilter(3))
    im.putalpha(a)
    return im


def to_p_transparent(rgba):
    """P frame with index 255 == fully transparent (alpha <= 128)."""
    alpha = rgba.split()[-1]
    mask = alpha.point(lambda v: 255 if v <= 128 else 0, mode="L")
    p = rgba.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
    p.paste(255, mask)
    return p


def normalize(name):
    frames, durs = load_orig(name)
    frames = [de_bg(f) for f in frames]

    ub = None
    for fr in frames:
        b = fr.split()[-1].getbbox()
        if b is None:
            continue
        ub = list(b) if ub is None else [
            min(ub[0], b[0]), min(ub[1], b[1]),
            max(ub[2], b[2]), max(ub[3], b[3])]
    if ub is None:
        print(name, ": fully transparent — skipped")
        return
    uw, uh = ub[2] - ub[0], ub[3] - ub[1]
    scale = TARGET / float(max(uw, uh))
    cx = (ub[0] + ub[2]) / 2.0
    bottom = ub[3]

    outs = []
    for fr in frames:
        b = fr.split()[-1].getbbox()
        if b is None:
            outs.append(Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0)))
            continue
        piece = fr.crop(b)
        piece = piece.resize(
            (max(1, round(piece.width * scale)),
             max(1, round(piece.height * scale))), Image.LANCZOS)
        canvas = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
        dxn = ((b[0] + b[2]) / 2.0 - cx) * scale
        left = OUT_W / 2.0 - piece.width / 2.0 + dxn
        new_bottom = GROUND_Y - (bottom - b[3]) * scale
        top = new_bottom - piece.height
        canvas.alpha_composite(
            piece, (int(round(left)), int(round(top))))
        outs.append(canvas)

    path = os.path.join(ASSETS, name)
    if name.lower().endswith(".gif"):
        p_frames = [to_p_transparent(f) for f in outs]
        p_frames[0].save(
            path, save_all=True, loop=0, duration=durs,
            append_images=p_frames[1:], transparency=255, disposal=2)
    else:
        outs[0].save(path, format="PNG")

    b2 = outs[0].split()[-1].getbbox()
    le = max(b2[2] - b2[0], b2[3] - b2[1]) if b2 else 0
    a = outs[0].split()[-1]
    trans = sum(1 for v in a.getdata() if v < 10)
    kb = os.path.getsize(path) // 1024
    print(f"{name}: {len(outs)}f scale={scale:.3f} long_edge={le}px "
          f"({le/OUT_W*100:.0f}%) trans={trans/(OUT_W*OUT_H)*100:.0f}% "
          f"{kb}KB")


for n in FILES:
    normalize(n)
print("done")