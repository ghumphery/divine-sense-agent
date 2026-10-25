# -*- coding: utf-8 -*-
"""v0.9.132.7 — the entrance ALWAYS runs IN toward the middle (the dog
is born off-screen-left; the entrance burst is forced rightward and
keeps going until it passes the screen centre; the emoji fallback
parks at the centre instead of the invisible off-screen birth slot)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_dog_runner import _src  # noqa: E402


def test_entrance_runs_toward_centre():
    js = _src("app.js")
    assert ("const moveBurst = function (kind, next, towardCenter) {"
            in js)
    # v0.9.132.8 — towardCenter is a tri-state (true / 'center' / unset)
    assert ("if (towardCenter === true) dir = 1;" in js)
    assert "else if (towardCenter === 'center') {" in js
    assert ("if (towardCenter && x < cx"
            " && Date.now() - started < 5000) {"
            not in js)  # superseded by the tri-state toC check
    assert ("const toC = towardCenter === true" in js)
    # the entrance itself forces towardCenter + runs (not walks) in
    i = js.index("function openWalk() {")
    body = js[i: i + 800]
    assert "moveBurst('run', function () {" in body
    assert "}, true);" in body
    # the emoji fallback parks at the centre (never off-screen)
    i = js.index("function openIdle() {")
    body = js[i: i + 700]
    assert "dog.style.left = Math.max(4, Math.min(" in body