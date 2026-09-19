"""Small, conservative ExifTool adapter. No GUI or third-party Python imports."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone


class MetadataError(RuntimeError):
    """The requested metadata operation could not be completed safely."""


# Explicit support is intentionally narrower than ExifTool's complete format list.
WRITABLE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".heic", ".heif",
    ".mp4", ".mov", ".m4v", ".3gp", ".3g2",
})
CAPTURE_TAGS = frozenset({
    "ExifIFD:DateTimeOriginal", "ExifIFD:CreateDate",
    "XMP-exif:DateTimeOriginal", "XMP-xmp:CreateDate", "XMP-photoshop:DateCreated",
    "QuickTime:CreateDate", "Keys:CreationDate", "UserData:DateTimeOriginal",
    "UserData:ContentCreateDate", "ItemList:ContentCreateDate",
})
_TRACK_TAG = re.compile(r"Track[1-9][0-9]*:(?:TrackCreateDate|MediaCreateDate)\Z")
_OFFSET_TAGS = {
    "ExifIFD:DateTimeOriginal": "ExifIFD:OffsetTimeOriginal",
    "ExifIFD:CreateDate": "ExifIFD:OffsetTimeDigitized",
}
_DATE = re.compile(
    r"^(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)


@dataclass
class CaptureMetadata:
    dates: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def find_exiftool() -> str | None:
    """Find an explicitly configured, bundled, or PATH ExifTool executable."""
    configured = os.environ.get("DATEFIX_EXIFTOOL")
    if configured:
        candidate = Path(configured).expanduser()
        return str(candidate.absolute()) if candidate.is_file() else shutil.which(configured)
    project = Path(__file__).resolve().parents[2]
    roots = [project]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).parent)
    if hasattr(sys, "_MEIPASS"):
        roots.append(Path(sys._MEIPASS))
    for root in roots:
        for directory in (root / "tools" / "exiftool", root / "tools" / "exiftool" / "bin"):
            for name in ("exiftool.exe", "exiftool"):
                candidate = directory / name
                if candidate.is_file():
                    return str(candidate)
    return shutil.which("exiftool") or shutil.which("exiftool.exe")


def is_capture_tag(tag: str) -> bool:
    return tag in CAPTURE_TAGS or _TRACK_TAG.fullmatch(tag) is not None


def is_quicktime_integer(tag: str) -> bool:
    return tag == "QuickTime:CreateDate" or _TRACK_TAG.fullmatch(tag) is not None


def supports_writing(path: str | Path) -> bool:
    return Path(path).suffix.lower() in WRITABLE_EXTENSIONS


def parse_date(value: str) -> datetime:
    """Parse an ExifTool date without assuming a timezone for naive values."""
    match = _DATE.fullmatch(value.strip())
    if match is None:
        raise MetadataError(f"Unrecognized capture date: {value!r}")
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    normalized = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
    if fraction:
        normalized += "." + fraction[:6]
    if zone:
        normalized += "+00:00" if zone == "Z" else zone
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MetadataError(f"Invalid or unset capture date: {value!r}") from exc


def format_date(value: datetime, original: str) -> str:
    """Keep the source's precision; explicit UTC offsets remain explicit."""
    match = _DATE.fullmatch(original.strip())
    if match is None:
        raise MetadataError(f"Unrecognized capture date: {original!r}")
    output = value.strftime("%Y:%m:%d %H:%M:%S")
    # strftime may omit leading zeroes for years < 1000 on some platforms.
    output = f"{value.year:04d}" + output[output.index(":"):]
    fraction = match.group(7)
    if fraction:
        output += "." + (f"{value.microsecond:06d}" + fraction[6:])[:len(fraction)]
    if value.utcoffset() is not None:
        offset = value.strftime("%z")
        output += offset[:3] + ":" + offset[3:]
    return output


def _run(executable: str, arguments: list[str], timeout: int = 180) -> str:
    if any("\n" in arg or "\r" in arg or "\0" in arg for arg in arguments):
        raise MetadataError("ExifTool cannot safely handle a newline in this path or value.")
    # UTF-8 argument input avoids Windows command-line filename encoding problems.
    # Disable user config, which may otherwise run arbitrary Perl at startup.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [executable, "-config", "", "-charset", "filename=utf8", "-@", "-"],
            input="\n".join(arguments) + "\n", capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False, creationflags=flags,
            env={**os.environ, "TZ": "UTC0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MetadataError(f"Could not run ExifTool: {exc}") from exc
    if result.returncode != 0:
        raise MetadataError((result.stderr or result.stdout or "ExifTool failed.").strip())
    return result.stdout


def read_dates(path: str | Path, executable: str | None = None) -> CaptureMetadata:
    executable = executable or find_exiftool()
    if not executable:
        raise MetadataError("Capture dates require ExifTool. Install it or set DATEFIX_EXIFTOOL.")
    output = _run(executable, ["-j", "-G1:4", "-a", "-s", "-n", "-time:all",
                              "-OffsetTimeOriginal", "-OffsetTimeDigitized",
                              "-api", "QuickTimeUTC=1", "-api", "TimeZone=+00:00",
                              "-api", "LargeFileSupport=1",
                              str(Path(path).absolute())])
    def unique_properties(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("ExifTool returned duplicate JSON properties")
            result[key] = value
        return result
    try:
        records = json.loads(output, object_pairs_hook=unique_properties)
        if not isinstance(records, list) or len(records) != 1:
            raise ValueError("Expected one metadata record")
        record = records[0]
        if not isinstance(record, dict):
            raise ValueError("Expected a metadata object")
    except (ValueError, TypeError) as exc:
        raise MetadataError("ExifTool returned invalid metadata.") from exc
    result = CaptureMetadata()
    unique_tags = {}
    for grouped, value in record.items():
        # Family 4 exposes repeated instances that JSON would otherwise hide.
        # Keep family 1 for writing, but reject ambiguous repeated capture tags.
        tag = ":".join(part for part in grouped.split(":") if not re.fullmatch(r"Copy\d+", part))
        if tag in unique_tags and (is_capture_tag(tag) or tag in _OFFSET_TAGS.values()):
            raise MetadataError(f"Duplicate instances of {tag} cannot be edited safely.")
        unique_tags[tag] = value
    for tag, value in unique_tags.items():
        if tag.endswith(":Error") or tag == "Error":
            raise MetadataError(str(value))
        if tag.endswith(":Warning") or tag == "Warning":
            result.warnings.append(str(value))
        if is_capture_tag(tag) and isinstance(value, str):
            try:
                parsed = parse_date(value)
                companion = unique_tags.get(_OFFSET_TAGS.get(tag, ""))
                if companion is not None and parsed.tzinfo is None:
                    if not isinstance(companion, str) or not re.fullmatch(r"[+-]\d{2}:\d{2}", companion):
                        raise MetadataError(f"Unsupported timezone companion for {tag}.")
                    value += companion
                    parsed = parse_date(value)
                if is_quicktime_integer(tag):
                    # TZ + the API TimeZone setting make unzoned tool output UTC.
                    parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
                    value = format_date(parsed, value)
            except MetadataError as exc:
                result.warnings.append(f"{tag}: {exc}")
            else:
                result.dates[tag] = value
    if any(tag.startswith(("QuickTime:", "Track")) for tag in result.dates):
        result.warnings.append(
            "QuickTime integer dates are interpreted as UTC, as specified by the format. "
            "Some cameras store local time instead; check the preview."
        )
    return result


def write_dates(path: str | Path, values: dict[str, str], executable: str | None = None) -> None:
    if not supports_writing(path):
        raise MetadataError(f"Embedded capture-date writing is not supported for {Path(path).suffix or 'this format'}.")
    if not values or any(not is_capture_tag(tag) for tag in values):
        raise MetadataError("Only existing, supported capture-date tags may be written.")
    for value in values.values():
        parse_date(value)
    executable = executable or find_exiftool()
    if not executable:
        raise MetadataError("Capture dates require ExifTool.")
    current = read_dates(path, executable).dates
    if not values.keys() <= current.keys():
        raise MetadataError("A requested capture tag no longer exists. Preview the file again.")
    assignments = []
    for tag, value in values.items():
        if tag in _OFFSET_TAGS:
            # EXIF stores offsets separately. Keep that companion and write only
            # the adjusted wall clock to the base date field.
            value = format_date(parse_date(value).replace(tzinfo=None), value)
        assignments.append(f"-{tag}={value}")
    _run(executable, ["-overwrite_original_in_place", "-P", "-wm", "w", "-n",
                      "-api", "QuickTimeUTC=1", "-api", "TimeZone=+00:00",
                      "-api", "LargeFileSupport=1", *assignments,
                      str(Path(path).absolute())])
    actual = read_dates(path, executable).dates
    for tag, expected in values.items():
        if tag not in actual or parse_date(actual[tag]) != parse_date(expected):
            raise MetadataError(f"ExifTool did not save the expected value for {tag}.")
