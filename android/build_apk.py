# -*- coding: utf-8 -*-
"""v0.9.126.2 — assemble the Divine Sense Agent Android launcher APK
on this Windows box WITHOUT gradle:

    aapt2 compile → aapt2 link (base apk + R.java) → javac → d8 →
    append classes.dex → zipalign → keytool (debug keystore once) →
    apksigner sign + verify → android/dist/*.apk

SDK requirements live in ``.android-sdk/`` (commandline-tools +
``build-tools;34.0.0`` + ``platforms;android-34`` — downloaded once
via ``sdkmanager``; see android/APK_BUILD_STEPS.md). Re-runs are safe:
every step is idempotent over the no-gradle build dir.

The wrapper app is ZERO-DEPENDENCY (platform android.* only), so the
same source also builds unchanged under Android Studio/gradle.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / ".android-sdk"
# v0.9.126.2 — build-tools 35 (d8/R8 stable): the 34.0.0 d8
# (R8 8.2.2-dev) NPEs on our inputs ("Cannot invoke String.length()
# ... <parameter1> is null"); 35.0.0 ships the fixed stable R8.
BT = SDK / "build-tools" / "35.0.0"
AJAR = SDK / "platforms" / "android-34" / "android.jar"
APP = ROOT / "android" / "app"
SRC = APP / "src" / "main"
BUILD = APP / "build" / "no-gradle"
DIST = ROOT / "android" / "dist"
KEYSTORE = ROOT / "android" / ".debug.keystore"
OUT = DIST / "divine-sense-agent-launcher.apk"


def _run(cmd: list, cwd: Path | None = None) -> subprocess.CompletedProcess:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    p = subprocess.run(
        [str(c) for c in cmd], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if p.returncode != 0:
        print("  stdout:", (p.stdout or "")[-1500:])
        print("  stderr:", (p.stderr or "")[-1500:])
        raise SystemExit(f"[error] step failed ({p.returncode}): {cmd[0]}")
    return p


def _fresh(d: Path) -> Path:
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    if not BT.joinpath("aapt2.exe").is_file() or not AJAR.is_file():
        raise SystemExit(
            "[error] Android build-tools/platform missing under "
            f"{SDK} — run sdkmanager first (see android/APK_BUILD_STEPS.md)")
    BUILD.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)

    # ① compile resources
    _run([BT / "aapt2.exe", "compile", "--dir", SRC / "res",
          "-o", BUILD / "res.zip"])
    # ② link → base.apk + generated R.java
    # The source manifest follows AGP 8 (namespace in build.gradle, no
    # `package` attr); aapt2 REQUIRES it — inject a temp copy.
    gen = _fresh(BUILD / "gen")
    import re
    mf = (SRC / "AndroidManifest.xml").read_text(encoding="utf-8")
    mf_tmp = BUILD / "AndroidManifest.apt2.xml"
    mf_tmp.write_text(
        re.sub(r"<manifest\b",
               '<manifest package="com.divinesense.agent"', mf, count=1),
        encoding="utf-8")
    _run([BT / "aapt2.exe", "link",
          "-o", BUILD / "base.apk",
          "-I", AJAR,
          "--manifest", mf_tmp,
          "--java", gen,
          "--min-sdk-version", 24,
          "--target-sdk-version", 34,
          "--version-code", 1,
          "--version-name", "0.9.126.2",
          BUILD / "res.zip"])
    # ③ javac
    cls = _fresh(BUILD / "classes")
    sources = sorted(gen.rglob("*.java")) + \
        sorted((SRC / "java").rglob("*.java"))
    _run([shutil.which("javac") or "javac",
          "-classpath", AJAR,
          "-d", cls,
          "-encoding", "UTF-8",
          "--release", "17"] + sources)
    # ④ d8 → classes.dex (jar input — the per-class CLI form hits an
    # r8 NPE: "Cannot invoke String.length() ... <parameter1> is null")
    dexdir = _fresh(BUILD / "dex")
    _run([shutil.which("jar") or "jar",
          "cf", BUILD / "classes.jar", "-C", cls, "."])
    _run([BT / "d8.bat", "--release", "--lib", AJAR, "--min-api", 24,
          "--output", dexdir, BUILD / "classes.jar"])
    # ⑤ append classes.dex into base.apk
    with zipfile.ZipFile(BUILD / "base.apk", "a") as zf:
        zf.write(dexdir / "classes.dex", "classes.dex")
    # ⑥ zipalign (4-byte, before signing)
    _run([BT / "zipalign.exe", "-f", "4",
          BUILD / "base.apk", BUILD / "aligned.apk"])
    # ⑦ debug keystore (generated once)
    if not KEYSTORE.is_file():
        _run([shutil.which("keytool") or "keytool", "-genkeypair",
              "-v", "-keystore", KEYSTORE,
              "-alias", "androiddebugkey",
              "-keyalg", "RSA", "-keysize", "2048",
              "-validity", "10950",
              "-storepass", "android", "-keypass", "android",
              "-dname", "CN=Divine Sense Agent Debug,O=Divine Sense,C=US"])
    # ⑧ sign + verify
    _run([BT / "apksigner.bat", "sign",
          "--ks", KEYSTORE,
          "--ks-pass", "pass:android", "--key-pass", "pass:android",
          "--out", OUT, BUILD / "aligned.apk"])
    _run([BT / "apksigner.bat", "verify", OUT])
    # ⑨ introspection (a manifest sanity dump)
    info = _run([BT / "aapt2.exe", "dump", "badging", OUT])
    interesting = [ln for ln in (info.stdout or "").splitlines()
                   if ln.startswith(("package", "application-label:",
                                     "launchable-activity", "uses-permission"))]
    for ln in interesting:
        print("  ", ln)
    print(f"APK_OK {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())