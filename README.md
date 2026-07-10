# Shrinkbox

**Batch-compress a folder of images and videos to hit a target total size — with a live quality preview before you commit.**

Shrinkbox is a Windows desktop app built with Python + PyQt6. You pick a source folder and a target size (e.g. 500 MB), and Shrinkbox distributes that budget proportionally across every image and video in the folder, compresses them in the background, and saves the results to an output folder — preserving the original directory structure.

---

## Table of Contents

- [Features](#features)
- [Screenshots / Quick Tour](#screenshots--quick-tour)
- [Download & Run (End Users)](#download--run-end-users)
- [Building from Source](#building-from-source)
- [Packaging as a Standalone .exe](#packaging-as-a-standalone-exe)
- [Project Structure](#project-structure)
- [How It Works](#how-it-works)
- [Configuration & Limits](#configuration--limits)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Batch compression** — point at any folder (scanned recursively) and set one target size in MB.
- **Proportional budget distribution** — larger files receive a proportionally larger slice of the budget, so quality degrades evenly across the whole folder.
- **Live quality preview** — before compressing anything, scrub a slider to preview what an image or video will look like at the calculated quality.
  - Scroll-wheel **zoom toward cursor** and **drag to pan** on both image and video previews.
  - `[−]`, `[Fit]`, `[+]` zoom controls.
- **Image formats**: JPEG, WebP (lossy quality search), PNG (lossless optimize → palette quantize), BMP/TIFF (re-encoded as JPEG).
- **Video formats**: Any container ffmpeg can read, re-encoded with two-pass **libx264** to a target bitrate.
- **Pre-flight warnings** — if a video's target bitrate would fall below a usable minimum (~100 kbps), you get a warning dialog listing the affected files before compression starts.
- **Live progress** — per-file status in the file table, an overall progress bar, a scrollable log, and a cancel button (finishes the current file before stopping).
- **Non-destructive** — source files are never touched; output always goes to a separate folder.
- Files already at or below their budget are copied unchanged.

---

## Screenshots / Quick Tour
<img width="1033" height="705" alt="image" src="https://github.com/user-attachments/assets/10410566-ef73-4c29-8136-c4b44f6c3250" />

> _Compress entire folders of images and videos to hit a target size._

<img width="1033" height="705" alt="image" src="https://github.com/user-attachments/assets/d69198e1-3ecf-4f5c-8a89-526c25911a82" />

> _Compress with confidence with a live preview before you start compressing._

| Step | What you do |
|------|-------------|
| 1 | Choose **Source Folder** and **Output Folder** |
| 2 | Set a **Target Size** in MB |
| 3 | Click a file row to open the **Quality Preview** panel |
| 4 | Adjust the preview slider; click **Apply** if you want to lock in custom settings per-file |
| 5 | Click **Compress All** — a progress dialog shows live status |
| 6 | When done, click **Open Output Folder** |

---

## Download & Run (End Users)

1. Go to the [**Releases**](../../releases) page and download the latest `Shrinkbox.zip`.
2. Extract the zip anywhere — no installer required.
3. Run `Shrinkbox.exe` inside the extracted folder.

> **Requirements**: Windows 10/11 (64-bit). Everything else — Python, Qt, ffmpeg — is bundled inside the zip.

---

## Building from Source

### Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11 or 3.12 | [python.org](https://www.python.org/downloads/) |
| ffmpeg + ffprobe | Any recent static build | Must be on `PATH` — see below |

**Install ffmpeg** (development only):  
Download a Windows static build from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) — pick `ffmpeg-master-latest-win64-gpl.zip`. Extract it and add the inner `bin\` folder to your system `PATH`.

### Clone and install

```powershell
git clone https://github.com/YOUR_USERNAME/shrinkbox.git
cd shrinkbox
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Run

```powershell
python main.py
```

---

## Packaging as a Standalone .exe

The repo ships with a ready-made PyInstaller spec (`shrinkbox.spec`) that bundles Python, all dependencies, and ffmpeg into a single folder.

### 1 — Get ffmpeg binaries for bundling

Download `ffmpeg-master-latest-win64-gpl.zip` from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases).  
Copy **all files** from inside the zip's `bin\` folder into a `bin\` folder at the project root — this includes `ffmpeg.exe`, `ffprobe.exe`, and all `av*.dll` / `sw*.dll` shared libraries:

```
shrinkbox\
  bin\
    ffmpeg.exe
    ffprobe.exe
    avcodec-*.dll
    avformat-*.dll
    avutil-*.dll
    swscale-*.dll
    swresample-*.dll
    avfilter-*.dll
    avdevice-*.dll
    postproc-*.dll    ← copy everything; the spec globs bin\* automatically
  main.py
  shrinkbox.spec
  ...
```

> `bin\` is gitignored — these large binaries should not be committed.

### 2 — Install PyInstaller

```powershell
pip install "pyinstaller>=6.0"
```

### 3 — Build

```powershell
pyinstaller shrinkbox.spec
```

### 4 — Output

```
dist\
  Shrinkbox\
    Shrinkbox.exe   ← launch this
    ffmpeg.exe
    ffprobe.exe
    ... (Qt and Python runtime files)
```

Zip the entire `dist\Shrinkbox\` folder and attach it to a GitHub Release.

---

## Project Structure

```
shrinkbox/
├── main.py                  # Entry point; dependency check, then MainWindow
├── requirements.txt         # Runtime Python dependencies
├── shrinkbox.spec           # PyInstaller build script
│
├── core/
│   ├── file_scanner.py      # Recursive folder scan → list[FileInfo]
│   ├── budget.py            # Proportional size-budget distribution
│   ├── estimator.py         # Preview-quality size estimation (no disk I/O)
│   ├── image_compressor.py  # Pillow-based image compression
│   ├── video_compressor.py  # ffmpeg two-pass libx264 video compression
│   └── worker.py            # QThread background compression worker
│
├── ui/
│   ├── main_window.py       # Main application window + file table
│   ├── preview_panel.py     # Inline quality-preview panel (image + video)
│   ├── preview_dialog.py    # Standalone preview dialog
│   └── progress_dialog.py   # Compression progress dialog
│
└── utils/
    ├── ffmpeg_utils.py      # ffmpeg/ffprobe subprocess wrappers
    └── size_utils.py        # Byte ↔ MB helpers, human_readable()
```

---

## How It Works

### Budget distribution (`core/budget.py`)

Given a target total size T and N files with original sizes s₁…sₙ:

$$\text{budget}_i = T \times \frac{s_i}{\sum_{j=1}^{N} s_j}$$

Files already smaller than their budget are excluded from the pool and their unused budget is redistributed to the remaining files (iteratively).

### Image compression (`core/image_compressor.py`)

- **JPEG / WebP** — binary search on the Pillow `quality` parameter (1–95) until the encoded size lands at or below the budget.
- **PNG** — lossless `optimize=True` first; if still over budget, quantize to a 256-color palette.
- **BMP / TIFF** — converted to JPEG at the output path (these formats have no native lossy compression).

### Video compression (`core/video_compressor.py`)

Two-pass **libx264** encoding via ffmpeg:

$$\text{video\_kbps} = \frac{\text{target\_bytes} \times 8}{\text{duration\_seconds} \times 1000} - 128$$

128 kbps is reserved for the AAC audio track. If the resulting video bitrate is below 100 kbps, a `ValueError` is raised and the worker copies the original unchanged (the UI warns you beforehand).

### Cancellation

Clicking **Cancel** calls `QThread.requestInterruption()`. The worker checks between files — a running ffmpeg encode is never killed mid-process; it completes first. The UI shows "Cancelling… (waiting for current file to finish)".

---

## Configuration & Limits

| Setting | Default | Location |
|---------|---------|----------|
| Minimum video bitrate | 100 kbps | `core/video_compressor.py` → `MIN_VIDEO_BITRATE_KBPS` |
| Audio track bitrate | 128 kbps | `core/video_compressor.py` → `AUDIO_BITRATE_KBPS` |
| JPEG quality search range | 1–95 | `core/image_compressor.py` → `_QUALITY_MIN/_MAX` |

---

## Contributing

1. Fork the repo and create a feature branch.
2. Run the app from source (`python main.py`) and verify your change works end-to-end.
3. Keep new modules in the appropriate `core/`, `ui/`, or `utils/` package.
4. Open a pull request with a clear description of what changed and why.

There is no test suite yet — contributions that add one are very welcome.

---

## License

Shrinkbox is distributed under the **GNU General Public License v3.0**.  
See [LICENSE](LICENSE) for the full text.

**Why GPL v3?** Shrinkbox uses [PyQt6](https://www.riverbankcomputing.com/software/pyqt/), which is licensed under GPL v3. Any application that links against PyQt6 must also be GPL v3 (or hold a commercial Riverbank Computing license). The bundled ffmpeg binaries are built with GPL codecs (libx264); their source is available from the [FFmpeg project](https://ffmpeg.org/).

---

## Acknowledgements

**App icon** — "Box" by [Sergei Kokota](https://icon-icons.com/authors/219-sergei-kokota), from the [Office Vol.7 Icons](https://icon-icons.com/pack/office-vol7icons/945) pack on [icon-icons.com](https://icon-icons.com/icon/box/73953). Licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Converted to ICO format for use as the application icon.

