# shrinkbox.spec — PyInstaller build script
#
# Prerequisites
# -------------
# 1. pip install pyinstaller>=6.0
# 2. Place bin\ffmpeg.exe and bin\ffprobe.exe in the project root.
#    Download a Windows static build from:
#      https://github.com/BtbN/FFmpeg-Builds/releases
#    (grab the *-win64-lgpl-shared or *-win64-gpl-shared essentials zip,
#     extract, and copy ffmpeg.exe + ffprobe.exe into bin\)
#
# Build
# -----
#   pyinstaller shrinkbox.spec
#
# Output
# ------
#   dist\Shrinkbox\Shrinkbox.exe   ← launch this (or zip the whole folder)

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        ('bin/ffmpeg.exe',  '.'),   # extracted to root of dist\Shrinkbox\
        ('bin/ffprobe.exe', '.'),
    ],
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
