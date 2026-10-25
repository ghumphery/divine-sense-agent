# -*- coding: utf-8 -*-
"""Generate the SITTING/waiting dog frames (v0.9.127.21 second action
— user: 「動作很單調」). AI image engine LIVE; raw saved for slicing.
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

PROMPT = (
    "A sprite sheet of ONE cute fluffy grey cartoon puppy SITTING on "
    "its hind legs, panting happily, side view facing right. Bold "
    "flat vector cartoon style with THICK DARK OUTLINES and vivid "
    "colours, a big eye, a tiny red tongue sticking out of an open "
    "dark mouth. Exactly 2 sequential animation frames in ONE "
    "horizontal row, EQUAL spacing, vertically centered, fully "
    "TRANSPARENT background. IDENTICAL character design in both "
    "frames; frame 1 mouth slightly open, frame 2 tongue fully out "
    "panting. No text, no ground shadow, no background elements, no "
    "borders, no grid lines."
)

eng = ImageGenEngine()
r = eng.generate(
    PROMPT, confirm=True, n=1, model="recraft/recraft-v4.1-flash",
    aspect_ratio="1:1", output_format="png",
)
print(json.dumps({k: v for k, v in r.items()
                  if k not in ("images",)}, ensure_ascii=False,
                 default=str)[:600])
if not r.get("ok"):
    print("GENERATION FAILED")
    sys.exit(1)

for i, im in enumerate(r["images"], 1):
    path = os.path.join("gui", "assets", f"dog_sit_raw_{i}.png")
    with open(path, "wb") as fh:
        fh.write(im["bytes"])
    print("saved:", path, len(im["bytes"]), "bytes,", im["media_type"])
print("cost_usd:", (r.get("usage") or {}).get("cost"))