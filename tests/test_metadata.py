from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from datefix import metadata
from datefix.core import Offset, Request, _new_capture


class MetadataTests(unittest.TestCase):
    def test_parse_naive_and_zoned_dates(self):
        self.assertIsNone(metadata.parse_date("2024:02:29 10:20:30").tzinfo)
        self.assertEqual(metadata.parse_date("2024:02:29 10:20:30.123+02:00").utcoffset(), timedelta(hours=2))
        self.assertEqual(metadata.parse_date("2024-02-29T10:20:30Z").tzinfo, timezone.utc)
        with self.assertRaises(metadata.MetadataError):
            metadata.parse_date("0000:00:00 00:00:00")

    def test_shift_retains_precision_and_offset(self):
        before = "2024:02:29 10:20:30.123456789+02:00"
        after = _new_capture(before, Request(offset=Offset(years=2, days=16, hours=6)))
        self.assertEqual(after, "2026:03:16 16:20:30.123456789+02:00")

    def test_fixed_aware_time_converts_to_existing_offset(self):
        after = _new_capture("2020:01:01 10:00:00+02:00", Request(
            fixed=datetime(2025, 3, 4, 12, tzinfo=timezone.utc)))
        self.assertEqual(after, "2025:03:04 14:00:00+02:00")

    def test_quicktime_fixed_local_time_uses_destination_dst_offset(self):
        fixed = datetime(2026, 7, 1, 12)
        after = _new_capture("2024:01:01 12:00:00+01:00", Request(fixed=fixed), "QuickTime:CreateDate")
        self.assertEqual(metadata.parse_date(after), fixed.astimezone(timezone.utc))

    def test_quicktime_offset_uses_calendar_time_in_selected_zone(self):
        source = datetime(2024, 1, 1, 12).astimezone(timezone.utc)
        before = metadata.format_date(source, "2024:01:01 00:00:00+00:00")
        after = _new_capture(before, Request(offset=Offset(days=182)), "Track1:MediaCreateDate")
        self.assertEqual(metadata.parse_date(after), datetime(2024, 7, 1, 12).astimezone(timezone.utc))

    def test_exif_companion_offset_is_used_for_fixed_utc_date(self):
        record = {"ExifIFD:DateTimeOriginal": "2024:01:01 12:00:00", "ExifIFD:OffsetTimeOriginal": "+02:00"}
        with patch.object(metadata, "_run", return_value=json.dumps([record])):
            result = metadata.read_dates("sample.jpg", "tool")
        before = result.dates["ExifIFD:DateTimeOriginal"]
        self.assertEqual(before, "2024:01:01 12:00:00+02:00")
        after = _new_capture(before, Request(fixed=datetime(2026, 7, 1, 12, tzinfo=timezone.utc)), "ExifIFD:DateTimeOriginal")
        self.assertEqual(after, "2026:07:01 14:00:00+02:00")

    def test_repeated_capture_instances_are_refused(self):
        record = {"ExifIFD:DateTimeOriginal": "2024:01:01 12:00:00", "ExifIFD:Copy1:DateTimeOriginal": "2024:01:01 13:00:00"}
        with patch.object(metadata, "_run", return_value=json.dumps([record])):
            with self.assertRaisesRegex(metadata.MetadataError, "Duplicate instances"):
                metadata.read_dates("sample.jpg", "tool")

    def test_copy_identifiers_in_different_groups_are_unambiguous(self):
        record = {"ExifIFD:CreateDate": "2024:01:01 12:00:00", "XMP-xmp:Copy1:CreateDate": "2024:01:01 13:00:00"}
        with patch.object(metadata, "_run", return_value=json.dumps([record])):
            result = metadata.read_dates("sample.jpg", "tool")
        self.assertEqual(set(result.dates), {"ExifIFD:CreateDate", "XMP-xmp:CreateDate"})

    def test_read_only_known_capture_tags(self):
        record = {
            "ExifIFD:DateTimeOriginal": "2020:01:01 10:00:00",
            "IFD0:ModifyDate": "2020:01:01 11:00:00",
            "File:FileModifyDate": "2020:01:01 12:00:00",
            "QuickTime:CreateDate": "0000:00:00 00:00:00",
            "Track1:MediaCreateDate": "2020:01:01 13:00:00+00:00",
        }
        with patch.object(metadata, "_run", return_value=json.dumps([record])):
            result = metadata.read_dates("sample.mp4", "exiftool")
        self.assertEqual(set(result.dates), {"ExifIFD:DateTimeOriginal", "Track1:MediaCreateDate"})
        self.assertTrue(any("unset" in message for message in result.warnings))
        self.assertTrue(any("UTC" in message for message in result.warnings))

    def test_unsupported_format_rejected_before_invoking_tool(self):
        with patch.object(metadata, "_run") as run:
            with self.assertRaises(metadata.MetadataError):
                metadata.write_dates("sample.avi", {"ExifIFD:DateTimeOriginal": "2024:01:01 00:00:00"}, "tool")
            run.assert_not_called()

    def test_unknown_or_injected_tag_rejected(self):
        with self.assertRaises(metadata.MetadataError):
            metadata.write_dates("sample.jpg", {"File:FileName": "2024:01:01 00:00:00"}, "tool")
        with self.assertRaises(metadata.MetadataError):
            metadata._run("tool", ["sample\n-execute.jpg"])

    def test_missing_tag_is_not_manufactured(self):
        with patch.object(metadata, "read_dates", return_value=metadata.CaptureMetadata()), patch.object(metadata, "_run") as run:
            with self.assertRaisesRegex(metadata.MetadataError, "no longer exists"):
                metadata.write_dates("sample.jpg", {"ExifIFD:DateTimeOriginal": "2024:01:01 00:00:00"}, "tool")
            run.assert_not_called()

    def test_write_must_be_verified(self):
        capture = metadata.CaptureMetadata(dates={"ExifIFD:DateTimeOriginal": "2020:01:01 00:00:00"})
        with patch.object(metadata, "read_dates", return_value=capture), patch.object(metadata, "_run"):
            with self.assertRaisesRegex(metadata.MetadataError, "did not save"):
                metadata.write_dates("sample.jpg", {"ExifIFD:DateTimeOriginal": "2024:01:01 00:00:00"}, "tool")


if __name__ == "__main__":
    unittest.main()
