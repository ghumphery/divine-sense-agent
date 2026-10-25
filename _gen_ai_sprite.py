# -*- coding: utf-8 -*-
"""Generate the running-dog sprite sheet with the project's AI image
engine (v0.9.127.21 — user: 「圖片可以請 AI 進行製作嗎」). Uses
ImageGenEngine (OpenRouter Image API, recraft) LIVE; saves the raw
result(s) for visual inspection — the grid-slicing step runs after a
human/AI look at the raw frames.
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
    "A sprite sheet of ONE cute fluffy grey cartoon puppy RUNNING, "
    "side view facing right. Exactly 8 sequential gallop-cycle "
    "animation frames arranged in ONE horizontal row, EQUAL spacing, "
    "vertically centered, fully TRANSPARENT background. Flat vector "
    "cartoon style, soft thick outlines, big expressive eyes, tiny "
    "red tongue out. The character design is IDENTICAL across all 8 "
    "frames; each frame shows all four legs in a DIFFERENT running "
    "phase. No text, no ground shadow, no background elements, no "
    "borders, no grid lines, no frame numbers."
)

eng = ImageGenEngine()
r = eng.generate(
    PROMPT, confirm=True, n=2, model="recraft/recraft-v4.1-flash",
    aspect_ratio="1:1", output_format="png",
)
print(json.dumps({k: v for k, v in r.items()
                  if k not in ("images",)}, ensure_ascii=False,
                 default=str)[:600])
if not r.get("ok"):
    print("GENERATION FAILED")
    sys.exit(1)

for i, im in enumerate(r["images"], 1):
    path = os.path.join("gui", "assets", f"dog_ai_raw_{i}.png")
    with open(path, "wb") as fh:
        fh.write(im["bytes"])
    print("saved:", path, len(im["bytes"]), "bytes,", im["media_type"])
print("cost_usd:", (r.get("usage") or {}).get("cost"))