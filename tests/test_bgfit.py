"""v0.9.99.2 — 背景圖佈放格式（cover/contain/center/tile）。

Covers:
* index.html — 佈放格式 select（#ap-bg-fit）4 選項 + i18n hooks
* app.js — st.bgFit persisted + body.dataset.bgFit + openAppearance
  restores the select + _applyFromUi unified applier
* style.css — body.has-bg data-bg-fit blocks (cover default,
  contain/center/tile)
* i18n keys ×4 locales
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as f:
        return f.read()


class TestBgFitGuards(unittest.TestCase):
    def test_index_fit_selector(self):
        html = _read("gui/assets/index.html")
        self.assertIn('id="ap-bg-fit"', html)
        for v in ("cover", "contain", "center", "tile"):
            self.assertIn(f'value="{v}"', html)
        self.assertIn('data-i18n="appearanceBgFitCover"', html)

    def test_app_js_bgfit_logic(self):
        js = _read("gui/assets/app.js")
        self.assertIn("st.bgFit =\n", js)  # _applyFromUi sets it
        self.assertIn("dataset.bgFit = fit", js)
        self.assertIn("delete document.body.dataset.bgFit", js)
        self.assertIn("getElementById('ap-bg-fit')", js)
        self.assertIn("st.bgFit || 'cover'", js)

    def test_css_fit_blocks(self):
        css = _read("gui/assets/style.css")
        self.assertIn("body.has-bg {", css)
        self.assertIn("background-size: cover;", css)
        for v in ("contain", "center", "tile"):
            self.assertIn(f'data-bg-fit="{v}"', css)
        self.assertIn("background-attachment: fixed", css)

    def test_i18n_keys_in_all_four_locales(self):
        js = _read("gui/assets/app.js")
        for key in ("appearanceBgFit:", "appearanceBgFitCover:",
                    "appearanceBgFitContain:", "appearanceBgFitCenter:",
                    "appearanceBgFitTile:"):
            self.assertEqual(js.count(key), 4, key)


if __name__ == "__main__":
    unittest.main()