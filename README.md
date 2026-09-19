# DateFix

DateFix 0.1.0 adjusts file dates and existing photo/video capture dates. Its Python core and command-line interface are independent of the optional desktop interface, so the same operations can run on Windows and Linux. Everything runs locally.

## Start on Windows

Double-click **Start DateFix.cmd** in this project folder. The portable desktop application is also available at `dist\DateFix\DateFix.exe`; its command-line companion is `dist\DateFix\datefix-cli.exe`.

Keep the complete `dist\DateFix` folder together. The portable build includes Python, Qt, and the Windows ExifTool engine, so no separate installation is needed for that build.

1. Add files or folders, or drop them onto the window.
2. Choose the fields you want to change. **File modified** is selected initially.
3. Enter an offset or select an exact date and time. The preset fills in **+2 years, +16 days, +6 hours**.
4. Select **Preview changes**, review the proposed dates and any warnings, then **Apply changes**.
5. Use **Undo latest** or **Choose undo journal** to restore a previous run.

Changing a setting invalidates the preview. Selecting a preset does not change any files.

## Which dates can change?

| Field | Windows | Linux | Notes |
| --- | --- | --- | --- |
| File modified | Yes | Yes | Works with any supported regular file, including AVI videos. |
| File created | Yes | No | Windows filesystem creation time; Linux `ctime` is not creation time. |
| Embedded capture dates | With ExifTool | With ExifTool | Only existing, recognized date tags in the formats below. |

Embedded editing supports JPEG, TIFF, PNG, WebP, HEIC/HEIF, MP4, MOV, M4V, 3GP and 3G2. A supported extension does not guarantee that a particular file contains a writable capture date. DateFix does not invent missing tags.

**AVI filesystem dates work; AVI embedded capture-date writing does not.** ExifTool's [RIFF/AVI implementation](https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/RIFF.pm) does not provide AVI metadata writing.

If a selected field cannot be changed for a file, DateFix leaves that whole file unchanged. Other eligible files in the batch can still be processed. For example, when a folder contains AVI and JPEG files, select filesystem dates alone to process both without capture-format restrictions.

## Install from source

Use Python 3.10 or newer. Run the following commands from this `Project` directory.

### Windows

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop]"
.\.venv\Scripts\python.exe -m datefix gui
```

The supplied Windows engine is packaged by PhotoStructure's `exiftool-vendored.exe` and lives at `tools\exiftool\bin\exiftool.exe`. For a source checkout without that bundle, install [ExifTool](https://exiftool.org/) and put its executable on `PATH`, or set `DATEFIX_EXIFTOOL` to its full executable path.

### Linux desktop

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[desktop]'
sudo apt install libimage-exiftool-perl
python -m datefix gui
```

The `apt` command is for Debian/Ubuntu; use your distribution's ExifTool package elsewhere. File dates work without ExifTool.

The desktop interface requires a graphical session and Qt's platform libraries. If startup reports a missing `xcb` library, install the corresponding runtime packages for your distribution; see [Qt's Linux platform dependencies](https://doc.qt.io/qt-6/linux-requirements.html). A desktop session is not required for the command-line interface.

### Command-line only

```bash
python -m pip install -e .
datefix --help
```

This installs no third-party Python dependencies. ExifTool is a separate optional executable for capture metadata. `python -m datefix` can be used anywhere these examples use `datefix`.

## Command-line examples

The default is **preview only**. Review the output before adding `--apply`.

```bash
# Preview the requested calendar adjustment.
datefix shift "/path/to/media" --years 2 --days 16 --hours 6

# Apply that adjustment to modification dates, including nested folders.
datefix shift "/path/to/media" --years 2 --days 16 --hours 6 --recursive --apply

# Subtract six hours.
datefix shift "clip.avi" --hours -6 --apply

# Preview file-modified and capture dates together.
datefix shift "photo.jpg" "clip.mp4" --hours 6 --fields modified,captured

# Set an exact UTC instant.
datefix set "clip.mp4" --at "2026-04-21T11:27:50Z" --timezone UTC --apply

# Windows only: shift both filesystem dates.
datefix shift "clip.avi" --days 16 --fields modified,created --apply

# Read machine-readable capabilities and previews.
datefix doctor
datefix shift "clip.avi" --hours 6 --json

# Find journals, then restore a selected run.
datefix history
datefix undo "/path/to/history/run-folder/journal.json"
```

Use `--journal-dir "/path/to/history"` on `shift`, `set`, or `history` to choose a history folder. Use `--exiftool "/path/to/exiftool"` on `shift` or `set` to choose the metadata engine. Quote paths containing spaces. The Windows portable equivalent is, for example, `dist\DateFix\datefix-cli.exe doctor`.

`--json` is available for preview/apply, undo and history; `doctor` always returns JSON. Exit code `0` means success, `1` means an operation/file error, and argument syntax errors return `2`. A batch can contain both successful files and failures, so inspect its per-file results.

## Calendar and timezone behavior

- Years are calendar years, followed by days, hours, minutes and seconds. February 29 becomes February 28 when the target year is not a leap year. A year is not treated as 365 days.
- Filesystem dates and QuickTime integer dates use the selected `local` or `UTC` calendar. Local means the timezone configured on the computer. Nonexistent local times during daylight-saving transitions are rejected.
- QuickTime integer dates are treated as UTC internally. Some cameras write local time into those fields; the preview warns about this assumption.
- Capture dates with explicit offsets retain their stored offsets. EXIF companion offset tags are taken into account. A fixed instant is converted into the source offset.
- Capture dates without any stored timezone use the entered wall-clock time literally; fixed-date previews warn about this ambiguity.

The same offset is applied to every original date. Calendar adjustments around leap days or daylight-saving changes can alter the elapsed spacing between dates.

## Undo and file handling

DateFix saves a journal before applying changes. Filesystem-only edits store the original timestamps. Embedded edits additionally save a complete copy of each affected file, so allow enough free space for those backups.

Default history locations are:

- Windows: `%LOCALAPPDATA%\DateFix\history`
- Linux: `$XDG_DATA_HOME/DateFix/history`, or `~/.local/share/DateFix/history` when that variable is unset

Keep each run folder and its backups together. Journals use the original absolute file paths. Moving files or editing them after a run can prevent undo; DateFix refuses stale restores to protect newer changes. **Undo latest** selects the latest journal; use **Choose undo journal** for another run.

Symbolic links, junctions, other reparse points and hard-linked files are refused. Timestamp precision and permitted date ranges depend on the filesystem. An interrupted write may require manual recovery from the saved backup; keep its entire history folder if an error asks you to do so. No cloud service or upload is used.

## Development and verification

```bash
python -m pip install -r requirements-dev.txt
python -m pip install -e .
python -m pytest
```

Tests exercise calendar arithmetic, preview/application checks, metadata behavior, rollback/undo, the real command-line entry point, and the desktop controller with temporary files. Desktop tests use Qt's offscreen platform. ExifTool integration checks use generated media and copies, rather than editing the sample originals.

The optional `imageio-ffmpeg` package supplies the encoder used to generate disposable video fixtures; it is included in `requirements-dev.txt`.

To rebuild the Windows portable application:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\build_windows.ps1
```

The build writes `dist\DateFix`. Editable source installs keep the core next to the bundled project engine so its discovery also works before packaging.

Windows has been exercised during development. Linux is an intended source-install target, but a native Linux run has not been verified in this development session.

See [the architecture notes](docs/ARCHITECTURE.md) for the public core API and component boundaries. Bundled dependencies retain their own licenses; keep the included license files with the portable distribution.
