# Plan: Shrinkbox Windows GUI Application

## Overview
Python + PyQt6 desktop app that batch-compresses image and video files in a folder to hit a user-specified total folder size target. Users preview quality before committing to full compression.

## Stack Decisions
- **GUI**: Python 3.11+ / PyQt6
- **Video compression**: ffmpeg (subprocess / ffmpeg-python) — two-pass bitrate targeting
- **Image compression**: Pillow — binary search on quality parameter
- **Video preview**: QMediaPlayer (PyQt6 built-in) plays a short temp clip
- **Image preview**: Side-by-side QLabel with quality slider
- **Background work**: QThread + signals (never block the UI thread)
- **ffmpeg**: Must be on PATH or bundled; ffprobe used for metadata

## Feature Scope
### In scope
- Folder picker → scan all image and video files recursively (optional toggle)
- Target total folder size input (MB)
- App distributes size budget proportionally by original file size
- Per-file preview of compressed output before committing
- Global quality slider that redistributes the budget
- Background compression with per-file progress bar
- Output to a new folder (never overwrites originals by default)
- Graceful error handling (unsupported format, ffmpeg missing, bitrate too low)
- Persist last-used folder and target size (QSettings)

### Out of scope
- Audio-only files
- RAW image formats (CR2, NEF, etc.)
- Uploading or cloud integration
- Lossless compression modes

---

## Project Structure
```
shrinkbox/
├── main.py
├── requirements.txt
├── ui/
│   ├── __init__.py
│   ├── main_window.py        # Folder picker, target size, file table, action buttons
│   ├── preview_dialog.py     # Before/after image or video clip preview
│   └── progress_dialog.py   # Per-file compression progress
├── core/
│   ├── __init__.py
│   ├── file_scanner.py       # Recursively find + classify image/video files
│   ├── budget.py             # Proportional MB budget distribution
│   ├── image_compressor.py   # Pillow binary-search quality targeting
│   ├── video_compressor.py   # ffmpeg two-pass bitrate targeting
│   ├── estimator.py          # Fast preview encode (images: temp file; videos: 5s clip)
│   └── worker.py             # QThread subclass; emits progress/done/error signals
└── utils/
    ├── __init__.py
    ├── ffmpeg_utils.py       # ffprobe metadata, ffmpeg subprocess wrappers
    └── size_utils.py         # Human-readable size formatting, MB↔bytes
```

---

## Phases

### Phase 1 — Project Setup
1. Create requirements.txt: PyQt6, Pillow, ffmpeg-python
2. Create folder structure and empty __init__.py files
3. Write main.py entry point (QApplication bootstrap)
4. Write utils/ffmpeg_utils.py: detect ffmpeg/ffprobe on PATH, raise clear error if missing

### Phase 2 — Core Engine (no GUI, testable via CLI)
5. file_scanner.py: walk folder, classify by extension into IMAGE_EXTS / VIDEO_EXTS sets; return list of FileInfo dataclasses (path, type, size_bytes)
6. budget.py: given list of FileInfo and total_target_mb, compute per-file target_bytes proportionally (target_bytes_i = original_size_i / total_original_size * total_target_bytes)
7. image_compressor.py: binary search on Pillow JPEG quality (1–95); PNG uses optimize=True + progressive; saves to output path; returns final size
8. video_compressor.py: use ffprobe to get duration; compute video_bitrate_kbps = (target_bytes * 8 / duration / 1000) - 128; run ffmpeg two-pass libx264 encode; returns final size
9. estimator.py: for images, encodes to a temp file at estimated quality; for videos, encodes first 5 seconds to a temp file at estimated bitrate; returns temp file path

### Phase 3 — Main Window UI
10. main_window.py: QMainWindow with folder picker (QFileDialog), target size QSpinBox (MB), recursive toggle QCheckBox, QTableWidget showing filename / type / original size / projected size / status
11. "Scan" button triggers file_scanner + budget calculation, populates table
12. Table rows are selectable; "Preview" button opens preview_dialog for selected row
13. "Compress All" button opens progress_dialog and starts worker

### Phase 4 — Preview Dialog
14. preview_dialog.py: detect file type; for images show two QLabels (Original | Compressed) with a quality QSlider that re-runs estimator.py on slider release; for videos show QMediaPlayer playing the 5s clip with a bitrate QSlider
15. Dialog shows original size vs estimated compressed size in real time as slider moves
16. "Accept Settings" updates the per-file budget override; "Cancel" discards

### Phase 5 — Compression Worker + Progress Dialog
17. worker.py: QThread; iterates FileInfo list; calls image_compressor or video_compressor; emits file_started(index), file_done(index, final_size), file_error(index, msg), all_done()
18. progress_dialog.py: QDialog with overall QProgressBar, per-file status label, per-file QProgressBar (indeterminate during encode), log QTextEdit; Cancel button sends worker.requestInterruption()
19. On all_done: show summary (total original size → total final size, savings %)

### Phase 6 — Output & Polish
20. Output folder: default to {source_folder}_shrinkbox; user can override via Browse button in main window
21. QSettings persistence: save/restore last folder path, last target MB, recursive toggle state
22. ffmpeg missing: on startup, check PATH; if absent show QMessageBox with download link and exit gracefully
23. Minimum bitrate guard in video_compressor: if computed bitrate < 100 kbps, surface a warning to the user before proceeding

---

## Key Technical Details

### Video two-pass bitrate formula
- `duration_s` from ffprobe JSON output
- `audio_kbps = 128`
- `video_kbps = int((target_bytes * 8) / duration_s / 1000) - audio_kbps`
- Pass 1: `-b:v {video_kbps}k -pass 1 -an -f null NUL`
- Pass 2: `-b:v {video_kbps}k -pass 2 -c:a aac -b:a 128k {output}`

### Image binary search
- `lo=1, hi=95`; tolerance = ±5% of target bytes
- Save to BytesIO (in-memory) to avoid disk thrash during search
- Final save to output path at converged quality

### Proportional budget
- Files below their natural "lossless" threshold don't need compression — skip them or keep them as-is (configurable)

### Supported formats
- Images: `.jpg .jpeg .png .webp .bmp .tiff`
- Videos: `.mp4 .mov .mkv .avi .wmv .m4v .webm`

---

## Relevant Files to Create
- `p:\Github Stuff\shrinkbox\main.py`
- `p:\Github Stuff\shrinkbox\requirements.txt`
- All files under `core/`, `ui/`, `utils/`

## Verification Steps
1. Run `python main.py` — window opens without errors
2. Scan a folder of mixed images/videos — table populates with correct types and sizes
3. Click Preview on a JPEG — before/after labels appear; moving slider updates compressed size estimate
4. Click Preview on an MP4 — 5s clip plays in the dialog
5. Click Compress All — progress dialog appears; files appear in output folder
6. Verify output folder total size is within ~5% of target
7. Verify originals are untouched
8. Test with ffmpeg not on PATH — friendly error dialog appears

## Decisions
- **Target = folder total, not per-file**: Budget distributed proportionally; user can override per-file via preview dialog
- **Never overwrite originals**: Always output to a new folder
- **ffmpeg on PATH preferred** over bundling (simpler; user already has it or can install)
- **libx264** codec default (widest compatibility); could expose codec choice in settings later
