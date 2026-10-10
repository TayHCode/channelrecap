import helpers
from pathlib import Path
import tempfile
import unittest
from channelrecap.captions import read_captions
from channelrecap.errors import RowError


class CaptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_vtt(self) -> None:
        path = self.root / 'sample.vtt'
        path.write_text('WEBVTT\nmetadata\n\nNOTE ignored\n00:00 --> 00:01\nwrong\n\nSTYLE\n::cue {}\n\nREGION\nregion\n\nfirst\n00:01.000 --> 00:03.000 align:start\n<v speaker>Tea &amp; <b>coffee</b> &lt;cup&gt;</v>\n\n00:03 --> 00:04\nTea &amp; coffee &lt;cup&gt;\n\n00:04 --> 00:06\nNext&nbsp;line.\n', encoding='utf-8')
        cues = read_captions(path)
        self.assertEqual([c.text for c in cues], ['Tea & coffee <cup>', 'Next line.'])
        self.assertEqual([c.start for c in read_captions(path, start=4, end=5)], [4])

    def test_srt_and_empty(self) -> None:
        path = self.root / 'sample.srt'
        path.write_text('1\n00:00:01,500 --> 00:00:03,000\nTwo lines\nof speech.\n', encoding='utf-8')
        self.assertEqual(read_captions(path)[0].start, 1.5)
        self.assertEqual(read_captions(path)[0].text, 'Two lines of speech.')
        path.write_text('', encoding='utf-8')
        with self.assertRaisesRegex(RowError, 'No captions found'):
            read_captions(path)

    def test_header_without_blank_line_and_position_tags(self) -> None:
        path = self.root / 'sample.vtt'
        path.write_text('WEBVTT\n00:00:01.000 --> 00:00:03.000\n' + r'{\an8}' + 'A paper filter holds coffee.\n', encoding='utf-8')
        cue = read_captions(path)[0]
        self.assertEqual((cue.start, cue.text), (1, 'A paper filter holds coffee.'))

    def test_unicode_captions_with_byte_order_mark(self) -> None:
        path = self.root / 'sample.srt'
        text = '1\r\n00:00:01,000 --> 00:00:03,000\r\nCafé water tastes good.\r\n'
        for encoding in ('utf_16', 'utf-8-sig'):
            with self.subTest(encoding=encoding):
                path.write_bytes(text.encode(encoding))
                self.assertEqual(read_captions(path)[0].text, 'Café water tastes good.')

    def test_unpaired_surrogates_are_invalid_caption_encoding(self) -> None:
        path = self.root / 'invalid.vtt'
        text = 'WEBVTT\n\n00:00 --> 00:03\nA cup \ud800 holds water.\n'
        for encoding in ('utf-8', 'utf_16'):
            with self.subTest(encoding=encoding):
                path.write_bytes(text.encode(encoding, errors='surrogatepass'))
                with self.assertRaisesRegex(RowError, 'Cannot read captions'):
                    read_captions(path)
