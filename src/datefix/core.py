"""GUI-independent timestamp planning, guarded application, and journalled undo.

Filesystem offsets use calendar/wall-clock arithmetic in the selected local or UTC
timezone. Years and months are combined, with the day clamped to the target
month's last day when necessary, followed by days and time. Nonexistent local times are rejected; an ambiguous local time
retains the source's fold when available. QuickTime integer dates use the same
timezone arithmetic. String capture tags retain their explicitly stored offsets.
"""
from __future__ import annotations

import calendar
import ctypes
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Callable, Sequence
import uuid

from . import metadata

UTC = timezone.utc
SCHEMA_VERSION = 1
Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class Offset:
    years: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    # Append to preserve the original positional arguments of the public API.
    months: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in asdict(self).values()):
            raise ValueError("Offset components must be whole numbers.")


@dataclass(frozen=True)
class Request:
    offset: Offset | None = None
    fixed: datetime | None = None
    targets: tuple[str, ...] = ("modified",)
    timezone: str = "local"

    def __post_init__(self) -> None:
        if (self.offset is None) == (self.fixed is None):
            raise ValueError("Choose exactly one offset or fixed date.")
        if self.offset is not None and not isinstance(self.offset, Offset):
            raise ValueError("offset must be an Offset.")
        if self.fixed is not None and not isinstance(self.fixed, datetime):
            raise ValueError("fixed must be a datetime.")
        targets = tuple(self.targets)
        if not targets or any(target not in {"modified", "created", "captured"} for target in targets):
            raise ValueError("Select at least one of modified, created, or captured.")
        if len(set(targets)) != len(targets):
            raise ValueError("Date targets must not be repeated.")
        if self.timezone.lower() not in {"local", "utc"}:
            raise ValueError("Timezone must be local or UTC.")
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "timezone", "UTC" if self.timezone.lower() == "utc" else "local")


@dataclass
class Change:
    field: str
    before: str
    after: str


@dataclass
class FilePlan:
    path: str
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    operations: dict = field(default_factory=dict)


@dataclass
class Plan:
    files: list[FilePlan]
    request: Request
    exiftool: str | None = None
    version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class FileResult:
    path: str
    status: str
    message: str = ""


@dataclass
class Result:
    files: list[FileResult]
    journal_path: str | None = None


def _absolute(path: str | Path) -> Path:
    # resolve() would hide a symlink that we need to reject.
    return Path(os.path.abspath(Path(path).expanduser()))


def _is_link(path: Path) -> bool:
    details = path.lstat()
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_file(path: Path) -> os.stat_result:
    for part in (path, *path.parents):
        if _is_link(part):
            raise ValueError("Symbolic links, junctions, and other reparse points are not edited.")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("This path is not a regular file.")
    if details.st_nlink > 1:
        raise ValueError("Hard-linked files are not edited because another path shares their dates.")
    return details


def discover(paths: Sequence[str | Path], recursive: bool = False) -> list[Path]:
    """Expand folders deterministically, without following symlinks or junctions.

    Explicit missing/unsafe paths are retained so preview can report their error.
    Folder expansion includes all regular files: filesystem dates are format agnostic.
    """
    found: dict[str, Path] = {}

    def add(path: Path) -> None:
        found.setdefault(os.path.normcase(str(path)), path)

    def folder(path: Path) -> None:
        try:
            entries = sorted(path.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            add(path)
            return
        for child in entries:
            try:
                if _is_link(child):
                    continue
                if child.is_file():
                    add(child)
                elif recursive and child.is_dir():
                    folder(child)
            except OSError:
                add(child)

    for raw in paths:
        path = _absolute(raw)
        try:
            if not _is_link(path) and path.is_dir():
                # Do not enter folders beneath a linked ancestor, either.
                if any(_is_link(parent) for parent in path.parents):
                    add(path)
                else:
                    folder(path)
            else:
                add(path)
        except OSError:
            add(path)
    return list(found.values())


def _created_ns(details: os.stat_result) -> int | None:
    if hasattr(details, "st_birthtime_ns"):
        return details.st_birthtime_ns
    return details.st_ctime_ns if os.name == "nt" else None


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(path: Path, hash_content: bool = False) -> dict:
    details = _safe_file(path)
    result = {
        "size": details.st_size, "mtime_ns": details.st_mtime_ns,
        "atime_ns": details.st_atime_ns, "ctime_ns": details.st_ctime_ns,
        "created_ns": _created_ns(details), "device": details.st_dev, "inode": details.st_ino,
    }
    if hash_content:
        result["sha256"] = _hash(path)
        after = _safe_file(path)
        if (details.st_size, details.st_mtime_ns, details.st_ctime_ns, details.st_ino) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino
        ):
            raise ValueError("File changed while it was being read. Preview it again.")
    return result


def _matches(path: Path, expected: dict) -> bool:
    actual = _snapshot(path, "sha256" in expected)
    return all(actual.get(key) == expected.get(key) for key in (
        "size", "mtime_ns", "ctime_ns", "created_ns", "device", "inode", "sha256"
    ))


def _snapshot_after_write(path: Path, atime_ns: int, hash_content: bool) -> dict:
    result = _snapshot(path, hash_content)
    # Hashing itself reads the file and can change its access date. Restore that
    # unselected date *after* verification, then record the new POSIX ctime.
    latest = path.stat()
    if latest.st_atime_ns != atime_ns:
        os.utime(path, ns=(atime_ns, latest.st_mtime_ns))
        refreshed = _snapshot(path)
        if "sha256" in result:
            refreshed["sha256"] = result["sha256"]
        result = refreshed
    return result


def _shift(value: datetime, offset: Offset) -> datetime:
    if not any(asdict(offset).values()):
        return value
    month_index = (value.year - 1) * 12 + value.month - 1 + offset.years * 12 + offset.months
    year_index, month_index = divmod(month_index, 12)
    year, month = year_index + 1, month_index + 1
    if not 1 <= year <= 9999:
        raise ValueError("The resulting year must be between 1 and 9999.")
    day = min(value.day, calendar.monthrange(year, month)[1])
    shifted = value.replace(year=year, month=month, day=day) + timedelta(
        days=offset.days, hours=offset.hours, minutes=offset.minutes, seconds=offset.seconds
    )
    return shifted.replace(fold=value.fold)


def _from_ns(value: int, zone: str) -> datetime:
    seconds, fraction = divmod(value, 1_000_000_000)
    dt = datetime.fromtimestamp(seconds, UTC) if zone == "UTC" else datetime.fromtimestamp(seconds)
    return dt.replace(microsecond=fraction // 1000)


def _to_ns(value: datetime, zone: str) -> int:
    if value.tzinfo is not None:
        delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000
    if zone == "UTC":
        return _to_ns(value.replace(tzinfo=UTC), "UTC")
    seconds = int(value.replace(microsecond=0).timestamp())
    round_trip = datetime.fromtimestamp(seconds).replace(microsecond=value.microsecond)
    if round_trip != value:
        raise ValueError("The resulting local time does not exist during a daylight-saving transition. Use UTC or another time.")
    return seconds * 1_000_000_000 + value.microsecond * 1000


def _new_ns(before: int, request: Request) -> int:
    if request.fixed is not None:
        return _to_ns(request.fixed, request.timezone)
    if request.offset is None:
        raise ValueError("Missing offset.")
    return _to_ns(_shift(_from_ns(before, request.timezone), request.offset), request.timezone) + before % 1000


def _display_ns(value: int, zone: str) -> str:
    dt = _from_ns(value, zone)
    if zone == "local":
        dt = dt.astimezone()
    return dt.isoformat(sep=" ", timespec="seconds")


def _new_capture(before: str, request: Request, tag: str = "") -> str:
    original = metadata.parse_date(before)
    if metadata.is_quicktime_integer(tag):
        if original.tzinfo is None:
            original = original.replace(tzinfo=UTC)
        after_ns = _new_ns(_to_ns(original, "UTC"), request)
        return metadata.format_date(_from_ns(after_ns, "UTC"), before)
    if request.offset is not None:
        after = _shift(original, request.offset)
    elif request.fixed is not None:
        after = request.fixed
        if original.tzinfo is None:
            # EXIF has separate timezone tags which we leave intact.
            after = after.replace(tzinfo=None)
        elif after.tzinfo is None:
            # A fixed time is entered in the selected timezone. Convert its
            # instant into the source's explicitly stored timezone offset.
            after = _from_ns(_to_ns(after, request.timezone), "UTC").astimezone(original.tzinfo)
        else:
            after = after.astimezone(original.tzinfo)
    else:
        raise ValueError("Missing date adjustment.")
    return metadata.format_date(after, before)


def preview(paths: Sequence[str | Path], request: Request, exiftool: str | None = None) -> Plan:
    """Inspect explicit files and create a reusable plan; never write media files."""
    if not isinstance(request, Request):
        raise ValueError("request must be a Request.")
    executable = exiftool or metadata.find_exiftool() if "captured" in request.targets else exiftool
    plan = Plan(files=[], request=request, exiftool=executable)
    seen: set[str] = set()
    for raw in paths:
        path = _absolute(raw)
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        item = FilePlan(path=str(path))
        plan.files.append(item)
        try:
            item.snapshot = _snapshot(path, "captured" in request.targets and metadata.supports_writing(path))
            for target in request.targets:
                if target == "captured":
                    if not metadata.supports_writing(path):
                        raise ValueError(
                            f"Embedded capture-date writing is not supported for {path.suffix or 'this format'}. "
                            "Select filesystem dates only for this file."
                        )
                    capture = metadata.read_dates(path, executable)
                    item.warnings.extend(capture.warnings)
                    item.snapshot["capture_dates"] = dict(capture.dates)
                    if not capture.dates:
                        raise ValueError("No supported existing capture dates were found; no tags will be created.")
                    updates = {}
                    for tag, before in capture.dates.items():
                        after = _new_capture(before, request, tag)
                        if request.fixed is not None and metadata.parse_date(before).tzinfo is None:
                            item.warnings.append(f"{tag} has no stored timezone; the entered fixed wall-clock time is used literally.")
                        if metadata.parse_date(before) != metadata.parse_date(after):
                            updates[tag] = after
                            item.changes.append(Change(f"captured:{tag}", before, after))
                    if updates:
                        item.operations["captured"] = updates
                        item.warnings.append("Embedded editing saves a full file backup for undo; allow enough free disk space.")
                else:
                    if target == "created" and os.name != "nt":
                        raise ValueError("Changing filesystem creation dates is supported on Windows only.")
                    before = item.snapshot["mtime_ns" if target == "modified" else "created_ns"]
                    if before is None:
                        raise ValueError("This filesystem does not expose a creation date.")
                    after = _new_ns(before, request)
                    if after != before:
                        item.operations[target] = after
                        item.changes.append(Change(target, _display_ns(before, request.timezone), _display_ns(after, request.timezone)))
        except (OSError, ValueError, OverflowError, metadata.MetadataError) as exc:
            item.errors.append(str(exc))
    return plan


def default_journal_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "DateFix" / "history"


def _save_json(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _set_created(path: Path, value: int) -> None:
    if os.name != "nt":
        raise ValueError("Changing creation dates requires Windows.")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                  ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    kernel.SetFileTime.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ticks = value // 100 + 116444736000000000
    if not 0 <= ticks < 2**64:
        raise ValueError("Creation date is outside the supported Windows range.")
    # FILE_WRITE_ATTRIBUTES, shared read/write/delete, OPEN_EXISTING.
    handle = kernel.CreateFileW(str(path), 0x100, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        stamp = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        if not kernel.SetFileTime(handle, ctypes.byref(stamp), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def _restore_times(path: Path, snapshot: dict) -> None:
    os.utime(path, ns=(snapshot["atime_ns"], snapshot["mtime_ns"]))
    if os.name == "nt" and snapshot.get("created_ns") is not None:
        _set_created(path, snapshot["created_ns"])


def _restore_content(path: Path, backup: Path, expected_hash: str) -> None:
    _safe_file(path)
    _safe_file(backup)
    if _hash(backup) != expected_hash:
        raise ValueError("The undo backup was changed or damaged; restoration was refused.")
    # Copy into the existing file: preserve inode, permissions, and creation time.
    with backup.open("rb") as source, path.open("wb") as destination:
        shutil.copyfileobj(source, destination, 1024 * 1024)
        destination.flush()
        os.fsync(destination.fileno())


def _progress(callback: Progress | None, index: int, total: int, path: str) -> None:
    if callback is not None:
        try:
            callback(index, total, path)
        except Exception:
            # UI/progress failures must not abort a write or strand its journal.
            pass


def _validate_operations(item: FilePlan, request: Request | None = None) -> None:
    if not item.snapshot or any(key not in {"modified", "created", "captured"} for key in item.operations):
        raise ValueError("Invalid saved plan. Generate a new preview.")
    for target, value in item.operations.items():
        if target == "captured":
            if not isinstance(value, dict) or not value or any(not metadata.is_capture_tag(tag) for tag in value):
                raise ValueError("Invalid metadata operations in saved plan.")
            if "sha256" not in item.snapshot:
                raise ValueError("The capture-date plan is missing its content fingerprint.")
            for date in value.values():
                metadata.parse_date(date)
        elif type(value) is not int:
            raise ValueError("Invalid filesystem timestamp in saved plan.")
    if request is not None:
        expected_operations = {}
        expected_changes = []
        for target in request.targets:
            if target == "captured":
                originals = item.snapshot.get("capture_dates")
                if not isinstance(originals, dict) or not originals:
                    raise ValueError("The capture plan is missing its original dates. Preview again.")
                updates = {}
                for tag, before in originals.items():
                    if not metadata.is_capture_tag(tag):
                        raise ValueError("Invalid original capture tag in saved plan.")
                    after = _new_capture(before, request, tag)
                    if metadata.parse_date(before) != metadata.parse_date(after):
                        updates[tag] = after
                        expected_changes.append(Change(f"captured:{tag}", before, after))
                if updates:
                    expected_operations[target] = updates
            else:
                before = item.snapshot["mtime_ns" if target == "modified" else "created_ns"]
                after = _new_ns(before, request)
                if after != before:
                    expected_operations[target] = after
                    expected_changes.append(Change(target, _display_ns(before, request.timezone), _display_ns(after, request.timezone)))
        if item.operations != expected_operations or item.changes != expected_changes:
            raise ValueError("Saved operations or displayed changes do not match the date request. Generate a new preview.")


def _safe_history_directory(path: Path) -> None:
    for part in (path, *path.parents):
        try:
            if _is_link(part):
                raise ValueError("The history directory must not contain symbolic links or junctions; choose its physical path.")
        except FileNotFoundError:
            continue


def apply(plan: Plan, journal_dir: str | Path | None = None, progress: Progress | None = None) -> Result:
    """Apply valid files independently; persist originals before any mutation."""
    if plan.version != SCHEMA_VERSION:
        raise ValueError("Unsupported plan version.")
    result = Result(files=[])
    active = [item for item in plan.files if not item.errors and item.operations]
    journal_path: Path | None = None
    journal = {"version": SCHEMA_VERSION, "kind": "datefix-journal", "created_at": datetime.now(UTC).isoformat(), "files": []}
    if active:
        directory = _absolute(journal_dir or default_journal_dir())
        _safe_history_directory(directory)
        directory.mkdir(parents=True, exist_ok=True)
        run_dir = directory / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12])
        run_dir.mkdir()
        journal_path = run_dir / "journal.json"
        _save_json(journal_path, journal)
        result.journal_path = str(journal_path)
    for index, item in enumerate(plan.files, 1):
        _progress(progress, index - 1, len(plan.files), item.path)
        if item.errors:
            result.files.append(FileResult(item.path, "error", "; ".join(item.errors)))
            continue
        try:
            _validate_operations(item, plan.request)
        except Exception as exc:
            result.files.append(FileResult(item.path, "error", str(exc)))
            continue
        if not item.operations:
            result.files.append(FileResult(item.path, "skipped", "The selected dates are already correct."))
            continue
        path = _absolute(item.path)
        entry = None
        mutated = False
        try:
            if not _matches(path, item.snapshot):
                raise ValueError("File changed since preview. Generate a new preview before applying.")
            entry = {"path": str(path), "before": item.snapshot, "state": "prepared", "operations": item.operations}
            if "captured" in item.operations:
                backup = journal_path.parent / f"{index:06d}.backup"
                shutil.copyfile(path, backup)
                if _hash(backup) != item.snapshot["sha256"] or not _matches(path, item.snapshot):
                    raise ValueError("File changed while creating its backup. Generate a new preview.")
                entry["backup"] = backup.name
            journal["files"].append(entry)
            _save_json(journal_path, journal)
            # Recheck after backing up and recording the original values.
            if not _matches(path, item.snapshot):
                raise ValueError("File changed immediately before application. Generate a new preview.")
            mutated = True
            if "captured" in item.operations:
                metadata.write_dates(path, item.operations["captured"], plan.exiftool)
            os.utime(path, ns=(item.snapshot["atime_ns"], item.operations.get("modified", item.snapshot["mtime_ns"])))
            if os.name == "nt":
                _set_created(path, item.operations.get("created", item.snapshot["created_ns"]))
            elif "created" in item.operations:
                raise ValueError("Changing creation dates requires Windows.")
            entry["after"] = _snapshot_after_write(path, item.snapshot["atime_ns"], "captured" in item.operations)
            expected_mtime = item.operations.get("modified", item.snapshot["mtime_ns"])
            if entry["after"]["mtime_ns"] != expected_mtime:
                raise ValueError("The filesystem could not save the exact requested modification date.")
            if os.name == "nt":
                expected_created = item.operations.get("created", item.snapshot["created_ns"])
                if entry["after"]["created_ns"] != expected_created // 100 * 100:
                    raise ValueError("The filesystem could not save the exact requested creation date.")
            entry["state"] = "applied"
            _save_json(journal_path, journal)
            result.files.append(FileResult(item.path, "applied", "Dates updated; undo information saved."))
        except Exception as exc:
            message = str(exc)
            if mutated and entry is not None:
                try:
                    if entry.get("backup"):
                        _restore_content(path, journal_path.parent / entry["backup"], item.snapshot["sha256"])
                    _restore_times(path, item.snapshot)
                    entry["state"] = "rolled_back"
                    message += " Original file and dates restored."
                except Exception as rollback_error:
                    entry["state"] = "recovery_required"
                    message += f" Restoration also failed: {rollback_error}. Keep the journal and backup for recovery."
            elif entry is not None:
                entry["state"] = "not_applied"
            if entry is not None:
                entry["error"] = message
                try:
                    _save_json(journal_path, journal)
                except Exception as journal_error:
                    message += f" Journal update failed: {journal_error}."
            result.files.append(FileResult(item.path, "error", message))
    _progress(progress, len(plan.files), len(plan.files), "")
    return result


def undo(journal_path: str | Path, progress: Progress | None = None) -> Result:
    """Restore only applied, unchanged files. Damaged or stale backups are refused."""
    location = _absolute(journal_path)
    _safe_file(location)
    with location.open(encoding="utf-8") as stream:
        journal = json.load(stream)
    if journal.get("kind") != "datefix-journal" or journal.get("version") != SCHEMA_VERSION:
        raise ValueError("This is not a supported DateFix undo journal.")
    entries = journal.get("files")
    if not isinstance(entries, list):
        raise ValueError("The undo journal is invalid.")
    result = Result(files=[], journal_path=str(location))
    for index, entry in enumerate(entries, 1):
        path = _absolute(entry["path"])
        _progress(progress, index - 1, len(entries), str(path))
        if entry.get("state") != "applied":
            status = "error" if entry.get("state") in {"prepared", "recovery_required", "undo_prepared"} else "skipped"
            message = "This file requires manual recovery; keep its journal and backup." if status == "error" else "This file has no applied change to undo."
            result.files.append(FileResult(str(path), status, message))
            continue
        changed = False
        try:
            if not _matches(path, entry["after"]):
                raise ValueError("File changed after application. Undo refused to protect newer changes.")
            backup = None
            if entry.get("backup"):
                name = entry["backup"]
                if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name:
                    raise ValueError("Invalid backup path in undo journal.")
                backup = location.parent / name
                _safe_file(backup)
                if _hash(backup) != entry["before"]["sha256"]:
                    raise ValueError("The undo backup was changed or damaged; restoration was refused.")
            entry["state"] = "undo_prepared"
            _save_json(location, journal)
            if not _matches(path, entry["after"]):
                raise ValueError("File changed immediately before undo. Restoration refused.")
            changed = True
            if backup is not None:
                _restore_content(path, backup, entry["before"]["sha256"])
            _restore_times(path, entry["before"])
            entry["state"] = "undone"
            entry["undone_at"] = datetime.now(UTC).isoformat()
            _save_json(location, journal)
            result.files.append(FileResult(str(path), "undone", "Original dates and any backed-up content restored."))
        except Exception as exc:
            # If nothing was mutated, the applied entry remains eligible for retry.
            if not changed:
                entry["state"] = "applied"
            entry["undo_error"] = str(exc)
            try:
                _save_json(location, journal)
            except Exception:
                pass
            result.files.append(FileResult(str(path), "error", str(exc) + (" Keep the journal and backup for recovery." if changed else "")))
    _progress(progress, len(entries), len(entries), "")
    return result


def plan_to_dict(plan: Plan) -> dict:
    data = asdict(plan)
    # Executable selection is a runtime setting, never portable plan content.
    data["exiftool"] = None
    data["request"]["fixed"] = plan.request.fixed.isoformat() if plan.request.fixed else None
    data["request"]["targets"] = list(plan.request.targets)
    return data


def plan_from_dict(data: dict) -> Plan:
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise ValueError("Unsupported or invalid saved plan.")
    try:
        raw_request = data["request"]
        request = Request(
            offset=Offset(**raw_request["offset"]) if raw_request.get("offset") is not None else None,
            fixed=datetime.fromisoformat(raw_request["fixed"]) if raw_request.get("fixed") else None,
            targets=tuple(raw_request["targets"]), timezone=raw_request["timezone"],
        )
        files = [FilePlan(path=raw["path"], changes=[Change(**change) for change in raw["changes"]],
                          warnings=list(raw["warnings"]), errors=list(raw["errors"]),
                          snapshot=dict(raw["snapshot"]), operations=dict(raw["operations"]))
                 for raw in data["files"]]
        for item in files:
            if not Path(item.path).is_absolute():
                raise ValueError("Saved file paths must be absolute.")
            if not item.errors:
                _validate_operations(item, request)
        executable = metadata.find_exiftool() if "captured" in request.targets else None
        return Plan(files=files, request=request, exiftool=executable,
                    version=data["version"], created_at=data["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid saved plan: {exc}") from exc
