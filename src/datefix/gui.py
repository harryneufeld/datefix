"""Cross-platform desktop view for DateFix's independent core library."""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDateTime, QObject, QThread, Qt, QUrl, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDateTimeEdit,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .core import Offset, Request, apply, default_journal_dir, discover, preview, undo
from .metadata import find_exiftool


STYLE = """
QMainWindow, QWidget#canvas { background: #f5f6f4; color: #182b36; }
QWidget { font-family: 'Segoe UI', 'DejaVu Sans', sans-serif; font-size: 13px; color: #182b36; }
QFrame#card { background: #ffffff; border: 1px solid #dfe5e1; border-radius: 12px; }
QLabel { background: transparent; }
QLabel#brand { color: #102f36; font-size: 21px; font-weight: 700; }
QLabel#logo { background: #117f75; color: white; border-radius: 9px; font-size: 17px; font-weight: 700; }
QLabel#eyebrow { color: #718087; font-size: 10px; font-weight: 600; letter-spacing: 1.5px; }
QLabel#hero { font-size: 26px; font-weight: 650; color: #172f38; }
QLabel#subtitle { font-size: 13px; color: #67777e; }
QLabel#section { font-size: 16px; font-weight: 650; }
QLabel#hint { font-size: 12px; color: #6a7b80; }
QLabel#pill { background: #e5efeb; color: #366759; border-radius: 12px; padding: 7px 12px; font-size: 11px; }
QLabel#dropHint { color: #71847f; font-size: 13px; }
QLabel#summary { font-weight: 600; color: #1f4645; }
QPushButton { background: #ffffff; border: 1px solid #d0dbd6; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #edf5f1; border-color: #8cb5a8; }
QPushButton:pressed { background: #dcebe4; }
QPushButton:disabled { color: #9aaba5; background: #f3f6f3; border-color: #e4e9e5; }
QPushButton#primary { background: #087f72; color: #ffffff; border: 1px solid #087f72; padding: 11px 20px; }
QPushButton#primary:hover { background: #06685e; }
QPushButton#primary:disabled { background: #cadfd7; color: #789b90; border-color: #cadfd7; }
QPushButton#secondary { padding: 11px 18px; }
QPushButton#textButton { background: transparent; border: 0; color: #26776b; padding: 5px 2px; text-align: left; font-size: 12px; }
QPushButton#textButton:disabled { color: #a4b3ab; }
QPushButton#preset { background: #eef6f1; color: #307465; border: 1px solid #dcebe2; font-size: 11px; padding: 7px 8px; }
QSpinBox, QDateTimeEdit, QComboBox { background: #ffffff; border: 1px solid #d6dfda; border-radius: 6px; padding: 7px 8px; min-height: 18px; }
QSpinBox:focus, QDateTimeEdit:focus, QComboBox:focus { border-color: #258e7e; }
QSpinBox:disabled, QDateTimeEdit:disabled, QComboBox:disabled { color: #9aaba5; background: #f5f7f5; }
QCheckBox, QRadioButton { spacing: 8px; padding: 3px 0; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #b8c9bf; border-radius: 4px; background: white; }
QCheckBox::indicator:checked { background: #168777; border-color: #168777; image: none; }
QCheckBox::indicator:disabled { background: #edf1ed; border-color: #dbe1dc; }
QRadioButton::indicator { width: 14px; height: 14px; border: 1px solid #bdcec3; border-radius: 8px; background: white; }
QRadioButton::indicator:checked { background: #178576; border: 3px solid #d8ebe2; }
QListWidget { border: 1px dashed #ccdcd1; border-radius: 8px; background: #f8fbf7; padding: 6px; outline: none; }
QListWidget::item { padding: 5px 6px; border-radius: 4px; }
QListWidget::item:selected { background: #dcece4; color: #194d43; }
QTableWidget { border: 0; background: white; gridline-color: #edf0ed; selection-background-color: #e8f3ee; selection-color: #234b40; outline: none; }
QTableWidget::item { padding: 8px 6px; border-bottom: 1px solid #eef1ee; }
QHeaderView::section { background: #f4f7f3; color: #65776e; border: 0; border-bottom: 1px solid #e2e9e2; padding: 11px 6px; font-size: 11px; font-weight: 600; }
QProgressBar { border: 0; background: #e4ede6; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #168777; border-radius: 3px; }
QScrollArea { background: transparent; border: 0; }
QScrollBar:vertical { background: #f3f5f1; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #c9d6cd; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #183d34; color: white; padding: 6px; border: 0; }
"""


def _label(text: str, kind: str = "hint", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(kind)
    label.setWordWrap(wrap)
    return label


def _button(text: str, callback: Callable, kind: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(kind)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(callback)
    return button


class _Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, action: Callable) -> None:
        super().__init__()
        self.action = action

    @Slot()
    def run(self) -> None:
        try:
            result = self.action(self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc) or "The operation could not be completed.")
        else:
            self.finished.emit(result)


class MainWindow(QMainWindow):
    """Thin view/controller; every date operation is delegated to the core."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DateFix — File & capture dates")
        self.resize(1260, 920)
        self.setMinimumSize(1060, 740)
        self.setAcceptDrops(True)
        self.sources: list[Path] = []
        self.plan = None
        self._busy = False
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._operation = ""
        self._row_paths: list[str] = []
        self._latest_journal: Path | None = None
        self._build_ui()
        self._refresh_history()
        self._refresh_buttons()

    def _build_ui(self) -> None:
        canvas = QWidget()
        canvas.setObjectName("canvas")
        self.setCentralWidget(canvas)
        outer = QVBoxLayout(canvas)
        outer.setContentsMargins(28, 20, 28, 20)
        outer.setSpacing(18)
        header = QHBoxLayout()
        logo = _label("Df", "logo")
        logo.setFixedSize(40, 40)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(logo)
        branding = QVBoxLayout()
        branding.setSpacing(1)
        branding.addWidget(_label("DateFix", "brand"))
        branding.addWidget(_label("FILE & CAPTURE DATES", "eyebrow"))
        header.addLayout(branding)
        header.addStretch()
        exiftool = find_exiftool()
        status = "Metadata engine ready" if exiftool else "File dates ready · ExifTool needed for capture dates"
        self.engine_status = _label(status, "pill")
        self.engine_status.setToolTip("ExifTool enables reading and changing embedded photo and video dates.\nFile dates work without it.")
        header.addWidget(self.engine_status)
        outer.addLayout(header)
        hero = QVBoxLayout()
        hero.setSpacing(5)
        hero.addWidget(_label("Get your dates in order.", "hero"))
        hero.addWidget(_label("Shift a collection or set an exact time. Review every change before applying it.", "subtitle"))
        outer.addLayout(hero)

        content = QHBoxLayout()
        content.setSpacing(18)
        self.editor = self._build_editor()
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setWidget(self.editor)
        editor_scroll.setFixedWidth(316)
        content.addWidget(editor_scroll)
        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(self._build_sources())
        right.addWidget(self._build_preview(), 1)
        content.addLayout(right, 1)
        outer.addLayout(content, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer.addWidget(self.progress)
        footer = QHBoxLayout()
        footer_text = QVBoxLayout()
        footer_text.setSpacing(4)
        self.summary = _label("Add files or folders to get started.", "summary")
        self.summary.setWordWrap(True)
        self.detail = _label("Original dates are saved to a local undo journal when you apply changes.")
        self.detail.setWordWrap(True)
        footer_text.addWidget(self.summary)
        footer_text.addWidget(self.detail)
        footer.addLayout(footer_text, 1)
        self.preview_button = _button("Preview changes", self._preview, "secondary")
        self.apply_button = _button("Apply changes", self._apply, "primary")
        footer.addWidget(self.preview_button)
        footer.addWidget(self.apply_button)
        outer.addLayout(footer)

        QShortcut(QKeySequence.StandardKey.Open, self, self._add_files)
        remove = QShortcut(QKeySequence.StandardKey.Delete, self.source_list)
        remove.setContext(Qt.ShortcutContext.WidgetShortcut)
        remove.activated.connect(self._remove_selected)

    def _build_editor(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(_label("1   Choose a change", "section"))
        modes = QHBoxLayout()
        self.shift_mode = QRadioButton("Shift dates")
        self.fixed_mode = QRadioButton("Set exact time")
        self.shift_mode.setChecked(True)
        mode_group = QButtonGroup(frame)
        mode_group.addButton(self.shift_mode)
        mode_group.addButton(self.fixed_mode)
        modes.addWidget(self.shift_mode)
        modes.addWidget(self.fixed_mode)
        layout.addLayout(modes)
        self.mode_stack = QStackedWidget()
        shift = QWidget()
        shift_layout = QVBoxLayout(shift)
        shift_layout.setContentsMargins(0, 0, 0, 0)
        shift_layout.setSpacing(10)
        shift_layout.addWidget(_label("Positive adds time. Negative subtracts.", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.offset_inputs: dict[str, QSpinBox] = {}
        for index, name in enumerate(("years", "months", "days", "hours", "minutes", "seconds")):
            row, col = (index // 3) * 2, index % 3
            label = _label(name.capitalize())
            spin = QSpinBox()
            spin.setRange(-9999, 9999)
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setAccessibleName(f"Offset {name}")
            spin.valueChanged.connect(self._invalidate_plan)
            self.offset_inputs[name] = spin
            grid.addWidget(label, row, col)
            grid.addWidget(spin, row + 1, col)
        shift_layout.addLayout(grid)
        self.preset_button = _button("Use +2 years, +16 days, +6 hours", self._set_preset, "preset")
        shift_layout.addWidget(self.preset_button)
        shift_layout.addStretch()
        self.mode_stack.addWidget(shift)
        fixed = QWidget()
        fixed_layout = QVBoxLayout(fixed)
        fixed_layout.setContentsMargins(0, 0, 0, 0)
        fixed_layout.setSpacing(10)
        fixed_layout.addWidget(_label("Use the same date and time for each selected field.", wrap=True))
        self.fixed_datetime = QDateTimeEdit(QDateTime.currentDateTime())
        self.fixed_datetime.setDisplayFormat("yyyy-MM-dd  HH:mm:ss")
        self.fixed_datetime.setCalendarPopup(True)
        self.fixed_datetime.setAccessibleName("Exact date and time")
        self.fixed_datetime.dateTimeChanged.connect(self._invalidate_plan)
        fixed_layout.addWidget(self.fixed_datetime)
        fixed_layout.addStretch()
        self.mode_stack.addWidget(fixed)
        layout.addWidget(self.mode_stack)
        self.shift_mode.toggled.connect(self._mode_changed)
        layout.addWidget(_label("Time zone"))
        self.timezone = QComboBox()
        self.timezone.addItem("Local time on this computer", "local")
        self.timezone.addItem("UTC", "UTC")
        self.timezone.currentIndexChanged.connect(self._invalidate_plan)
        layout.addWidget(self.timezone)
        layout.addWidget(_label("Calendar years and months, then days and time.\nDates clamp to the last day of the target month.", wrap=True))
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #e2e9e1;")
        layout.addWidget(divider)
        layout.addWidget(_label("2   Select date fields", "section"))
        self.modified = QCheckBox("File modified")
        self.modified.setChecked(True)
        self.created = QCheckBox("File created")
        self.captured = QCheckBox("Embedded capture dates")
        for checkbox in (self.modified, self.created, self.captured):
            checkbox.stateChanged.connect(self._invalidate_plan)
        layout.addWidget(self.modified)
        layout.addWidget(_label("Works for every file format, including AVI video.", wrap=True))
        layout.addWidget(self.created)
        layout.addWidget(_label("Windows creation time. Not available on Linux.", wrap=True))
        if sys.platform != "win32":
            self.created.setEnabled(False)
            self.created.setToolTip("Changing creation time is available on Windows only.")
        layout.addWidget(self.captured)
        layout.addWidget(_label("Photo and video metadata. Requires ExifTool; support depends on the format and existing tags.", wrap=True))
        layout.addStretch()
        layout.addWidget(_button("How dates and undo work", self._show_help, "textButton"))
        return frame

    def _build_sources(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(_label("3   Add your files", "section"))
        top.addStretch()
        self.add_files_button = _button("+ Files", self._add_files)
        self.add_folder_button = _button("+ Folder", self._add_folder)
        top.addWidget(self.add_files_button)
        top.addWidget(self.add_folder_button)
        layout.addLayout(top)
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.source_list.setMinimumHeight(76)
        self.source_list.setMaximumHeight(96)
        self.source_list.setAccessibleName("Files and folders to process")
        layout.addWidget(self.source_list)
        self.drop_hint = _label("Drop images, videos, files or folders anywhere in this window.", "dropHint")
        layout.addWidget(self.drop_hint)
        bottom = QHBoxLayout()
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.stateChanged.connect(self._invalidate_plan)
        bottom.addWidget(self.recursive)
        bottom.addStretch()
        self.remove_button = _button("Remove selected", self._remove_selected, "textButton")
        self.clear_button = _button("Clear", self._clear_sources, "textButton")
        bottom.addWidget(self.remove_button)
        bottom.addSpacing(10)
        bottom.addWidget(self.clear_button)
        layout.addLayout(bottom)
        self.source_controls = [self.add_files_button, self.add_folder_button, self.source_list,
                                self.recursive, self.remove_button, self.clear_button]
        return card

    def _build_preview(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 16, 0, 12)
        layout.setSpacing(12)
        top = QHBoxLayout()
        top.setContentsMargins(18, 0, 18, 0)
        top.addWidget(_label("Change preview", "section"))
        top.addStretch()
        self.preview_count = _label("NO CHANGES APPLIED", "eyebrow")
        top.addWidget(self.preview_count)
        layout.addLayout(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["FILE", "DATE FIELD", "CURRENT", "PROPOSED", "STATUS"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setAccessibleName("Preview of date changes")
        for column, width in enumerate((160, 124, 175, 175, 94)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(54)
        self.preview_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        empty_icon = _label("↔", "hero")
        empty_icon.setStyleSheet("color: #9ab5a8; font-size: 44px;")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_title = _label("See the change before you make it.", "section")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        self.empty_hint = _label("Add your files, choose an adjustment, then preview.")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_hint)
        empty_layout.addStretch()
        self.preview_stack.addWidget(empty)
        self.preview_stack.addWidget(self.table)
        layout.addWidget(self.preview_stack, 1)
        history = QHBoxLayout()
        history.setContentsMargins(18, 0, 18, 0)
        self.undo_button = _button("Undo latest", self._undo_latest, "textButton")
        self.choose_undo_button = _button("Choose undo journal…", self._choose_undo, "textButton")
        self.history_button = _button("Open history folder", self._open_history, "textButton")
        history.addWidget(self.undo_button)
        history.addSpacing(12)
        history.addWidget(self.choose_undo_button)
        history.addStretch()
        history.addWidget(self.history_button)
        layout.addLayout(history)
        return card

    def _request(self) -> Request:
        targets = tuple(name for name, check in (("modified", self.modified), ("created", self.created),
                                                ("captured", self.captured)) if check.isChecked())
        if not targets:
            raise ValueError("Choose at least one date field to change.")
        zone = self.timezone.currentData()
        if self.shift_mode.isChecked():
            values = {name: spin.value() for name, spin in self.offset_inputs.items()}
            if not any(values.values()):
                raise ValueError("Enter an amount to shift, or choose Set exact time.")
            return Request(offset=Offset(**values), targets=targets, timezone=zone)
        selected = self.fixed_datetime.dateTime()
        date, time = selected.date(), selected.time()
        fixed = datetime(date.year(), date.month(), date.day(), time.hour(), time.minute(), time.second(),
                         tzinfo=timezone.utc if zone == "UTC" else None)
        return Request(fixed=fixed, targets=targets, timezone=zone)

    def _mode_changed(self, shift: bool) -> None:
        self.mode_stack.setCurrentIndex(0 if shift else 1)
        self._invalidate_plan()

    def _set_preset(self) -> None:
        for name, spin in self.offset_inputs.items():
            spin.blockSignals(True)
            spin.setValue({"years": 2, "days": 16, "hours": 6}.get(name, 0))
            spin.blockSignals(False)
        self.shift_mode.setChecked(True)
        self._invalidate_plan()

    def _invalidate_plan(self, *_args) -> None:
        self.plan = None
        self.table.setRowCount(0)
        self._row_paths = []
        self.preview_stack.setCurrentIndex(0)
        self.preview_count.setText("NO CHANGES APPLIED")
        self.empty_hint.setText("Preview again to see your updated choices." if self.sources else
                                "Add your files, choose an adjustment, then preview.")
        self.summary.setText("Ready to preview your changes." if self.sources else "Add files or folders to get started.")
        self.detail.setText("Original dates are saved to a local undo journal when you apply changes.")
        self._refresh_buttons()

    def add_paths(self, paths: list[str | Path]) -> None:
        """Add source paths without reading or modifying their contents."""
        if self._busy:
            return
        known = {os.path.normcase(str(path)) for path in self.sources}
        for raw in paths:
            # Preserve links so the core can identify and reject unsafe paths.
            path = Path(os.path.abspath(Path(raw).expanduser()))
            key = os.path.normcase(str(path))
            if key in known:
                continue
            known.add(key)
            self.sources.append(path)
            item = QListWidgetItem(f"{'Folder' if path.is_dir() else 'File'}   ·   {path.name}")
            item.setToolTip(str(path))
            self.source_list.addItem(item)
        self.drop_hint.setText(f"{len(self.sources)} source{'s' if len(self.sources) != 1 else ''} added · Drop more files or folders here.")
        self._invalidate_plan()

    def _add_files(self) -> None:
        if not self._busy:
            paths, _ = QFileDialog.getOpenFileNames(self, "Add files", "", "All files (*)")
            if paths:
                self.add_paths(paths)

    def _add_folder(self) -> None:
        if not self._busy:
            path = QFileDialog.getExistingDirectory(self, "Add a folder")
            if path:
                self.add_paths([path])

    def _remove_selected(self) -> None:
        if self._busy:
            return
        for row in sorted((self.source_list.row(item) for item in self.source_list.selectedItems()), reverse=True):
            self.sources.pop(row)
            self.source_list.takeItem(row)
        self.drop_hint.setText(f"{len(self.sources)} sources added · Drop more files or folders here." if self.sources else
                               "Drop images, videos, files or folders anywhere in this window.")
        self._invalidate_plan()

    def _clear_sources(self) -> None:
        if self._busy:
            return
        self.sources.clear()
        self.source_list.clear()
        self.drop_hint.setText("Drop images, videos, files or folders anywhere in this window.")
        self._invalidate_plan()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if not self._busy and event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if not self._busy:
            self.add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def _preview(self) -> None:
        if self._busy or not self.sources:
            return
        try:
            request = self._request()
        except (ValueError, TypeError) as exc:
            QMessageBox.information(self, "Choose your adjustment", str(exc))
            return
        paths, recursive = list(self.sources), self.recursive.isChecked()
        self.plan = None
        def build_plan(progress):
            progress(0, 0, "Finding files and reading their dates…")
            files = discover(paths, recursive=recursive)
            return preview(files, request)
        self._start("preview", build_plan)

    def _apply(self) -> None:
        if self._busy or self.plan is None or not self._applicable_count():
            return
        plan = self.plan
        self.plan = None
        self._start("apply", lambda progress: apply(plan, progress=progress))

    def _start(self, operation: str, action: Callable) -> None:
        self._operation = operation
        self._busy = True
        self.editor.setEnabled(False)
        for control in self.source_controls:
            control.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.summary.setText({"preview": "Reading files and building your preview…", "apply": "Applying date changes…",
                              "undo": "Restoring original dates…"}[operation])
        self.detail.setText("Please keep DateFix open until this operation finishes.")
        self._refresh_buttons()
        self._thread = QThread(self)
        self._worker = _Worker(action)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_stopped)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @Slot(int, int, str)
    def _on_progress(self, current: int, total: int, message: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.detail.setText(message)
        self.detail.setToolTip(message)

    @Slot(object)
    def _on_success(self, result) -> None:
        if self._operation == "preview":
            self._display_plan(result)
        else:
            self._display_result(result)

    @Slot(str)
    def _on_failure(self, message: str) -> None:
        self.plan = None
        self.summary.setText("The operation could not be completed.")
        self.detail.setText(message)
        self.detail.setToolTip(message)
        QMessageBox.warning(self, "DateFix", message)

    @Slot()
    def _on_stopped(self) -> None:
        self._busy = False
        self._thread = None
        self._worker = None
        self.progress.hide()
        self.editor.setEnabled(True)
        for control in self.source_controls:
            control.setEnabled(True)
        self._refresh_history()
        self._refresh_buttons()

    def _display_plan(self, plan) -> None:
        self.plan = plan
        self.table.setRowCount(0)
        self._row_paths = []
        warning_files = sum(bool(file.warnings) for file in plan.files)
        skipped = sum(bool(file.errors) or not file.changes for file in plan.files)
        fields = {"modified": "File modified", "created": "File created", "captured": "Capture date"}
        for file in plan.files:
            status = "Cannot change" if file.errors else ("Review warning" if file.warnings else "Ready")
            if not file.errors and not file.changes:
                status = "No changes"
            notes = "\n".join([*file.errors, *file.warnings])
            for change in file.changes or [None]:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._row_paths.append(str(file.path))
                field = "Capture: " + change.field.split(":", 1)[1] if change and change.field.startswith("captured:") else fields.get(change.field, change.field) if change else "—"
                values = [Path(file.path).name, field,
                          change.before if change else "—", change.after if change else "—", status]
                for col, value in enumerate(values):
                    rendered = str(value)
                    if col in (2, 3) and len(rendered) > 10 and rendered[10] in ("T", " "):
                        rendered = rendered[:10] + "\n" + rendered[11:]
                    item = QTableWidgetItem(rendered)
                    item.setToolTip(str(file.path) if col == 0 else notes if col == 4 and notes else str(value))
                    if col == 4:
                        item.setForeground(QColor("#ae5e2a" if file.errors or file.warnings else "#27836d"))
                    self.table.setItem(row, col, item)
        self.preview_stack.setCurrentIndex(1 if plan.files else 0)
        if not plan.files:
            self.empty_hint.setText("No files were found. Add files or include subfolders.")
        available = self._applicable_count()
        self.preview_count.setText(f"{len(plan.files)} FILES · PREVIEW ONLY")
        self.summary.setText(f"{available} file{'s' if available != 1 else ''} ready · {skipped} skipped · {warning_files} with warnings")
        self.detail.setText("Review the proposed dates. Hover over a status for details." if available else
                            "No changes can be applied. Review each file's status or adjust your choices.")
        self._refresh_buttons()

    def _display_result(self, result) -> None:
        self.plan = None
        counts = Counter(file.status for file in result.files)
        successes = counts["undone"] if self._operation == "undo" else counts["applied"]
        action = "restored" if self._operation == "undo" else "updated"
        self.summary.setText(f"{successes} files {action} · {counts['skipped']} skipped · {counts['error']} errors")
        if result.journal_path:
            self._latest_journal = Path(result.journal_path)
            self.detail.setText("Original dates saved. You can restore them using Undo latest." if self._operation == "apply" else
                                "Undo finished. Preview again before making further changes.")
            self.detail.setToolTip(str(result.journal_path))
        else:
            self.detail.setText("Preview again before making further changes.")
        by_path = {str(file.path): file for file in result.files}
        if self._operation == "undo":
            self.table.setRowCount(0)
            self._row_paths = []
            for file in result.files:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._row_paths.append(str(file.path))
                for col, value in enumerate((Path(file.path).name, "Original dates", "—", "—", file.status.capitalize())):
                    item = QTableWidgetItem(value)
                    item.setToolTip(str(file.path) if col == 0 else file.message)
                    self.table.setItem(row, col, item)
        for row, path in enumerate(self._row_paths):
            if path in by_path:
                file = by_path[path]
                item = self.table.item(row, 4)
                item.setText(file.status.capitalize())
                item.setToolTip(file.message)
                item.setForeground(QColor("#ae5e2a" if file.status == "error" else "#27836d"))
        self.preview_stack.setCurrentIndex(1 if self.table.rowCount() else 0)
        self.preview_count.setText("UNDO RESULTS" if self._operation == "undo" else
                                   "CHANGES APPLIED" if successes else "NO CHANGES APPLIED")

    def _applicable_count(self) -> int:
        return sum(bool(file.changes) and not file.errors for file in self.plan.files) if self.plan else 0

    def _refresh_buttons(self) -> None:
        self.preview_button.setEnabled(bool(self.sources) and not self._busy)
        self.apply_button.setEnabled(bool(self._applicable_count()) and not self._busy)
        self.undo_button.setEnabled(self._latest_journal is not None and not self._busy)
        self.choose_undo_button.setEnabled(not self._busy)
        self.history_button.setEnabled(not self._busy)

    def _refresh_history(self) -> None:
        try:
            journals = sorted(default_journal_dir().glob("*/journal.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            if journals:
                self._latest_journal = journals[0]
        except OSError:
            pass
        if self._latest_journal:
            self.undo_button.setToolTip(str(self._latest_journal))

    def _undo_latest(self) -> None:
        if self._latest_journal and not self._busy:
            self._undo_path(self._latest_journal)

    def _choose_undo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an undo journal", str(default_journal_dir()), "DateFix journals (*.json)")
        if path:
            self._undo_path(Path(path))

    def _undo_path(self, path: Path) -> None:
        if self._busy:
            return
        self.plan = None
        self._start("undo", lambda progress: undo(path, progress=progress))

    def _open_history(self) -> None:
        try:
            directory = default_journal_dir()
            directory.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                QMessageBox.information(self, "History folder", str(directory))
        except OSError as exc:
            QMessageBox.warning(self, "Could not open history", str(exc))

    def _show_help(self) -> None:
        QMessageBox.information(self, "How DateFix handles dates", (
            "File modified and File created are filesystem dates. Embedded capture dates are metadata inside a photo or video; galleries often sort by these.\n\n"
            "The same shift is applied to each existing date. Calendar years and months are combined first, then days, hours, minutes and seconds are added. If the day does not exist in the target month, its last day is used: January 31 plus one month becomes February 28 (or 29 in a leap year).\n\n"
            "Local time uses this computer's time zone; UTC uses universal time. Capture tags without time-zone information are treated as wall-clock dates. Preview the results, especially around daylight-saving changes.\n\n"
            "ExifTool is required for embedded metadata. Support depends on the format and tags already present. A file with an unsupported selected field is skipped; review its status for details.\n\n"
            "An undo journal is saved locally when you apply changes. Keep it to restore original dates. Undo checks for later changes and may refuse to overwrite a changed file."
        ))

    def closeEvent(self, event) -> None:
        if self._busy:
            self.summary.setText("Please wait for the current operation to finish before closing.")
            event.ignore()
        else:
            event.accept()


def main(startup_report: Path | None = None) -> int:
    application = QApplication.instance() or QApplication([sys.argv[0]])
    application.setApplicationName("DateFix")
    application.setOrganizationName("DateFix")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    window = MainWindow()
    if startup_report is not None:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    if startup_report is not None:
        def report_ready():
            import json
            startup_report.write_text(json.dumps({"ready": window.isVisible(), "platform": application.platformName()}), encoding="utf-8")
            application.quit()
        QTimer.singleShot(0, report_ready)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
