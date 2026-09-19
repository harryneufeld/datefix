"""End-to-end command-line behavior using temporary files and real subprocesses."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
BASE_DATE = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)


def run_cli(*arguments, no_site=False):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable]
    if no_site:
        command.append("-S")
    return subprocess.run(
        [*command, "-m", "datefix", *(str(argument) for argument in arguments)],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )


def json_output(result, expected_status=0):
    assert result.returncode == expected_status, (result.stdout, result.stderr)
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def media(tmp_path):
    source = tmp_path / "Family clip ä.avi"
    source.write_bytes(b"Synthetic fixture: file dates do not require a valid media container.")
    original_ns = int(BASE_DATE.timestamp()) * 10**9
    os.utime(source, ns=(original_ns, original_ns))
    return source


def test_shift_previews_by_default_without_mutating_or_creating_history(media, tmp_path):
    original_stat = media.stat()
    original_bytes = media.read_bytes()
    history = tmp_path / "history"
    output = json_output(run_cli(
        "shift", media, "--years", 2, "--days", 16, "--hours", 6,
        "--timezone", "UTC", "--journal-dir", history, "--json",
    ))
    assert output["preview"] is True
    assert len(output["files"]) == 1
    file = output["files"][0]
    assert file["path"] == str(media)
    assert not file["errors"]
    assert file["changes"] == [{
        "field": "modified", "before": "2024-01-01 11:00:00+00:00",
        "after": "2026-01-17 17:00:00+00:00",
    }]
    assert media.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert media.read_bytes() == original_bytes
    if hasattr(original_stat, "st_birthtime_ns"):
        assert media.stat().st_birthtime_ns == original_stat.st_birthtime_ns
    assert not history.exists()


def test_cli_apply_then_undo_and_history(media, tmp_path):
    original_ns = media.stat().st_mtime_ns
    original_bytes = media.read_bytes()
    history = tmp_path / "history"
    applied = json_output(run_cli(
        "shift", media, "--hours", 6, "--timezone", "UTC",
        "--apply", "--journal-dir", history, "--json",
    ))
    assert applied["files"][0]["status"] == "applied"
    assert media.stat().st_mtime_ns == original_ns + 6 * 3600 * 10**9
    journal = Path(applied["journal_path"])
    assert journal.is_file()
    assert journal.is_relative_to(history)
    assert json_output(run_cli("history", "--journal-dir", history, "--json")) == [str(journal)]

    restored = json_output(run_cli("undo", journal, "--json"))
    assert restored["files"][0]["status"] == "undone"
    assert media.stat().st_mtime_ns == original_ns
    assert media.read_bytes() == original_bytes
    repeated = json_output(run_cli("undo", journal, "--json"))
    assert repeated["files"][0]["status"] == "skipped"


def test_negative_offsets_apply_as_subtraction(media, tmp_path):
    original_ns = media.stat().st_mtime_ns
    output = json_output(run_cli(
        "shift", media, "--hours", -6, "--seconds", -17, "--timezone", "UTC",
        "--apply", "--journal-dir", tmp_path / "history", "--json",
    ))
    assert output["files"][0]["status"] == "applied"
    assert media.stat().st_mtime_ns == original_ns - (6 * 3600 + 17) * 10**9


@pytest.mark.parametrize("months, expected", [
    (1, datetime(2024, 2, 29, 11, tzinfo=timezone.utc)),
    (-1, datetime(2023, 12, 31, 11, tzinfo=timezone.utc)),
    (14, datetime(2025, 3, 31, 11, tzinfo=timezone.utc)),
])
def test_calendar_month_cli_preview_apply_and_undo(media, tmp_path, months, expected):
    original_ns = int(datetime(2024, 1, 31, 11, tzinfo=timezone.utc).timestamp()) * 10**9
    os.utime(media, ns=(original_ns, original_ns))
    history = tmp_path / "history"
    arguments = ("shift", media, "--months", months, "--timezone", "UTC", "--journal-dir", history, "--json")
    previewed = json_output(run_cli(*arguments))
    assert previewed["files"][0]["changes"][0]["after"] == expected.isoformat(sep=" ")
    assert media.stat().st_mtime_ns == original_ns
    assert not history.exists()
    applied = json_output(run_cli(*arguments, "--apply"))
    assert applied["files"][0]["status"] == "applied"
    assert media.stat().st_mtime_ns == int(expected.timestamp()) * 10**9
    restored = json_output(run_cli("undo", applied["journal_path"], "--json"))
    assert restored["files"][0]["status"] == "undone"
    assert media.stat().st_mtime_ns == original_ns


@pytest.mark.parametrize("date", ["2026-04-21T11:27:50Z", "2026-04-21T11:27:50"])
def test_set_accepts_explicit_or_selected_utc(media, tmp_path, date):
    output = json_output(run_cli(
        "set", media, "--at", date, "--timezone", "UTC", "--apply",
        "--journal-dir", tmp_path / "history", "--json",
    ))
    assert output["files"][0]["status"] == "applied"
    expected = datetime(2026, 4, 21, 11, 27, 50, tzinfo=timezone.utc)
    assert media.stat().st_mtime_ns == int(expected.timestamp()) * 10**9


def test_invalid_field_returns_useful_error_without_changes(media, tmp_path):
    original_ns = media.stat().st_mtime_ns
    output = json_output(run_cli(
        "shift", media, "--hours", 1, "--fields", "renamed", "--apply",
        "--journal-dir", tmp_path / "history", "--json",
    ), expected_status=1)
    assert "error" in output
    assert "modified" in output["error"]
    assert media.stat().st_mtime_ns == original_ns
    assert not (tmp_path / "history").exists()


def test_unsupported_capture_field_does_not_partially_apply_file_dates(media, tmp_path):
    original_ns = media.stat().st_mtime_ns
    history = tmp_path / "history"
    output = json_output(run_cli(
        "shift", media, "--hours", 1, "--fields", "modified,captured", "--apply",
        "--journal-dir", history, "--json",
    ), expected_status=1)
    assert output["files"][0]["status"] == "error"
    assert "not supported" in output["files"][0]["message"]
    assert media.stat().st_mtime_ns == original_ns
    assert output["journal_path"] is None
    assert not history.exists()


def test_cli_runs_without_site_packages_or_qt(media):
    # -S removes all installed extras, including PySide6. Real CLI preview must
    # still work because the core and command-line interface use only stdlib.
    output = json_output(run_cli(
        "shift", media, "--minutes", 1, "--timezone", "UTC", "--json", no_site=True,
    ))
    assert output["preview"] is True
    assert output["files"][0]["changes"][0]["after"] == "2024-01-01 11:01:00+00:00"


def test_doctor_returns_machine_readable_capabilities_without_qt():
    output = json_output(run_cli("doctor", no_site=True))
    assert output["version"] == "0.1.0"
    assert output["platform"] == sys.platform
    assert output["file_modified"] is True
    assert output["file_created"] is (os.name == "nt")
    assert output["exiftool"] is None or isinstance(output["exiftool"], str)
    assert Path(output["history"]).is_absolute()
    assert output["python"]


def test_empty_history_is_reported_without_creating_a_directory(tmp_path):
    folder = tmp_path / "new-history"
    assert json_output(run_cli("history", "--journal-dir", folder, "--json")) == []
    result = run_cli("history", "--journal-dir", folder)
    assert result.returncode == 0
    assert "No history yet" in result.stdout
    assert not folder.exists()


def test_recursive_flag_controls_folder_expansion(tmp_path):
    direct = tmp_path / "direct.jpg"
    direct.write_bytes(b"Direct synthetic fixture")
    nested = tmp_path / "nested"
    nested.mkdir()
    child = nested / "child.mp4"
    child.write_bytes(b"Nested synthetic fixture")
    shallow = json_output(run_cli("shift", tmp_path, "--days", 1, "--json"))
    assert {entry["path"] for entry in shallow["files"]} == {str(direct)}
    recursive = json_output(run_cli("shift", tmp_path, "--days", 1, "--recursive", "--json"))
    assert {entry["path"] for entry in recursive["files"]} == {str(direct), str(child)}


def test_invalid_exact_date_and_missing_file_return_failure(media, tmp_path):
    output = json_output(run_cli("set", media, "--at", "not-a-date", "--json"), expected_status=1)
    assert output["error"]
    missing = json_output(run_cli("shift", tmp_path / "missing.jpg", "--hours", 1, "--json"), expected_status=1)
    assert missing["files"][0]["errors"]
