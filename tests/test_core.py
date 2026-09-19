"""Data-integrity tests, using temporary files only."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from datefix import core, metadata
from datefix.core import Offset, Request, apply, discover, plan_from_dict, plan_to_dict, preview, undo


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.file = self.root / "sample.avi"
        self.file.write_bytes(b"untouched movie payload\x00\xff")
        self.original_ns = 1709164800_123456700  # 2024-02-29 UTC, subsecond precision.
        os.utime(self.file, ns=(self.original_ns, self.original_ns))
        self.history = self.root / "history"

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, **offset):
        return Request(offset=Offset(**offset), timezone="UTC")

    def test_calendar_years_then_days_hours_preserves_subseconds(self):
        plan = preview([self.file], self.request(years=2, days=16, hours=6))
        expected = datetime(2026, 3, 16, 6, tzinfo=timezone.utc)
        self.assertEqual(plan.files[0].operations["modified"], core._to_ns(expected, "UTC") + 123456700)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)

    def test_apply_undo_preserves_payload_and_creation(self):
        original = self.file.read_bytes()
        created = core._created_ns(self.file.stat())
        plan = preview([self.file], self.request(years=2, days=16, hours=6))
        result = apply(plan, self.history)
        self.assertEqual(result.files[0].status, "applied", result.files[0].message)
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(core._created_ns(self.file.stat()), created)
        self.assertEqual(self.file.stat().st_mtime_ns, plan.files[0].operations["modified"])
        undone = undo(result.journal_path)
        self.assertEqual(undone.files[0].status, "undone", undone.files[0].message)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(undo(result.journal_path).files[0].status, "skipped")

    def test_stale_preview_refused_and_other_files_continue(self):
        second = self.root / "other.avi"
        second.write_bytes(b"second")
        plan = preview([self.file, second], self.request(days=1))
        self.file.write_bytes(b"new content")
        result = apply(plan, self.history)
        self.assertEqual([item.status for item in result.files], ["error", "applied"])
        self.assertIn("since preview", result.files[0].message)
        self.assertEqual(self.file.read_bytes(), b"new content")

    def test_stale_undo_refused(self):
        result = apply(preview([self.file], self.request(hours=6)), self.history)
        self.file.write_bytes(b"edited after application")
        undone = undo(result.journal_path)
        self.assertEqual(undone.files[0].status, "error")
        self.assertIn("protect newer changes", undone.files[0].message)
        self.assertEqual(self.file.read_bytes(), b"edited after application")

    def test_filesystem_preview_never_hashes_large_media(self):
        with patch.object(core, "_hash", side_effect=AssertionError("Must not hash")):
            plan = preview([self.file], self.request(days=1))
            result = apply(plan, self.history)
            self.assertEqual(result.files[0].status, "applied")
            self.assertEqual(undo(result.journal_path).files[0].status, "undone")

    def test_json_roundtrip_and_invalid_plans(self):
        plan = preview([self.file], self.request(days=-16, hours=-6))
        restored = plan_from_dict(json.loads(json.dumps(plan_to_dict(plan))))
        self.assertEqual(restored, plan)
        payload = plan_to_dict(plan)
        payload["files"][0]["operations"]["arbitrary"] = "bad"
        with self.assertRaises(ValueError):
            plan_from_dict(payload)
        with self.assertRaises(ValueError):
            Request()
        with self.assertRaises(ValueError):
            Request(offset=Offset(), fixed=datetime.now())
        with self.assertRaises(ValueError):
            Offset(days=1.5)

    def test_saved_plan_does_not_trust_serialized_executable(self):
        payload = plan_to_dict(preview([self.file], self.request(days=1)))
        payload["exiftool"] = "untrusted-program.exe"
        restored = plan_from_dict(payload)
        self.assertIsNone(restored.exiftool)

    def test_tampered_plan_operations_and_display_are_refused(self):
        plan = preview([self.file], self.request(days=1))
        plan.files[0].operations["modified"] += 3600 * 1_000_000_000
        with self.assertRaisesRegex(ValueError, "do not match"):
            plan_from_dict(plan_to_dict(plan))
        result = apply(plan, self.history)
        self.assertEqual(result.files[0].status, "error")
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)
        plan = preview([self.file], self.request(days=1))
        plan.files[0].changes[0].after = "a misleading date"
        with self.assertRaisesRegex(ValueError, "do not match"):
            plan_from_dict(plan_to_dict(plan))

    def test_linked_journal_directory_is_refused_before_edit(self):
        actual = self.root / "actual-history"
        actual.mkdir()
        linked = self.root / "linked-history"
        try:
            linked.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "history directory"):
            apply(preview([self.file], self.request(days=1)), linked)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)

    def test_unsupported_capture_prevents_partial_edit_of_that_file(self):
        plan = preview([self.file], Request(offset=Offset(days=1), targets=("modified", "captured")))
        self.assertIn("not supported", plan.files[0].errors[0])
        result = apply(plan, self.history)
        self.assertEqual(result.files[0].status, "error")
        self.assertIsNone(result.journal_path)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)

    def test_zero_offset_no_journal_and_duplicates_removed(self):
        plan = preview([self.file, self.file], self.request())
        self.assertEqual(len(plan.files), 1)
        result = apply(plan, self.history)
        self.assertIsNone(result.journal_path)
        self.assertEqual(result.files[0].status, "skipped")

    def test_discover_folders_and_missing_paths(self):
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "photo.jpg").write_bytes(b"jpg")
        self.assertEqual(discover([self.root]), [self.file])
        self.assertEqual(set(discover([self.root], recursive=True)), {self.file, nested / "photo.jpg"})
        missing = self.root / "missing.avi"
        self.assertEqual(discover([missing]), [missing])
        self.assertTrue(preview([missing], self.request(days=1)).files[0].errors)

    def test_hardlinks_refused(self):
        linked = self.root / "linked.avi"
        try:
            os.link(self.file, linked)
        except OSError:
            self.skipTest("Hard links unavailable")
        plan = preview([linked], self.request(days=1))
        self.assertIn("Hard-linked", plan.files[0].errors[0])

    def test_symlinks_not_followed(self):
        linked = self.root / "linked.avi"
        try:
            linked.symlink_to(self.file)
        except OSError:
            self.skipTest("Symlinks require permissions on this host")
        self.assertNotIn(linked, discover([self.root]))
        self.assertIn("Symbolic links", preview([linked], self.request(days=1)).files[0].errors[0])

    def test_no_mutation_when_journal_cannot_be_written(self):
        plan = preview([self.file], self.request(days=1))
        original_save = core._save_json
        calls = 0

        def fail_prepared(path, data):
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise OSError("disk full")
            return original_save(path, data)

        with patch.object(core, "_save_json", side_effect=fail_prepared):
            result = apply(plan, self.history)
        self.assertEqual(result.files[0].status, "error")
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)

    def test_rollback_when_postwrite_journal_update_fails(self):
        plan = preview([self.file], self.request(days=1))
        original_save = core._save_json
        calls = 0

        def fail_once(path, data):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("journal unavailable")
            return original_save(path, data)

        with patch.object(core, "_save_json", side_effect=fail_once):
            result = apply(plan, self.history)
        self.assertEqual(result.files[0].status, "error")
        self.assertIn("restored", result.files[0].message)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)

    def test_progress_callback_failure_does_not_abort_transaction(self):
        def bad_progress(*_):
            raise RuntimeError("UI closed")
        result = apply(preview([self.file], self.request(days=1)), self.history, bad_progress)
        self.assertEqual(result.files[0].status, "applied")

    @unittest.skipUnless(os.name == "nt", "Windows creation date API")
    def test_created_date_changes_and_undo_leaves_modified_alone(self):
        before = core._created_ns(self.file.stat())
        plan = preview([self.file], Request(offset=Offset(days=-16, hours=-6), targets=("created",), timezone="UTC"))
        result = apply(plan, self.history)
        self.assertEqual(result.files[0].status, "applied", result.files[0].message)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)
        self.assertEqual(core._created_ns(self.file.stat()), plan.files[0].operations["created"])
        self.assertEqual(undo(result.journal_path).files[0].status, "undone")
        self.assertEqual(core._created_ns(self.file.stat()), before)

    @unittest.skipUnless(hasattr(__import__("time"), "tzset"), "POSIX local DST rules")
    def test_local_nonexistent_time_rejected_and_dst_day_is_calendar_day(self):
        import time
        before = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "EST5EDT,M3.2.0,M11.1.0"
            time.tzset()
            with self.assertRaisesRegex(ValueError, "does not exist"):
                core._to_ns(datetime(2024, 3, 10, 2, 30), "local")
            source = core._to_ns(datetime(2024, 3, 9, 12), "local")
            after = core._new_ns(source, Request(offset=Offset(days=1)))
            self.assertEqual(after - source, 23 * 3600 * 1_000_000_000)
        finally:
            if before is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = before
            time.tzset()


class MetadataTransactionTests(unittest.TestCase):
    """Use a fake engine to force write failures that real files rarely trigger."""
    def setUp(self):
        CoreTests.setUp(self)
        photo = self.root / "photo.jpg"
        self.file.rename(photo)
        self.file = photo
        self.read_patch = patch.object(metadata, "read_dates", return_value=metadata.CaptureMetadata(
            dates={"ExifIFD:DateTimeOriginal": "2024:02:29 10:20:30"}
        ))
        self.read_patch.start()

    def tearDown(self):
        self.read_patch.stop()
        CoreTests.tearDown(self)

    def capture_plan(self):
        return preview([self.file], Request(offset=Offset(years=2), targets=("captured",)), exiftool="fake")

    def test_capture_write_failure_restores_bytes_and_dates(self):
        original = self.file.read_bytes()
        def broken_write(path, *_):
            Path(path).write_bytes(b"partial corrupt write")
            raise metadata.MetadataError("injected write failure")
        with patch.object(metadata, "write_dates", side_effect=broken_write):
            result = apply(self.capture_plan(), self.history)
        self.assertEqual(result.files[0].status, "error")
        self.assertIn("restored", result.files[0].message)
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)

    def test_capture_backup_undo_preserves_unselected_filesystem_dates(self):
        original = self.file.read_bytes()
        def write(path, *_):
            Path(path).write_bytes(b"new embedded date; same media")
        plan = self.capture_plan()
        with patch.object(metadata, "write_dates", side_effect=write):
            result = apply(plan, self.history)
        self.assertEqual(result.files[0].status, "applied", result.files[0].message)
        self.assertEqual(self.file.stat().st_mtime_ns, self.original_ns)
        self.assertEqual(self.file.stat().st_atime_ns, plan.files[0].snapshot["atime_ns"])
        self.assertEqual(undo(result.journal_path).files[0].status, "undone")
        self.assertEqual(self.file.read_bytes(), original)

    def test_changed_content_with_restored_mtime_refuses_undo(self):
        with patch.object(metadata, "write_dates", side_effect=lambda path, *_: Path(path).write_bytes(b"new payload")):
            result = apply(self.capture_plan(), self.history)
        stamp = self.file.stat().st_mtime_ns
        self.file.write_bytes(b"bad payload")  # identical length
        os.utime(self.file, ns=(stamp, stamp))
        undone = undo(result.journal_path)
        self.assertEqual(undone.files[0].status, "error")
        self.assertEqual(self.file.read_bytes(), b"bad payload")

    def test_damaged_backup_refuses_undo(self):
        with patch.object(metadata, "write_dates", side_effect=lambda path, *_: Path(path).write_bytes(b"new payload")):
            result = apply(self.capture_plan(), self.history)
        journal = json.loads(Path(result.journal_path).read_text(encoding="utf-8"))
        backup = Path(result.journal_path).parent / journal["files"][0]["backup"]
        backup.write_bytes(b"damaged")
        undone = undo(result.journal_path)
        self.assertEqual(undone.files[0].status, "error")
        self.assertIn("damaged", undone.files[0].message)
        self.assertEqual(self.file.read_bytes(), b"new payload")


if __name__ == "__main__":
    unittest.main()
