# shrinkbox.spec — PyInstaller build script
#
# Prerequisites
# -------------
# 1. pip install pyinstaller>=6.0
# 2. Place bin\ffmpeg.exe, bin\ffprobe.exe, AND all av*.dll / sw*.dll files
#    from the BtbN release into the project bin\ folder.
#    Download from https://github.com/BtbN/FFmpeg-Builds/releases
#    (grab the *-win64-gpl.zip, extract, copy everything from its bin\ folder)
#
# Build
# -----
#   pyinstaller shrinkbox.spec
#
# Output
# ------
#   dist\Shrinkbox\Shrinkbox.exe   ← launch this (or zip the whole folder)

import glob
import os

# ── ffmpeg CLI DLLs (from bin\) ───────────────────────────────────────────────
# BtbN shared builds ship avcodec-*.dll etc. alongside the exes.
# Glob everything in bin\ so new DLL versions are picked up automatically.
_bin_files = [
    (f.replace('\\', '/'), '.')
    for f in glob.glob(os.path.join('bin', '*'))
    if os.path.isfile(f)
]

# ── PyQt6 multimedia DLLs ─────────────────────────────────────────────────────
# QMediaPlayer loads these at runtime via Qt's FFmpeg multimedia backend.
# PyInstaller doesn't collect them automatically.
try:
    import PyQt6 as _pyqt6
    _qt_bin = os.path.join(os.path.dirname(_pyqt6.__file__), 'Qt6', 'bin')
    _qt_mm_dlls = [
        (f.replace('\\', '/'), '.')
        for pattern in ('av*.dll', 'sw*.dll', 'postproc*.dll')
        for f in glob.glob(os.path.join(_qt_bin, pattern))
    ]
except Exception:
    _qt_mm_dlls = []

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_bin_files + _qt_mm_dlls,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',      # not used; shaves ~5 MB
        'matplotlib',
        'numpy',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Shrinkbox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX off — avoids antivirus false positives
    console=False,      # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Shrinkbox',
)
