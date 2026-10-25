# PyInstaller spec for divine-sense-agent GUI.
#
# Build:
#   pip install pyinstaller
#   pyinstaller gui.spec
#
# Output: dist/divine-sense-agent-gui/  (folder with .exe + assets)
#
# Why spec instead of CLI?
#   * Bundles assets/ correctly (pywebview opens HTML from disk)
#   * Single-file mode breaks pywebview's native loader; folder mode works
#   * We can declare hiddenimports for the lazy-loaded modules in api.py
#   * Icon + version metadata in one place for future branding

# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

# Project root (where pyproject.toml lives).
PROJECT_ROOT = Path(SPECPATH).resolve()  # SPECPATH is set by pyinstaller

datas = [
    # tuple: (source_path_relative_to_spec, destination_in_bundle)
    (str(PROJECT_ROOT / 'gui' / 'assets'), 'gui/assets'),
]

# Hidden imports — anything we use lazy / dynamic that pyinstaller
# can't detect via static AST analysis.
hiddenimports = [
    'gui.api',
    'gui.logger',
    'gui.app',
    'src.config',
    'src.agent.agent',
    'src.agent.session',
    'src.vector_store.qdrant_client',
    'src.vector_store.retriever',
    'src.ingest.pipeline',
    'src.ingest.loader',
    'src.ingest.chunker',
    'src.ingest.embedder',
    'src.ingest.image_describer',
    'src.tools.web_search',
    # pywebview's optional backends - bundle the ones we want.
    'webview',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    # qdrant-client uses grpc + httpx.
    'qdrant_client',
    'httpx',
    'openai',
]

# Excludes — strip heavy unused modules so the .exe stays small.
excludes = [
    'tkinter',
    'matplotlib',
    'numpy.tests',
    'pandas',
    'scipy',
    'PIL.ImageQt',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'streamlit',
]

a = Analysis(
    ['gui/app.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='divine-sense-agent-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX often breaks WebView2 loader
    console=False,        # GUI app - no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # v0.9.103.3 — the Divine Sense dog icon (16..256 from icon.svg).
    icon=str(PROJECT_ROOT / 'gui' / 'assets' / 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='divine-sense-agent-gui',
)
