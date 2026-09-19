"""Offscreen desktop tests: plan invalidation and the background apply/undo flow."""
import os
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QDateTime, QDate, QTime
from PySide6.QtWidgets import QApplication

from datefix import core, gui


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(gui.STYLE)
    return app


@pytest.fixture
def window(application, monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "default_journal_dir", lambda: tmp_path / "history")
    instance = gui.MainWindow()
    yield instance
    if instance._busy:
        wait_until_idle(instance, application)
    instance.close()
    instance.deleteLater()
    application.processEvents()


def wait_until_idle(window, application, timeout=10):
    deadline = time.monotonic() + timeout
    while window._busy and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()
    assert not window._busy, "Desktop operation did not finish"


def test_preview_is_required_and_settings_invalidate_it(window, tmp_path):
    source = tmp_path / "example.avi"
    source.write_bytes(b"DateFix UI test fixture")
    window.add_paths([source, source])
    assert len(window.sources) == 1
    assert not window.apply_button.isEnabled()

    window._set_preset()
    request = window._request()
    assert request.offset == core.Offset(years=2, days=16, hours=6)
    assert request.targets == ("modified",)
    window._display_plan(core.preview([source], request))
    assert window.apply_button.isEnabled()
    assert window.table.rowCount() == 1

    window.offset_inputs["days"].setValue(17)
    assert window.plan is None
    assert not window.apply_button.isEnabled()
    assert window.table.rowCount() == 0


def test_exact_time_uses_selected_timezone(window):
    window.fixed_mode.setChecked(True)
    window.fixed_datetime.setDateTime(QDateTime(QDate(2024, 2, 29), QTime(13, 14, 15)))
    window.timezone.setCurrentIndex(1)
    request = window._request()
    assert request.offset is None
    assert request.fixed == datetime(2024, 2, 29, 13, 14, 15, tzinfo=timezone.utc)
    window.timezone.setCurrentIndex(0)
    assert window._request().fixed.tzinfo is None


def test_months_accept_negative_values_invalidate_preview_and_reset_with_preset(window, tmp_path):
    source = tmp_path / "month-preview.avi"
    source.write_bytes(b"DateFix UI test fixture")
    window.add_paths([source])
    window.offset_inputs["months"].setValue(-2)
    assert window._request().offset.months == -2
    window._display_plan(core.preview([source], window._request()))
    assert window.apply_button.isEnabled()
    window.offset_inputs["months"].setValue(1)
    assert window.plan is None
    assert not window.apply_button.isEnabled()
    window._set_preset()
    assert window._request().offset == core.Offset(years=2, days=16, hours=6, months=0)
    assert window.offset_inputs["months"].value() == 0


def test_background_preview_apply_and_undo(window, application, monkeypatch, tmp_path):
    source = tmp_path / "clip.avi"
    source.write_bytes(b"DateFix UI test fixture")
    original_ns = int(datetime(2024, 1, 31, tzinfo=timezone.utc).timestamp()) * 10**9 + 123456700
    os.utime(source, ns=(original_ns, original_ns))
    actual_original = source.stat().st_mtime_ns
    monkeypatch.setattr(gui, "apply", lambda plan, progress: core.apply(plan, tmp_path / "history", progress))
    window.add_paths([source])
    window.offset_inputs["months"].setValue(1)
    window.offset_inputs["hours"].setValue(6)
    window.timezone.setCurrentIndex(1)

    window._preview()
    assert window._busy
    assert not window.add_files_button.isEnabled()
    assert not window.apply_button.isEnabled()
    wait_until_idle(window, application)
    assert source.stat().st_mtime_ns == actual_original
    assert window.apply_button.isEnabled()

    window._apply()
    wait_until_idle(window, application)
    expected_ns = int(datetime(2024, 2, 29, 6, tzinfo=timezone.utc).timestamp()) * 10**9 + 123456700
    assert source.stat().st_mtime_ns == expected_ns
    assert window.plan is None
    assert not window.apply_button.isEnabled()
    assert window.undo_button.isEnabled()
    assert window._latest_journal.is_file()
    assert window.table.item(0, 4).text() == "Applied"

    window._undo_latest()
    wait_until_idle(window, application)
    assert source.stat().st_mtime_ns == actual_original
    assert window.table.item(0, 4).text() == "Undone"


def test_journal_is_discovered_after_restart(window, tmp_path):
    journal = tmp_path / "history" / "20260101-run" / "journal.json"
    journal.parent.mkdir(parents=True)
    journal.write_text("{}", encoding="utf-8")
    window._refresh_history()
    window._refresh_buttons()
    assert window._latest_journal == journal
    assert window.undo_button.isEnabled()
