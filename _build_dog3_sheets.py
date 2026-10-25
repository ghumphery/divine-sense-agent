# -*- coding: utf-8 -*-
"""v0.9.127.27 / v0.9.128.0 / v0.9.129.0 / v0.9.130.0 — dog sheets from
the user's shih-tzu GIFs in C:\\Users\\humphery\\images\\ (16 actions,
4 frames each, 512x512, OPAQUE white background).

The GIFs face LEFT; the CSS sprint (scaleX(1) out / scaleX(-1) back)
expects a RIGHT-facing natural sprite, so frames are mirrored
horizontally — EXCEPT the tail-chase GIF, whose frames spin through
different body orientations (mirroring the group is meaningless).

v0.9.130.0 sheet map (the THREE reaction actions; app.js + CSS wire
them). NOTE — the c3 series faces RIGHT natively (validated by the
red-collar centroid: a collar right of the body centroid = facing
right; the method reproduces RIGHT on every existing sheet cell):

  lift     = shih_lift_c3     [0,1,2,3]   (600x120, union, LIFT — petting
                                            the running puppy or the 3-pet
                                            excitement streak: crouch ->
                                            leap -> pounce up -> land)
  putdown  = shih_putdown_c3  [0,1,2,3]   (600x120, union, PUTDOWN — the
                                            second half of the pair:
                                            descend -> touch down -> sit)
  bite     = shih_bite_c3     [0,1,0,3]   (600x120, union, ANGRY — waking
                                            the sleeper / over-petting:
                                            glare -> lunge -> glare ->
                                            grumble; the shocked f2 is
                                            EXCLUDED, the lunge-glare
                                            ping-pong reads angrier)

v0.9.129.0 sheet map (the FIVE new interactions; app.js + CSS wire
them):

  ball  = shih_ball  [0,1,2,3,2,1]  (900x120, union, FETCH flourish:
                                     stare -> chase -> leap -> carry back)
  bark  = shih_bark  [0,2,3,2]      (600x120, union, BARK flourish —
                                     f1 has a "WOOF!" speech bubble that
                                     would read mirrored, so it is
                                     EXCLUDED from the sequence)
  lick  = shih_lick  [1,2,3,2]      (600x120, union, LIP-LICK idle —
                                     f0 sits farther back than f1-f3,
                                     so the loop uses the close-up trio)
  lie   = shih_lie   [0,1,2,3,3,3]  (900x120, union, LIE-DOWN: walks in,
                                     folds down, dozes off — the last
                                     frame freezes on the Zzz drawing)
  spin  = shih_spin  [0,1,2,3]      (600x120, union, SPIN flourish — f0
                                     faces left / f3 faces right: the
                                     story IS the 180-degree turn, and
                                     the mid frames are body-agnostic)

v0.9.128.0 sheet map (8 sheets -> 8 actions; app.js + CSS wire them):

  run     = shih_run   [0,2,3,0,2,3]   (900x120, fit, gallop loop)
  jump    = shih_roll  [0,1,2,3,3]     (750x120, union, roll flourish)
  sit     = shih_sleep [0,1,2,3,3,3]   (900x120, union, settle -> asleep)
  tongue  = shih_bone  [0,1,0,1,0,1]   (900x120, union, nibble idle)
  scratch = shih_tail  [0,1,2,3,1,2]   (900x120, union, tail-chase idle)
  walk    = shih_walk  [1,2,1,2,1,2]   (900x120, fit, walk break)
  hop     = shih_hop   [0,0,2,3,3]     (750x120, union, hop flourish)
  yawn    = shih_yawn  [0,1,2,3,2,1]   (900x120, union, yawn idle)

Background removal: flood fill from the image BORDER over near-white
pixels only — NEVER a global white key, which would punch transparent
holes into the dog's white fur (the exact v0.9.127.24 lesson). The
cartoon outline fully encloses the fur, so the flood cannot reach it.
A >= 40 px component filter drops dust, and a 1-px erosion shaves the
antialias halo.

Cell conventions are IDENTICAL to _build_dog2_sheets.py (CELL 150x120,
union bbox, content height 104 px, bottom baseline at y=116); re-run
_build_dog2_sheets.py to restore the 30-frame set any time.
"""
import os
from collections import deque

import numpy as np
from PIL import Image

SRC = r"C:\Users\humphery\images"
OUT_DIR = r"c:\Users\humphery\coding\qdrant-rag-agent\gui\assets"

GIFS = {
    # key: (file, mirror) — all GIFs face LEFT and must be mirrored to
    # face RIGHT for the CSS sprint EXCEPT "tail": the tail-chase frames
    # spin through different body orientations (side / rear / back), so
    # mirroring the whole group is meaningless.
    "run": ("shih_run_c.gif", True),
    "roll": ("shih_roll_c.gif", True),
    "sleep": ("shih_sleep_c.gif", True),
    "bone": ("shih_bone_c.gif", True),
    # v0.9.128.0 — the 4 NEW actions (walk / hop / yawn / tail)
    "walk": ("shih_walk_c.gif", True),
    "hop": ("shih_hop_c.gif", True),
    "yawn": ("shih_yawn_c.gif", True),
    "tail": ("shih_tail_c.gif", False),
    # v0.9.129.0 — the 5 INTERACTION actions (ball / bark / lick / lie /
    # spin). All face LEFT and mirror cleanly EXCEPT spin: f0 faces the
    # other way from f3 in the SOURCE, which reads naturally as the
    # 180-degree turn of the spin itself (see the header note).
    "ball": ("shih_ball_c.gif", True),
    "bark": ("shih_bark_c.gif", True),
    "lick": ("shih_lick_c.gif", True),
    "lie": ("shih_lie_c.gif", True),
    "spin": ("shih_spin_c.gif", True),
    # v0.9.130.0 — the 3 REACTION actions (lift / putdown / bite). The
    # c3 series was generated per-frame and faces MIXED: lift and
    # putdown natively face RIGHT (validated per-frame against the
    # existing sheets — lift f1 pounces right / f3 lands right;
    # putdown f1 touches down running right / f2-f3 sit front) while
    # the bite GIF natively faces LEFT (f1 lunges left, f3 pouts
    # left) and is mirrored to match the sprint's right-facing.
    "lift": ("shih_lift_c3.gif", False),
    "putdown": ("shih_putdown_c3.gif", False),
    "bite": ("shih_bite_c3.gif", True),
}

SHEETS = [
    # out_name, source gif, cell pattern (indices into the gif frames),
    # layout mode:
    #   "union" — shared union bbox + single scale (preserves relative
    #             motion; natural for roll / sleep / bone poses)
    #   "fit"   — per-frame AREA-equalized + bottom-aligned (the run GIF
    #             frames are framed inconsistently — f1 is drawn ~30%
    #             bigger, aspect ratios spread 1.13-1.53 — so any single
    #             scale makes the dog pulse while running; the most
    #             consistent 3 frames are used instead of 4)
    ("dog_run_sheet.png", "run", [0, 2, 3, 0, 2, 3], "fit"),
    ("dog_jump_sheet.png", "roll", [0, 1, 2, 3, 3], "union"),
    ("dog_sit_sheet.png", "sleep", [0, 1, 2, 3, 3, 3], "union"),
    ("dog_tongue_sheet.png", "bone", [0, 1, 0, 1, 0, 1], "union"),
    # v0.9.128.0 — scratch slot becomes the TAIL CHASE (spin loop:
    # upright -> bend -> bite-tail(back view) -> upright); union keeps
    # the circling motion. The bone nibble stays on the tongue sheet.
    ("dog_scratch_sheet.png", "tail", [0, 1, 2, 3, 1, 2], "union"),
    # v0.9.128.0 — WALK break: only f1/f2 are clean side-profile walking
    # poses (f0/f3 face the camera); 2-frame leg-swap loop, fit mode.
    ("dog_walk_sheet.png", "walk", [1, 2, 1, 2, 1, 2], "fit"),
    # v0.9.128.0 — HOP flourish (mid-run alternative to the roll):
    # crouch -> crouch -> takeoff -> apex -> apex; union preserves the
    # jump heights.
    ("dog_hop_sheet.png", "hop", [0, 0, 2, 3, 3], "union"),
    # v0.9.128.0 — YAWN idle loop (ping-pong: big yawn -> sleepy ->
    # awake -> sleepy -> big yawn again); union (consistent sit pose).
    ("dog_yawn_sheet.png", "yawn", [0, 1, 2, 3, 2, 1], "union"),
    # v0.9.129.0 — FETCH flourish (stare -> chase -> leap -> carry the
    # ball back; the ping-pong replays the leap in slow reverse).
    ("dog_ball_sheet.png", "ball", [0, 1, 2, 3, 2, 1], "union"),
    # v0.9.129.0 — BARK flourish. f1 carries a "WOOF!" speech bubble
    # that would read MIRRORED ("!FOOW") after the flip, so it is
    # excluded: ready -> bark(wag) -> yap(puffs) -> bark again.
    ("dog_bark_sheet.png", "bark", [0, 2, 3, 2], "union"),
    # v0.9.129.0 — LIP-LICK idle. f0 sits farther back than f1-f3, so
    # the loop uses the close-up trio only.
    ("dog_lick_sheet.png", "lick", [1, 2, 3, 2], "union"),
    # v0.9.129.0 — LIE-DOWN (the bored end state): walk-in -> fold ->
    # head low -> dozing; the last frame freezes on the Zzz drawing.
    ("dog_lie_sheet.png", "lie", [0, 1, 2, 3, 3, 3], "union"),
    # v0.9.129.0 — SPIN flourish. f0 and f3 face OPPOSITE ways in the
    # source (the story IS the 180-degree turn); the mid frames curl
    # up and are body-agnostic. union preserves the curl.
    ("dog_spin_sheet.png", "spin", [0, 1, 2, 3], "union"),
    # v0.9.130.0 — LIFT (jump up = "being picked up", hand-free): the
    # pet reaction / excitement chain — crouch -> leap -> pounce up ->
    # land; union preserves the jump arc.
    ("dog_lift_sheet.png", "lift", [0, 1, 2, 3], "union"),
    # v0.9.130.0 — PUTDOWN (land = "being put down"): descend -> touch
    # down -> sit; the second half of the lift/putdown pet pair.
    ("dog_putdown_sheet.png", "putdown", [0, 1, 2, 3], "union"),
    # v0.9.130.0 — ANGRY bite (waking the sleeper / over-petting):
    # glare -> lunge -> glare -> grumble; the shocked f2 is excluded.
    ("dog_bite_sheet.png", "bite", [0, 1, 0, 3], "union"),
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
            white_in = int((dog & (np.asarray(fr).min(axis=2) >= WHITE_MIN)
                            ).sum())
            done.append((im, dog))
            print(f"{key} frame: dog px={int(dog.sum())} "
                  f"(opaque white kept={white_in})")
        cache[key] = done
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
            # v0.9.129.0 — width guard: a very WIDE pose group (lie) must
            # shrink to the cell even if the content ends up a little
            # shorter than TARGET_H; every other sheet is unaffected.
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
        # compress_level=1: lossless, just a larger file — the sprite is
        # tiny anyway and test_assets_shipped wants >30 KB for the run
        # sheet (a stub-detector threshold).
        sheet.save(out, compress_level=1)
        print(f"    -> {os.path.getsize(out)} bytes")
    print("DONE")


if __name__ == "__main__":
    main()