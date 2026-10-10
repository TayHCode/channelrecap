from __future__ import annotations

import helpers
import tempfile
import unittest
from pathlib import Path

from channelrecap.errors import InputError
from channelrecap.timecodes import parse_time as parse_timestamp
from channelrecap.manifest import read_manifest, video_id


class TimestampTests(unittest.TestCase):
    def test_supported_timestamp_forms(self) -> None:
        self.assertEqual(parse_timestamp("90"), 90)
        self.assertEqual(parse_timestamp("01:30"), 90)
        self.assertEqual(parse_timestamp("01:02:03.5"), 3723.5)

    def test_rejects_invalid_timestamp(self) -> None:
        """D3/D12: invalid times use the public input-error type."""
        with self.assertRaises(InputError):
            parse_timestamp("one minute")


class ManifestTests(unittest.TestCase):
    def test_reads_video(self) -> None:
        """D2/D4: title plus source-range key, with legacy columns ignored."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "videos.csv"
            path.write_text(
                "url,start,end,date,title,speaker,series,language\n"
                "https://example.com/video,10:00,45:00,2026-01-01,Title,Speaker,Series,en\n",
                encoding="utf-8",
            )
            entries = read_manifest(path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].start, 600)
        self.assertEqual(entries[0].end - entries[0].start, 2100)
        self.assertTrue(video_id(entries[0].title, entries[0].key).startswith("title-"))


if __name__ == "__main__":
    unittest.main()
