# -*- coding: utf-8 -*-
"""Generate the v0.9.127.22 GAME-STYLE dog sprite set with the project's
AI image engine (user: 「動作再多一點、連貫性再多一點、可以轉正面」).

Three sheets, all sharing ONE character design:
  run   — 12 sequential gallop frames in a 4x3 grid (smoother cycle)
  turn  — 3 frames turning from side view to facing the camera
  sitf  — 3 frames sitting FRONT-facing, panting at the camera

Each kind is generated twice (candidates) and the best is picked by a
human/AI look before slicing. Raw files: gui/assets/dog2_raw_<kind>_<n>.png
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from src import config  # noqa: F401,F403  (loads .env)

os.environ["IMAGEGEN_MODE"] = "live"
os.environ["IMAGEGEN_WRITES_ENABLED"] = "on"

from src.imagegen_tools import ImageGenEngine  # noqa: E402

CHAR = ("ONE cute fluffy grey cartoon puppy with a white chest patch, "
        "soft thick outlines, big expressive eyes, tiny red tongue, "
        "flat vector cartoon style")

PROMPTS = {
    "run": (
        "A sprite sheet of %s RUNNING at full gallop, side view facing "
        "right, floppy ears. Exactly 12 sequential animation frames of "
        "ONE complete gallop cycle arranged in a 4x3 GRID, reading order "
        "left-to-right then next row, EQUAL cell spacing, every dog "
        "vertically centered in its cell, fully TRANSPARENT background. "
        "CRITICAL: the four legs must be in a CLEARLY DIFFERENT running "
        "phase in every frame — front legs stretched far forward, then "
        "tucked under the body, then kicking back; hind legs extended, "
        "gathered, pushing off — so consecutive frames animate as a "
        "smooth continuous gallop with visible body bounce. The "
        "character design, colors, proportions and body size are "
        "IDENTICAL across all 12 frames. No text, no ground shadow, no "
        "background elements, no borders, no grid lines, no frame "
        "numbers." % CHAR
    ),
    "turn": (
        "A sprite sheet of %s TURNING to face the camera. Exactly 3 "
        "sequential frames arranged in ONE horizontal row, EQUAL "
        "spacing, fully TRANSPARENT background. Frame 1: full side view "
        "facing right. Frame 2: three-quarter view, head beginning to "
        "turn toward the camera. Frame 3: fully FRONT-facing, looking "
        "straight at the camera, standing on all fours with a happy "
        "expression. The character design, colors and body size are "
        "IDENTICAL across the 3 frames. No text, no ground shadow, no "
        "background elements, no borders, no grid lines, no frame "
        "numbers." % CHAR
    ),
    "sitf": (
        "A sprite sheet of %s SITTING and facing the CAMERA (front "
        "view). Exactly 3 sequential panting frames arranged in ONE "
        "horizontal row, EQUAL spacing, fully TRANSPARENT background. "
        "The puppy sits upright on its haunches looking straight at "
        "the viewer, mouth open with the tiny red tongue out, happily "
        "panting; each frame differs only slightly in tongue position "
        "and chest heave so the sequence loops as smooth breathing "
        "animation. The character design, colors and body size are "
        "IDENTICAL across the 3 frames. No text, no ground shadow, no "
        "background elements, no borders, no grid lines, no frame "
        "numbers." % CHAR
    ),
}

ASPECT = {"run": "4:3", "turn": "16:9", "sitf": "16:9"}

eng = ImageGenEngine()
total_cost = 0.0
wanted = os.environ.get("DOG_KINDS", "run,turn,sitf").split(",")
for kind, prompt in PROMPTS.items():
    if kind not in wanted:
        continue
    try:
        r = eng.generate(prompt, confirm=True, n=2,
                         model="recraft/recraft-v4.1-flash",
                         aspect_ratio=ASPECT[kind], output_format="png")
    except Exception as e:  # noqa: BLE001 — keep other kinds alive
        print("[%s] GENERATION FAILED: %s" % (kind, e))
        continue
    meta = {k: v for k, v in r.items() if k not in ("images",)}
    print("[%s]" % kind, json.dumps(meta, ensure_ascii=False,
                                    default=str)[:400])
    cost = float((r.get("usage") or {}).get("cost") or 0)
    total_cost += cost
    if not r.get("ok"):
        print("[%s] not ok, skipping" % kind)
        continue
    for i, im in enumerate(r["images"], 1):
        path = os.path.join("gui", "assets",
                            "dog2_raw_%s_%d.png" % (kind, i))
        with open(path, "wb") as fh:
            fh.write(im["bytes"])
        print("saved:", path, len(im["bytes"]), "bytes")
print("total cost_usd:", round(total_cost, 4))