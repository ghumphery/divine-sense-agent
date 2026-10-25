# -*- coding: utf-8 -*-
"""v0.9.131.0 — the 18-behaviour STATE-MACHINE sheet set (dog4).

Rebuilds the dog sprite library from the user's 20 std_*.gif files in
C:\\Users\\humphery\\images\\ (one behaviour each, 2 frames, 512x512,
OPAQUE white background — validated: all frames have alpha==255 after
convert('RGB'), so the dog3 flood-fill pipeline applies unchanged).

Orientation was validated by the zoom contact sheet + the red-collar
centroid heuristic: MOST behaviours face LEFT natively and are
mirrored to the RIGHT-facing sprint convention. THREE exceptions do
NOT mirror:
  spin  — natively RIGHT (the spin story reads either way)
  lift  — front-facing (held up by hands; mirroring is meaningless)
  tilt  — front-facing (the head-tilt IS the story)
  tail  — the tail-chase curls through different body orientations
          (mirroring the group is meaningless, v0.9.128.0 rule)

run/walk use FIT mode (per-frame bbox AREA EQUALISATION — a gallop
whose frames differ in size reads as flickering, the v0.9.127.28
lesson); everything else uses UNION mode (natural size changes are
part of the behaviour: lie down / roll / stretch).
"""
import os
from collections import deque

import numpy as np
from PIL import Image

import _build_dog3_sheets as B

TOL = 32            # max L1 colour distance between adjacent bg pixels
MIN_COMPONENT = 40  # drop dither/speck components < 40 px (dog3 rule)


def _grow(arr, bg, tol):
    """One vectorised region-growing pass: expand bg into adjacent
    pixels whose colour distance to the spawning pixel is <= tol."""
    grew = np.zeros_like(bg)
    okv = np.abs(arr[1:, :, :] - arr[:-1, :, :]).sum(axis=2) <= tol
    okh = np.abs(arr[:, 1:, :] - arr[:, :-1, :]).sum(axis=2) <= tol
    up = np.zeros_like(bg)
    up[:-1, :] = bg[1:, :]
    grew[:-1, :] |= up[:-1, :] & okv
    dn = np.zeros_like(bg)
    dn[1:, :] = bg[:-1, :]
    grew[1:, :] |= dn[1:, :] & okv
    lf = np.zeros_like(bg)
    lf[:, :-1] = bg[:, 1:]
    grew[:, :-1] |= lf[:, :-1] & okh
    rt = np.zeros_like(bg)
    rt[:, 1:] = bg[:, :-1]
    grew[:, 1:] |= rt[:, 1:] & okh
    return grew & ~bg


def remove_background_std(fr):
    """Border region-growing flood (coloured paper backdrops), numpy
    vectorised: the flood seeds on a 1/4-resolution grid, then refines
    at full resolution. Returns (RGBA image, bool dog mask) — the dog3
    remove_background contract."""
    rgba = fr.convert('RGBA')
    arr = np.asarray(rgba.convert('RGB')).astype(np.int16)
    h, w, _ = arr.shape
    k = 4
    sh, sw = h // k, w // k
    small = arr[:sh * k:k, :sw * k:k]
    bgs = np.zeros((sh, sw), bool)
    bgs[0, :] = bgs[-1, :] = bgs[:, 0] = bgs[:, -1] = True
    for _ in range(2000):
        new = _grow(small, bgs, TOL)
        if not new.any():
            break
        bgs |= new
    bg = np.zeros((h, w), bool)
    bg[0, :] = bg[-1, :] = bg[:, 0] = bg[:, -1] = True
    for yy in range(sh):
        for xx in range(sw):
            if bgs[yy, xx]:
                bg[yy * k:(yy + 1) * k, xx * k:(xx + 1) * k] = True
    # PYRAMID refinement: 1/2 -> full resolution, each level seeded by
    # the upsampled previous mask so the full-res pass needs only a
    # couple of iterations (the naive single-scale loop could spin for
    # thousands of iterations across the paper texture's gradients).
    half = arr[::2, ::2]
    bh, bw = h // 2, w // 2
    bg2 = np.zeros((bh, bw), bool)
    bg2[0, :] = bg2[-1, :] = bg2[:, 0] = bg2[:, -1] = True
    for yy in range(bh):
        for xx in range(bw):
            if bg[yy * 2, xx * 2]:
                bg2[yy, xx] = True
    for _ in range(50):
        new = _grow(half, bg2, TOL)
        if not new.any():
            break
        bg2 |= new
    up2 = np.repeat(np.repeat(bg2, 2, axis=0), 2, axis=1)
    bg[:up2.shape[0], :up2.shape[1]] |= up2
    for _ in range(50):
        new = _grow(arr, bg, TOL)
        if not new.any():
            break
        bg |= new
    dog = ~bg
    # 1px erosion kills the anti-alias halo (dog3 rule)
    er = dog.copy()
    er[1:, :] &= dog[:-1, :]
    er[:-1, :] &= dog[1:, :]
    er[:, 1:] &= dog[:, :-1]
    er[:, :-1] &= dog[:, 1:]
    alpha = np.where(er, 255, 0).astype(np.uint8)
    out = rgba.copy()
    out.putalpha(Image.fromarray(alpha))
    return out, er

# (sheet, gif, mirror, order, mode)
SHEETS = [
    ('dog_run_sheet.png',     'std_run.gif',     True,  [0, 1], 'fit'),
    ('dog_walk_sheet.png',    'std_walk.gif',    True,  [0, 0], 'fit'),
    ('dog_hop_sheet.png',     'std_hop.gif',     True,  [0, 1], 'union'),
    ('dog_spin_sheet.png',    'std_spin.gif',    False, [0, 1], 'union'),
    ('dog_ball_sheet.png',    'std_ball.gif',    True,  [0, 1], 'union'),
    ('dog_bone_sheet.png',    'std_bone.gif',    True,  [0, 0], 'union'),
    ('dog_lift_sheet.png',    'std_lift.gif',    False, [0, 1], 'union'),
    # putdown: std_putdown f1 has white ornament fragments — the CLEAN
    # sitting pose of std_tilt f1 stands in (put down -> sits = the
    # same visual contract, and the idle hub freezes this sheet's f1)
    ('dog_putdown_sheet.png', 'std_tilt.gif',     False, [1, 1], 'union'),
    ('dog_throw_sheet.png',   'std_throw.gif',   True,  [0, 1], 'union'),
    ('dog_belly_sheet.png',   'std_belly.gif',   True,  [0, 1], 'union'),
    ('dog_bark_sheet.png',    'std_bark.gif',    True,  [0, 1], 'union'),
    ('dog_lick_sheet.png',    'std_lick.gif',    True,  [0, 1], 'union'),
    ('dog_yawn_sheet.png',    'std_yawn.gif',    True,  [0, 1], 'union'),
    ('dog_bite_sheet.png',    'std_bite.gif',    True,  [0, 0], 'union'),
    ('dog_sleep_sheet.png',   'std_sleep.gif',   True,  [0, 1], 'union'),
    ('dog_lie_sheet.png',     'std_lie.gif',     True,  [1, 1], 'union'),
    ('dog_stretch_sheet.png', 'std_stretch.gif', True,  [0, 1], 'union'),
    ('dog_tilt_sheet.png',    'std_tilt.gif',    False, [0, 1], 'union'),
    ('dog_roll_sheet.png',    'std_roll.gif',    True,  [0, 1], 'union'),
    ('dog_tail_sheet.png',    'std_tail.gif',    False, [0, 1], 'union'),
]


def main():
    cache = {}
    for _, gif, mirror, _, _ in SHEETS:
        if gif in cache:
            continue
        frames = B.load_gif_frames(os.path.join(B.SRC, gif), mirror=mirror)
        done = []
        for fr in frames:
            im, dog = remove_background_std(fr)
            done.append((im, dog))
            print('%s frame: dog px=%d' % (gif, int(dog.sum())))
        cache[gif] = done
    for out_name, gif, mirror, order, mode in SHEETS:
        cells = [cache[gif][i] for i in order]
        n = len(order)
        if mode == 'fit':
            sheet, areas, s = B.build_fit_sheet(cells, n)
            print('%s: %s%s fit areas=%s scale=%.3f -> %s'
                  % (out_name, gif, order, areas, s, sheet.size))
        else:
            masks = [c[1] for c in cells]
            x0, y0, x1, y1 = B.union_bbox(masks)
            uw, uh = x1 - x0 + 1, y1 - y0 + 1
            scale = min(B.TARGET_H / uh, (B.CELL_W - 4) / uw)
            sw, sh = round(uw * scale), round(uh * scale)
            if sw > B.CELL_W:
                raise SystemExit(
                    '%s: scaled width %d > cell %d'
                    % (out_name, sw, B.CELL_W))
            sheet = Image.new('RGBA', (B.CELL_W * n, B.CELL_H),
                              (0, 0, 0, 0))
            px0 = (B.CELL_W - sw) // 2
            py0 = B.BASELINE - sh
            for i, (im, a) in enumerate(cells):
                crop = im.crop((x0, y0, x1 + 1, y1 + 1)).copy()
                crop.putalpha(Image.fromarray(
                    (a[y0:y1 + 1, x0:x1 + 1] * 255).astype(np.uint8)))
                crop = crop.resize((sw, sh), Image.LANCZOS)
                sheet.paste(crop, (i * B.CELL_W + px0, py0), crop)
            print('%s: %s%s union=%dx%d scale=%.3f -> %s'
                  % (out_name, gif, order, uw, uh, scale, sheet.size))
        out = os.path.join(B.OUT_DIR, out_name)
        sheet.save(out, compress_level=1)
        print('    -> %d bytes' % os.path.getsize(out))
    # the four OBSOLETE sheets of the old 16-set are gone
    for old in ('dog_jump_sheet.png', 'dog_sit_sheet.png',
                'dog_tongue_sheet.png', 'dog_scratch_sheet.png'):
        p = os.path.join(B.OUT_DIR, old)
        if os.path.exists(p):
            os.remove(p)
            print('removed %s' % old)
    print('DONE')


if __name__ == '__main__':
    main()
