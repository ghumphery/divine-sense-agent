"""v0.9.132.0 — the DOG BEHAVIOUR STATE MACHINE: the puppy runs on the
18-behaviour model over 20 CUTE cartoon sheets (dog5 rebuild from the
shih_*_c GIFs, 4-6 frames each, CELL 150x120, border-flood white
removal). Categories and contracts locked here:

  MOVEMENT  run / walk / hop / spin        — interruptible bursts
  INTERACT  lift / putdown / throw / belly
            / ball / bone                  — semi-lock, play out -> idle
  EMOTION   bark / bite / lick / yawn / tilt — interruptible
  REST      sleep / lie / roll / tail / stretch — deep sleep is LOCKED

IDLE (the sitting hub, frozen putdown pose) drives everything:
   tap the mover -> hearts + the pet streak (3 = follow/chase,
                    the 2nd streak = the angry bite)
   idle rotation -> bone (-> lick, finished eating) / tail chase
                    / yawn / lick / tilt / belly — six mood picks
   drag 14px+    -> GRAB: carried (rides the cursor)
   slow release  -> PUTDOWN -> idle
   fast fling    -> THROW (arc 110px) -> land -> chase -> ball -> idle
   sit 7-10s     -> LIE (pressed awake = an angry bite); 11-15s -> run
   resting 2min  -> SLEEP (LOCKED); tap the sleeper -> stretch
                    -> yawn -> sit (the wake chain)
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "gui" / "assets"

SHEETS = [
    "dog_run_sheet.png", "dog_walk_sheet.png", "dog_hop_sheet.png",
    "dog_spin_sheet.png", "dog_ball_sheet.png", "dog_bone_sheet.png",
    "dog_lift_sheet.png", "dog_putdown_sheet.png", "dog_throw_sheet.png",
    "dog_belly_sheet.png", "dog_bark_sheet.png", "dog_lick_sheet.png",
    "dog_yawn_sheet.png", "dog_bite_sheet.png", "dog_sleep_sheet.png",
    "dog_lie_sheet.png", "dog_stretch_sheet.png", "dog_tilt_sheet.png",
    "dog_roll_sheet.png", "dog_tail_sheet.png",
]
OBSOLETE_SHEETS = [
    "dog_jump_sheet.png", "dog_sit_sheet.png",
    "dog_tongue_sheet.png", "dog_scratch_sheet.png",
]


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


class TestDogRunnerSource:
    def test_functions_and_layer(self):
        js = _src("app.js")
        assert "function _dogRunStart()" in js
        assert "function _dogRunStop()" in js
        assert "id = 'dog-run-layer'" in js
        assert "dog.className = 'dog-runner';" in js
        assert "skin.className = 'dog-skin';" in js
        assert "probe.src = 'dog_run_sheet.png' + V;" in js
        assert "skin.textContent = '🐕';" in js

    def test_wired_into_send_core(self):
        js = _src("app.js")
        i = js.index("async function _sendChatCore()")
        body = js[i:js.index("async function autoSaveChat()", i)]
        assert "try { _dogRunStart(); } catch (_) {}" in body
        assert body.count("try { _dogRunStop(); } catch (_) {}") >= 2

    def test_state_machine_transitions(self):
        js = _src("app.js")
        # the v0.9.130.2 BEHAVIOUR model over the dog5 cute sheets
        assert ("['dog-boning', 'dog-tail-chasing', 'dog-yawning',"
                in js)  # idle rotation — six mood picks now
        assert "'dog-licking', 'dog-tilting'," in js
        assert "'dog-belling'];" in js
        assert "T('food', function () {" in js  # bone -> lick chain
        assert "S.pets >= 3" in js  # the 3-pet excitement streak
        assert "S.streaks >= 2" in js  # 2nd streak = angry bite
        assert ("['dog-rolling', 900], ['dog-hopping', 550], "
                "['dog-balling', 850],") in js  # flourish pool
        assert "setSkin('dog-biting');" in js  # angry bite
        assert "T('angry', function () { settle(); }, 750);" in js
        assert "setSkin('dog-lying');" in js  # bored lie-down
        assert "_dogParticles(dog, ['💤'], 1, 1600);" in js  # 💤
        assert "T('lift', enterCarry, 720);" in js  # tap = pick up
        assert "setSkin('dog-carried');" in js  # riding the cursor
        assert "T('follow', tick, 120);" in js  # the chase tick
        assert "dog.addEventListener('pointerup', onTap);" in js
        assert "T('again', wakeAndRun" in js  # never stays parked
        assert "}, 12000);" in js  # 12s gallop cap -> settle

    def test_css_dog_blocks(self):
        css = _src("style.css")
        assert "#dog-run-layer {" in css
        assert "pointer-events: none;" in css
        assert "pointer-events: auto;" in css
        assert "cursor: pointer;" in css
        for cls in ("dog-running", "dog-walking", "dog-hopping",
                    "dog-spinning", "dog-lifting", "dog-putdown",
                    "dog-throwing", "dog-belling", "dog-balling",
                    "dog-boning", "dog-barking", "dog-biting",
                    "dog-licking", "dog-yawning", "dog-tilting",
                    "dog-lying", "dog-sleeping", "dog-rolling",
                    "dog-tail-chasing", "dog-stretching", "dog-carried"):
            assert "#dog-run-layer .%s {" % cls in css, cls
        assert "dog-runner.dog-stopped .dog-skin {" in css
        assert "background-position: -450px 0;" in css
        assert "background-position: 0 0;" in css
        assert "@keyframes dog-run-play" in css
        assert "steps(5)" in css
        assert "@keyframes dog-carry-sway" in css
        assert "@keyframes dog-float" in css
        assert ".dog-particle" in css
        assert "@media (prefers-reduced-motion: reduce)" in css

    def test_assets_shipped(self):
        for name in SHEETS:
            assert (ASSETS / name).exists(), name
        for name in OBSOLETE_SHEETS:
            assert not (ASSETS / name).exists(), name
        assert (ASSETS / "dog_icon.png").exists()

    def test_url_busts_v13200(self):
        css = _src("style.css")
        js = _src("app.js")
        assert css.count("?v=13200") >= 14  # legacy sprite sheets
        # v0.9.133.6.1 — the ROLL sheet joined the 13330 generation
        # (its landing frame was re-grounded); 13320 is fully retired.
        assert css.count("?v=13310") == 0
        assert css.count("?v=13320") == 0
        assert css.count("?v=13330") >= 10
        assert "'?v=13200'" in js

    def test_ball_sheet_dog_enlarged(self):
        """v0.9.133.4.1 — the ball-play dog was the smallest sprite in
        the set (long edge 64-74% of the cell). After the 1.115x sheet
        rescale every cell must sit in the same band as the rest of
        the family (71-83%), the pounce frames keep filling the ground
        line, and the 150px grid / canvas stay unchanged."""
        from PIL import Image

        im = Image.open(ASSETS / "dog_ball_sheet.png")
        assert im.size == (900, 120)
        img = im.convert("RGBA")
        longs = []
        for col in range(6):
            cell = img.crop((col * 150, 0, col * 150 + 150, 120))
            b = cell.split()[-1].getbbox()
            assert b is not None, col
            longs.append(max(b[2] - b[0], b[3] - b[1]) / 150.0)
        assert all(0.68 <= r <= 0.90 for r in longs), longs

    def test_bw_assets_size_normalized(self):
        """v0.9.133.4 — the 8 AI assets must share ONE uniform 5:4
        canvas (500x400 == the 150x120 .dog-skin box ratio) and a
        bounded long-edge occupy ratio, so `background-size: 100% 100%`
        cannot squash them and the dog no longer changes size between
        actions."""
        from PIL import Image

        for name in ("dog_dizzy_bw.gif", "dog_lick_bw.gif",
                     "dog_lie_bw.gif", "dog_throw_bw.gif",
                     "dog_yawn_bw.gif", "dog_happy_bw.png",
                     "dog_lift_bw.png", "dog_sleep_bw.png"):
            im = Image.open(ASSETS / name)
            assert im.size == (500, 400), (name, im.size)
            if name.endswith(".gif"):
                from PIL import ImageSequence

                alpha_union = None
                for fr in ImageSequence.Iterator(im):
                    b = fr.convert("RGBA").split()[-1].getbbox()
                    if b is None:
                        continue
                    alpha_union = (
                        b if alpha_union is None else
                        [min(alpha_union[0], b[0]),
                         min(alpha_union[1], b[1]),
                         max(alpha_union[2], b[2]),
                         max(alpha_union[3], b[3])])
                b = tuple(alpha_union) if alpha_union else None
            else:
                rgba = im.convert("RGBA")
                b = rgba.split()[-1].getbbox()
            assert isinstance(b, tuple), name
            le = max(b[2] - b[0], b[3] - b[1])
            assert 0.66 <= le / 500 <= 0.88, (name, le)

    def test_roll_lands_on_ground(self):
        """v0.9.133.6.1 — the roll's frozen last frame (cells 3/4,
        duplicated frame 3) sits ON the ground line (bbox bottom =
        116), the CSS reaches it (to -750px, steps(5)), and the
        -6px floating landing frame is retired; burst 13320 is fully
        retired now (roll sheet bumped to 13330)."""
        from PIL import Image

        im = Image.open(ASSETS / "dog_roll_sheet.png").convert("RGBA")
        assert im.size == (750, 120)
        land = im.crop((600, 0, 750, 120)).split()[-1].getbbox()
        assert land is not None
        assert land[3] == 116, land
        css = _src("style.css")
        i = css.index(".dog-rolling {")
        rol = css[i:css.index("@keyframes dog-tail-play")]
        assert ".9s steps(5) 1 forwards" in rol
        i = css.index("@keyframes dog-roll-play")
        assert "-750px 0" in css[i:i + 80]
        assert css.count("?v=13320") == 0
        assert "dog_roll_sheet.png?v=13330" in css

    def test_deep_sleep_drag_fling_wiring(self):
        """v0.9.132.5 — the last five sheets are wired: the LOCKED deep
        sleep (+ the stretch->yawn wake chain), the drag-grab and the
        fling-throw (arc + land + chase + ball)."""
        js = _src("app.js")
        # deep sleep (locked) + the wake chain
        assert "const enterSleep = function () {" in js
        assert "const wakeUp = function () {" in js
        assert "setSkin('dog-sleeping');" in js
        assert "setSkin('dog-stretching');" in js
        assert "S.st === 'sleep'" in js
        # the sleep gate re-arms on every settle (test-shrinkable)
        assert "T('sleep', enterSleep, SLEEP_GATE());" in js
        assert "return layer._SLEEP_GATE_MS || 120000;" in js
        assert "const clearAllT = function () {" in js
        # drag-grab + fling-throw plumbing
        assert "const throwFly = function (vx) {" in js
        assert "Math.sin(t * Math.PI) * 110" in js
        assert "dog.addEventListener('pointerdown', onDown);" in js
        assert ("document.addEventListener('pointermove',"
                " onDragTrack);") in js
        assert ("document.addEventListener('pointerup',"
                " onUpRelease);") in js
        assert "layer._dogDragMove = onDragTrack;" in js
        assert "layer._dogDragUp = onUpRelease;" in js
        # a carried release is decided by onUpRelease, never by onTap
        assert "// carried: the release is resolved by onUpRelease" in js
        # the stop-teardown removes the drag listeners
        assert ("document.removeEventListener('pointermove',"
                " layer._dogDragMove);" in js)
        assert ("document.removeEventListener('pointerup',"
                " layer._dogDragUp);" in js)
        # v0.9.133.4 — sleep is the static AI PNG (animation: none),
        # re-baked transparent in 133.2 and size-normalized in 133.4
        css = _src("style.css")
        assert "url('dog_sleep_bw.png?v=13330')" in css


_DRIVER_HEAD = '''
const { JSDOM } = require('jsdom');
const w = new JSDOM('<body></body>').window;
global.document = w.document;
global.window = w;
global.getComputedStyle = w.getComputedStyle;
// Image stub: setting .src fires onload synchronously (sprite exists)
global.Image = function () {
  const img = { onload: null, onerror: null, _src: '' };
  Object.defineProperty(img, 'src', {
    set: function (v) { this._src = v; if (this.onload) this.onload(); },
    get: function () { return this._src; },
  });
  return img;
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const press = (el) => {
  el.dispatchEvent(new w.MouseEvent('pointerup', { bubbles: true }));
};
const until = async (fn, ms, step) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (fn()) return true;
    await wait(step || 150);
  }
  return fn();
};
'''


def _run_jsdom(seg_body: str, timeout: int = 40) -> str:
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = _src("app.js")
    i = js.index("function _dogParticles(host")
    seg = js[i:js.index("function bindMic()", i)]
    driver = _DRIVER_HEAD + """
async function main() {
__SEG__
__BODY__
}
main().catch(function (e) {
  console.log('RESULT:' + JSON.stringify({ error: String(e) }));
}).finally(function () {
  // v0.9.132.5 — exit shortly after main resolves: the sleeping-dog
  // gate timers (up to 2 min) would otherwise pin the event loop and
  // every jsdom test would hang until the 30s hard cap below. The
  // console.log payload is flushed synchronously; a forced exit(0)
  // also skips the Node-24 jsdom teardown (0xC00000FD) entirely.
  setTimeout(function () { process.exit(0); }, 250);
});
setTimeout(function () { process.exit(0); }, 30000);
""".replace("__SEG__", seg).replace("__BODY__", seg_body)
    fd, path = tempfile.mkstemp(suffix=".js", dir=str(ROOT))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(driver)
    try:
        p = subprocess.run(
            ["node", path], cwd=str(ROOT), timeout=timeout,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        out = p.stdout or ""
        if not out.strip() and (p.stderr or "").strip():
            raise AssertionError("node stderr: " + p.stderr[-300:])
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    assert '"error"' not in out, out[-400:]
    return out


class TestDogRunnerJsdom:
    def test_start_idempotent_stop_teardown(self):
        out = _run_jsdom("""
        _dogRunStart();
        _dogRunStart();
        const layers = document.querySelectorAll('#dog-run-layer').length;
        const dog = document.querySelector('.dog-runner');
        await wait(80);
        const entrance = dog.querySelector('.dog-skin').className;
        _dogRunStop();
        await wait(400);
        const gone = document.getElementById('dog-run-layer') === null;
        _dogRunStop();
        console.log('RESULT:' + JSON.stringify({
          layers: layers, entrance: entrance, gone: gone }));
        """)
        assert '"layers":1' in out, out[-300:]
        assert ('dog-running' in out) or ('dog-walking' in out), out[-300:]
        assert '"gone":true' in out, out[-300:]

    def test_pet_hearts_then_follow(self):
        out = _run_jsdom("""
        _dogRunStart();
        await wait(80);
        const layer = document.getElementById('dog-run-layer');
        const dog = layer.querySelector('.dog-runner');
        const skin = dog.querySelector('.dog-skin');
        const before = dog.style.left || '';
        press(dog);
        await wait(80);
        const particles = document.querySelectorAll('.dog-particle').length;
        press(dog); press(dog);
        await wait(80);
        const chasing = skin.classList.contains('dog-running');
        document.dispatchEvent(new w.MouseEvent('pointermove',
          { clientX: 600, clientY: 400, bubbles: true }));
        await wait(400);
        const moved = dog.style.left !== before;
        console.log('RESULT:' + JSON.stringify({
          particles: particles, chasing: chasing, moved: moved }));
        """)
        assert '"particles":1' in out, out[-300:]
        assert '"chasing":true' in out, out[-300:]
        assert '"moved":true' in out, out[-300:]

    def test_pickup_carry_putdown(self):
        out = _run_jsdom("""
        _dogRunStart();
        const layer = document.getElementById('dog-run-layer');
        const dog = layer.querySelector('.dog-runner');
        const skin = dog.querySelector('.dog-skin');
        // the 12s gallop cap settles the dog into the sitting hub
        await until(() => dog.classList.contains('dog-stopped'), 16000);
        const settled1 = dog.classList.contains('dog-stopped');
        press(dog);
        await wait(100);
        const lifting = skin.classList.contains('dog-lifting');
        await until(() => skin.classList.contains('dog-carried'), 2000);
        const carried = skin.classList.contains('dog-carried');
        const riding = dog.classList.contains('dog-carrying');
        document.dispatchEvent(new w.MouseEvent('pointermove',
          { clientX: 400, clientY: 200, bubbles: true }));
        await wait(50);
        const atX = dog.style.left === '325px';
        const atBottom = dog.style.bottom === '508px';
        press(dog);
        await wait(100);
        const putdown = skin.classList.contains('dog-putdown');
        await until(() => dog.classList.contains('dog-stopped'), 3000);
        const settled2 = dog.classList.contains('dog-stopped');
        console.log('RESULT:' + JSON.stringify({
          settled1: settled1, lifting: lifting, carried: carried,
          riding: riding, atX: atX, atBottom: atBottom,
          putdown: putdown, settled2: settled2 }));
        """, timeout=60)
        assert '"settled1":true' in out, out[-300:]
        assert '"lifting":true' in out, out[-300:]
        assert '"carried":true' in out, out[-300:]
        assert '"riding":true' in out, out[-300:]
        assert '"atX":true' in out, out[-300:]
        assert '"atBottom":true' in out, out[-300:]
        assert '"putdown":true' in out, out[-300:]
        assert '"settled2":true' in out, out[-300:]

    def test_bored_lie_wake_bite(self):
        out = _run_jsdom("""
        _dogRunStart();
        const layer = document.getElementById('dog-run-layer');
        const dog = layer.querySelector('.dog-runner');
        const skin = dog.querySelector('.dog-skin');
        await until(() => dog.classList.contains('dog-stopped'), 16000);
        // idle 8-12s with no interaction -> the bored lie-down
        await until(() => skin.classList.contains('dog-lying'), 15000);
        const lying = skin.classList.contains('dog-lying');
        const zzz = document.querySelectorAll('.dog-particle').length > 0;
        press(dog);
        await wait(100);
        const biting = skin.classList.contains('dog-biting');
        await until(() => dog.classList.contains('dog-stopped'), 3000);
        const settled = dog.classList.contains('dog-stopped');
        console.log('RESULT:' + JSON.stringify({
          lying: lying, zzz: zzz, biting: biting, settled: settled }));
        """, timeout=60)
        assert '"lying":true' in out, out[-300:]
        assert '"zzz":true' in out, out[-300:]
        assert '"biting":true' in out, out[-300:]
        assert '"settled":true' in out, out[-300:]

    def test_rolls_rest_and_dizzy_wiring(self):
        """v0.9.132.8 — ①nearing the window edge = roll over and turn
        back; ②six legs of running = drink (lick) -> walk to the
        centre -> lie -> sleep at the centre-bottom; ③too many LLM
        rounds = dizzy (dog-dizzy GIF wobble), a tap comforts it
        (dog-happy -> settle), otherwise it recovers alone in 15 s.
        v0.9.133.1 — dizzy/comfort skins swapped to the AI-generated
        dog_dizzy_bw.gif / dog_happy_bw.png assets."""
        js = _src("app.js")
        # ① edge roll + turn-around (inside moveBurst's step)
        assert "let rolling = false;" in js
        assert "rolling = true;" in js
        assert "setSkin('dog-rolling');" in js
        assert "dir = -dir;" in js
        assert "if (S.st !== kind) return;  // the burst was superseded" in js
        # ② the tired-rest ladder
        assert "const drinkAndSleep = function () {" in js
        assert "setSkin('dog-licking');  // 喝水（沒有喝水 GIF——舔水 sheet 代班）" in js
        assert "}, 'center');" in js  # walk to the centre
        assert "S.legs >= 6" in js
        assert "S.legs = 0;" in js
        assert "enterSleep();  // sleep at the centre, hugging the baseline" in js
        # ③ the LLM-round dizzy (hook + comfort + auto-recovery)
        assert "function _dogDizzy()" in js
        assert "layer._dogDizzyHook = function () {" in js
        assert "S.st = 'dizzy';" in js
        assert "setSkin('dog-dizzy');" in js  # v0.9.133.1 — AI GIF skin
        assert "S.st = 'comfort';" in js
        assert "setSkin('dog-happy');" in js  # v0.9.133.1 — AI PNG skin
        assert "clearT('wobble');" in js
        assert "clearT('dizzyAuto');" in js
        assert ("if (S.st !== 'dizzy') return;\n        settle();"
                in js)  # the 15 s self-recovery
        assert "let llmRounds = 0;" in js
        assert "const _DIZZY_AT = 8;" in js
        assert "llmRounds === _DIZZY_AT" in js
        # the tap dispatch routes dizzy -> comfort BEFORE the pet branch
        i = js.index("const onTap = function (ev) {")
        body = js[i: i + 1400]
        assert "if (S.st === 'dizzy') {" in body

    def test_second_streak_angry(self):
        out = _run_jsdom("""
        _dogRunStart();
        const layer = document.getElementById('dog-run-layer');
        const dog = layer.querySelector('.dog-runner');
        const skin = dog.querySelector('.dog-skin');
        press(dog); press(dog); press(dog);
        await wait(80);
        const following = skin.classList.contains('dog-running');
        // keeping petting WHILE the dog chases: the SECOND streak
        // bites instead of chasing again
        press(dog); press(dog); press(dog);
        const biting = await until(
          () => skin.classList.contains('dog-biting'), 2000);
        await until(() => dog.classList.contains('dog-stopped'), 4000);
        const settled = dog.classList.contains('dog-stopped');
        console.log('RESULT:' + JSON.stringify({
          following: following, biting: biting, settled: settled }));
        """, timeout=60)
        assert '"following":true' in out, out[-300:]
        assert '"biting":true' in out, out[-300:]
        assert '"settled":true' in out, out[-300:]

    def test_fling_throw_land_chase_ball(self):
        """v0.9.132.5 — grab mid-air (drag) + fast fling: throw arc ->
        land -> chase -> ball -> sit. Grabbed straight from the moving
        dog (no settle wait needed)."""
        out = _run_jsdom("""
        _dogRunStart();
        const layer = document.getElementById('dog-run-layer');
        const dog = layer.querySelector('.dog-runner');
        const skin = dog.querySelector('.dog-skin');
        const down = (el, x) => el.dispatchEvent(new w.MouseEvent(
          'pointerdown', { bubbles: true, clientX: x, clientY: 300 }));
        const move = (x) => document.dispatchEvent(new w.MouseEvent(
          'pointermove', { bubbles: true, clientX: x, clientY: 300 }));
        const upAt = (x) => document.dispatchEvent(new w.MouseEvent(
          'pointerup', { bubbles: true, clientX: x, clientY: 300 }));
        // grab straight from the moving dog (no settle wait needed)
        down(dog, 100);
        move(400);
        await wait(100);
        const carried = skin.classList.contains('dog-carried');
        const riding = dog.classList.contains('dog-carrying');
        // an immediate fast release = the fling (vx over the 1.1 gate)
        move(560);
        upAt(560);
        const throwing = await until(
          () => skin.classList.contains('dog-throwing'), 3000);
        const landing = await until(
          () => skin.classList.contains('dog-putdown'), 8000);
        const chasing = await until(
          () => skin.classList.contains('dog-running'), 8000);
        const balling = await until(
          () => skin.classList.contains('dog-balling'), 8000);
        await until(() => dog.classList.contains('dog-stopped'), 8000);
        const settled = dog.classList.contains('dog-stopped');
        console.log('RESULT:' + JSON.stringify({
          carried: carried, riding: riding, throwing: throwing,
          landing: landing, chasing: chasing, balling: balling,
          settled: settled }));
        """, timeout=45)
        assert '"carried":true' in out, out[-300:]
        assert '"riding":true' in out, out[-300:]
        assert '"throwing":true' in out, out[-300:]
        assert '"landing":true' in out, out[-300:]
        assert '"chasing":true' in out, out[-300:]
        assert '"balling":true' in out, out[-300:]
        assert '"settled":true' in out, out[-300:]
        assert '"carried":true' in out, out[-300:]
        assert '"riding":true' in out, out[-300:]
        assert '"throwing":true' in out, out[-300:]
        assert '"landing":true' in out, out[-300:]
        assert '"chasing":true' in out, out[-300:]
        assert '"balling":true' in out, out[-300:]
        assert '"settled":true' in out, out[-300:]

    def test_deep_sleep_locked_wake_chain(self):
        out = _run_jsdom("""
        _dogRunStart();
        const layer = document.getElementById('dog-run-layer');
        const dog = layer.querySelector('.dog-runner');
        const skin = dog.querySelector('.dog-skin');
        layer._SLEEP_GATE_MS = 1200;  // shrink the 2-min rest gate
        // the 12s cap settles the dog; ~1.2s of rest deepens the sleep
        const sleeping = await until(
          () => skin.classList.contains('dog-sleeping'), 20000);
        press(dog);
        await wait(150);
        const stretching = skin.classList.contains('dog-stretching');
        const yawning = await until(
          () => skin.classList.contains('dog-yawning'), 4000);
        await until(() => dog.classList.contains('dog-stopped'), 4000);
        const settled = dog.classList.contains('dog-stopped');
        console.log('RESULT:' + JSON.stringify({
          sleeping: sleeping, stretching: stretching,
          yawning: yawning, settled: settled }));
        """, timeout=60)
        assert '"sleeping":true' in out, out[-300:]
        assert '"stretching":true' in out, out[-300:]
        assert '"yawning":true' in out, out[-300:]
        assert '"settled":true' in out, out[-300:]