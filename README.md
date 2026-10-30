# Paperport

A Windows desktop app that lets you download PDFs from Google Drive's viewer, including files shared with view-only access. Paste a Drive file link, load the document, and export its displayed pages as a local PDF. Built with Python, CustomTkinter, Playwright, and PyMuPDF.

## Features

- Download PDFs displayed in Google Drive's viewer by rebuilding them from the rendered page images.
- Modern dark interface with progress and cancellation.
- DPI-aware header artwork and native Windows icons sized for the current monitor.
- Hidden browser capture, with an optional browser window for sign-in.
- Automatic page loading, always enabled without an extra checkbox.
- Page-number navigation with adaptive waits, batched image capture, and a scrolling fallback.
- Document-wide page deduplication, Arabic numeral support, and completeness checks.
- Mixed page sizes and lossless image compression.
- A single executable: no Python installation or command window required.

## Run on Windows

Double-click `dist/Paperport-1.1.2.exe`, included in this repository. Windows 10/11 x64 and an installed Microsoft Edge or Google Chrome are required. The executable includes Python and its libraries; the browser itself is not bundled. Its SHA-256 checksum is included alongside it.

1. Paste a Google Drive file link and click **Load document**.
2. Wait for the document to load in the background.
3. Click **Export PDF**, choose a filename, and leave Paperport running.
4. Review the result. Choose **Patient** speed if pages load slowly.

If a file requires authentication, enable **Show browser for sign-in**, load the link, and sign in. Once the PDF appears, turn the switch off and load the same link again. Google may reject some automated browser sign-in sessions.

The current build is unsigned. One-file startup may take a few seconds while the bundled runtime extracts.

## Output and limitations

Paperport rebuilds a PDF from the viewer's raster images. It does not recover the original PDF bytes. Selectable text, links, forms, and annotations are not preserved; quality is limited by the images Drive displays.

Drive's viewer can change. When page numbers are available, Paperport counts only valid numbered document pages and ignores unnumbered viewer images. Duplicate page numbers are captured once across viewer frames. Missing-page errors identify the affected pages. Without numbering, verify the output manually. Use documents you are authorized to access and save.

## Version 1.1

Version 1.1.2 adds DPI-aware header rendering, clearer small-icon artwork, additional Windows icon sizes, and native icon updates when the window moves between monitors. Use the new executable path when updating an existing taskbar shortcut.

Fixed extra viewer images being counted as document pages (for example, "140 of 138"). Automatic mode navigates to missing pages directly and waits for loaded images instead of sleeping after every scroll. It also batches image reads and uses asynchronous lossless PNG encoding when the viewer blocks direct blob reads.

On the 138-page regression document, capture and saving took 39.5 seconds versus 203 seconds in the earlier run. This is a single-document measurement; network speed and viewer behavior affect other exports. Image quality was preserved.

## Privacy

The app uploads no documents to a Paperport service. Browser traffic goes to Google when loading a file. Exports are saved to the location you select.

Sessions and logs live in `%LOCALAPPDATA%\Paperport`, outside the repository and executable folder. Keep this directory private. To clear the session, close Paperport and delete its `browser-profile` subdirectory. Set `PAPERPORT_DATA_DIR` to override the data directory.

## Run from source

Use Python 3.11+ on Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Use `.venv\Scripts\pythonw.exe main.py` for a console-free source launch. Errors are recorded in `%LOCALAPPDATA%\Paperport\paperport.log`.

## Build the executable

Build on Windows x64:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
$python = '.venv\Scripts\python.exe'
$base = & $python -c "import sys; print(sys.base_prefix)"
if (Test-Path "$base\tcl\tcl8.6\init.tcl") {
    $env:TCL_LIBRARY = "$base\tcl\tcl8.6"
    $env:TK_LIBRARY = "$base\tcl\tk8.6"
}
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx `
    --name Paperport-1.1.2 --icon assets/paperport.ico `
    --collect-all customtkinter --collect-all playwright --collect-all pymupdf `
    --copy-metadata customtkinter --copy-metadata playwright --copy-metadata PyMuPDF `
    --copy-metadata darkdetect --copy-metadata greenlet --copy-metadata packaging `
    --copy-metadata pyee --copy-metadata typing_extensions `
    --copy-metadata pyinstaller --copy-metadata Pillow `
    --add-data "assets;assets" main.py
```

The result is `dist\Paperport-1.1.2.exe`. The command bundles UI assets, Python, the PDF engine, and the Playwright driver. PyInstaller's generated `.spec` and build files are ignored. Edge/Chrome remains a system requirement.

## Checks

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe main.py --self-test
dist\Paperport-1.1.2.exe --self-test
```

The self-test opens and closes a hidden app window, launches a headless browser, captures a synthetic two-page document without connecting to Google, and verifies its PDF. It logs `PAPERPORT_SELF_TEST_PASSED` on success and exits with a nonzero code on failure.

## Source files

| File | Purpose |
| --- | --- |
| `main.py` | Entry point and startup logging |
| `app.py` | Interface and background capture worker |
| `core.py` | Viewer scripts and PDF assembly |
| `paths.py` | Private runtime-data location |
| `window_icons.py` | Native Windows icon sizing and monitor-DPI updates |
| `self_test.py` | Offline executable diagnostic |
| `assets/` | App PNG and Windows ICO |
| `tests/` | Core behavior checks |
