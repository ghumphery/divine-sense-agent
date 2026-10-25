"""Forensics: why didn't offload fire for the huge get_page_html?"""
from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, ".")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
lines = open(os.path.join(ROOT, "gui", "app.log"),
             encoding="utf-8", errors="replace").read().splitlines()
window = [ln for ln in lines
          if " 17:17:" in ln or " 17:18:" in ln or " 17:19:" in ln]
print("=== 17:17-19 lines mentioning offload/compress/create ===")
for ln in window:
    if any(k in ln for k in ("offload", "compress", "create", "tool",
                             "get_page_html", "dispatches")):
        print("  ", ln[:200])

print("=== chat_offloads dirs on disk ===")
for cand in (os.path.join(os.path.expanduser("~"), "chat_offloads"),
             os.path.join(ROOT, "sandbox", "chat_offloads"),
             os.path.join(ROOT, "documents", "chat_offloads")):
    if os.path.isdir(cand):
        files = sorted(glob.glob(os.path.join(cand, "*.txt")))
        print("  ", cand, len(files), "files")
        for f in files[-4:]:
            ts = os.path.getmtime(f)
            import datetime
            print("    ", os.path.basename(f),
                  os.path.getsize(f), "B",
                  datetime.datetime.fromtimestamp(ts).strftime(
                      "%H:%M:%S"))
    else:
        print("  (missing)", cand)

print("=== compressor cfg in the RUNNING app? ===")
import urllib.request
import json as _j
try:
    req = urllib.request.Request(
        "http://127.0.0.1:8334/api/get_runtime_status",
        data=_j.dumps({"args": []}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        print(_j.dumps(_j.loads(r.read().decode()), default=str)[:400])
except Exception as e:
    print("runtime status ERR:", repr(e))