import helpers
from pathlib import Path
import contextlib
import io
import tempfile
import unittest
from channelrecap.errors import InputError
from channelrecap.manifest import read_manifest, read_sources, source_key, video_id


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'talk.vtt').write_text('WEBVTT', encoding='utf-8')
        (self.root / 'talk.wav').write_text('placeholder', encoding='utf-8')

    def read(self, content: str):
        path = self.root / 'list.csv'
        path.write_text(content, encoding='utf-8-sig')
        return read_manifest(path)

    def test_bom_blanks_relative_and_kinds(self) -> None:
        rows = self.read('source\ntalk.vtt\n\ntalk.wav\nhttps://example.com/watch?v=sample\n')
        self.assertEqual([r.kind for r in rows], ['captions','media','link'])
        self.assertEqual(rows[0].path, self.root / 'talk.vtt')
        self.assertEqual(rows[0].source, 'talk.vtt')

    def test_errors_together(self) -> None:
        with self.assertRaises(InputError) as caught:
            self.read('source,start,end,date\nmissing.wav,bad,4,2026-99-99\ntalk.vtt,5,4,\ntalk.vtt,5,4,\n')
        message = str(caught.exception)
        for fragment in ['row 2', 'row 3', 'row 4', 'start:', 'date', 'missing local', 'end must', 'duplicate']:
            self.assertIn(fragment, message)

    def test_headers_empty_and_unknown(self) -> None:
        for text in ['title\nTalk\n', 'source\n\n']:
            with self.assertRaises(InputError):
                self.read(text)
        with contextlib.redirect_stdout(io.StringIO()) as err:
            self.read('source,extra\ntalk.vtt,value\n')
        self.assertIn('unknown columns', err.getvalue())

    def test_key_and_title_stability(self) -> None:
        a = self.read('source,title\ntalk.vtt,First\n')[0]
        b = self.read('source,title\ntalk.vtt,Second\n')[0]
        self.assertEqual(a.key, b.key)
        self.assertEqual(source_key('x', 1, 2), source_key('x', 1.0, 2.0))
        self.assertNotEqual(source_key('x', None, None), source_key('x', 0, None))
        self.assertEqual(video_id('A / New Title!', a.key), 'a-new-title-' + a.key)
        self.assertEqual(read_sources(['talk.vtt'], self.root)[0].kind, 'captions')

    def test_bad_csv_quotes_and_header_guidance(self) -> None:
        for content in ('Source\ntalk.vtt\n', ' source\ntalk.vtt\n', 'source;title\ntalk.vtt;Talk\n'):
            with self.subTest(content=content), self.assertRaisesRegex(InputError, 'lowercase source column in comma-separated CSV'):
                self.read(content)
        with self.assertRaisesRegex(InputError, 'unexpected end of data'):
            self.read('source,title\ntalk.vtt,"Unclosed title\n')

    def test_url_column_alias(self):
        rows = self.read('url,start,end\nhttps://example.com/a,2,9\n')
        self.assertEqual((rows[0].source, rows[0].start, rows[0].end), ('https://example.com/a',2,9))

    def test_both_source_columns_rejected(self):
        with self.assertRaisesRegex(InputError, 'both columns'):
            self.read('source,url\ntalk.vtt,https://example.com/a\n')

    def test_empty_source_is_skipped_even_with_other_fields(self):
        rows = self.read('source,title,start\n,Unused,2\ntalk.vtt,Talk,\n')
        self.assertEqual(len(rows), 1)
