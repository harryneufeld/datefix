"""Real ExifTool/media checks. Optional tools are skipped in a core-only install."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from datefix import core, metadata


@pytest.fixture
def engine():
    executable = metadata.find_exiftool()
    if not executable:
        pytest.skip("ExifTool is not installed")
    return executable


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put_tags(path, engine, *tags):
    metadata._run(engine, ["-overwrite_original", *tags, str(path)])


def test_real_jpeg_capture_offset_and_byte_exact_undo(tmp_path, engine):
    image = pytest.importorskip("PIL.Image")
    photo = tmp_path / "Urlaub – Grüße.jpg"
    image.new("RGB", (32, 24), "teal").save(photo)
    put_tags(photo, engine, "-EXIF:DateTimeOriginal=2024:02:29 08:15:12",
             "-EXIF:CreateDate=2024:02:29 08:15:12", "-EXIF:OffsetTimeOriginal=+02:00",
             "-EXIF:OffsetTimeDigitized=+02:00", "-EXIF:Artist=DateFix test")
    before_hash, before_stat = digest(photo), photo.stat()
    before_pixels = image.open(photo).tobytes()
    plan = core.preview([photo], core.Request(offset=core.Offset(years=1, days=16, hours=6), targets=("captured",)))
    assert not plan.files[0].errors
    result = core.apply(plan, tmp_path / "history")
    assert result.files[0].status == "applied", result.files[0].message
    saved = metadata.read_dates(photo, engine)
    assert saved.dates["ExifIFD:DateTimeOriginal"] == "2025:03:16 14:15:12+02:00"
    assert saved.dates["ExifIFD:CreateDate"] == "2025:03:16 14:15:12+02:00"
    assert photo.stat().st_mtime_ns == before_stat.st_mtime_ns
    if os.name == "nt":
        assert photo.stat().st_birthtime_ns == before_stat.st_birthtime_ns
    assert image.open(photo).tobytes() == before_pixels
    assert "DateFix test" in metadata._run(engine, ["-Artist", str(photo)])
    restored = core.undo(result.journal_path)
    assert restored.files[0].status == "undone", restored.files[0].message
    assert digest(photo) == before_hash
    assert photo.stat().st_mtime_ns == before_stat.st_mtime_ns


def test_real_jpeg_fixed_utc_respects_separate_offset(tmp_path, engine):
    image = pytest.importorskip("PIL.Image")
    photo = tmp_path / "offset.jpg"
    image.new("RGB", (16, 16), "blue").save(photo)
    put_tags(photo, engine, "-EXIF:DateTimeOriginal=2024:01:02 10:00:00", "-EXIF:OffsetTimeOriginal=+02:00")
    at = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    plan = core.preview([photo], core.Request(fixed=at, targets=("captured", "modified"), timezone="UTC"))
    assert not plan.files[0].errors
    result = core.apply(plan, tmp_path / "history")
    assert result.files[0].status == "applied", result.files[0].message
    actual = metadata.parse_date(metadata.read_dates(photo, engine).dates["ExifIFD:DateTimeOriginal"])
    assert actual == at
    assert photo.stat().st_mtime_ns == int(at.timestamp()) * 1_000_000_000


def test_real_mp4_capture_shift_keeps_playable_video_and_undo(tmp_path, engine):
    ffmpeg = pytest.importorskip("imageio_ffmpeg").get_ffmpeg_exe()
    video = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=c=teal:s=64x48:r=5",
                    "-t", "1", "-c:v", "mpeg4", "-metadata", "creation_time=2024-04-05T03:27:50Z",
                    str(video)], check=True, capture_output=True)
    before_hash = digest(video)
    decode = [ffmpeg, "-v", "error", "-i", str(video), "-map", "0:v:0", "-f", "md5", "-"]
    before_frames = subprocess.run(decode, check=True, capture_output=True).stdout
    request = core.Request(offset=core.Offset(years=2, days=16, hours=6), targets=("captured",), timezone="UTC")
    plan = core.preview([video], request)
    assert not plan.files[0].errors, plan.files[0].errors
    assert len(plan.files[0].changes) >= 3
    result = core.apply(plan, tmp_path / "history")
    assert result.files[0].status == "applied", result.files[0].message
    dates = metadata.read_dates(video, engine).dates
    expected = datetime(2026, 4, 21, 9, 27, 50, tzinfo=timezone.utc)
    for tag, value in dates.items():
        if metadata.is_quicktime_integer(tag):
            assert metadata.parse_date(value) == expected
    assert subprocess.run(decode, check=True, capture_output=True).stdout == before_frames
    assert core.undo(result.journal_path).files[0].status == "undone"
    assert digest(video) == before_hash


def test_sample_avi_copy_roundtrip(tmp_path):
    location = os.environ.get("DATEFIX_SAMPLE_DIR")
    if not location:
        pytest.skip("Set DATEFIX_SAMPLE_DIR to verify a copy of the smallest AVI sample")
    samples = list(Path(location).glob("*.AVI"))
    assert samples
    original = min(samples, key=lambda path: path.stat().st_size)
    original_stat, original_hash = original.stat(), digest(original)
    duplicate = tmp_path / original.name
    shutil.copy2(original, duplicate)
    before = duplicate.stat()
    targets = ("modified", "created") if os.name == "nt" else ("modified",)
    plan = core.preview([duplicate], core.Request(offset=core.Offset(years=2, days=16, hours=6), targets=targets, timezone="UTC"))
    result = core.apply(plan, tmp_path / "history")
    assert result.files[0].status == "applied", result.files[0].message
    assert digest(duplicate) == original_hash
    assert duplicate.stat().st_mtime_ns != before.st_mtime_ns
    assert core.undo(result.journal_path).files[0].status == "undone"
    assert duplicate.stat().st_mtime_ns == before.st_mtime_ns
    if os.name == "nt":
        assert duplicate.stat().st_birthtime_ns == before.st_birthtime_ns
        assert original.stat().st_birthtime_ns == original_stat.st_birthtime_ns
    assert digest(duplicate) == original_hash
    assert original.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert digest(original) == original_hash
