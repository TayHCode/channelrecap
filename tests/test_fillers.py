import helpers
# Manually checked against every demo cue:
# Words: 397, 408, 426. Clear totals: 4, 3, 3. All totals: 7, 5, 5.
# Each episode has one and so, like, sentence-initial so, um, and you know.
# Episode one also has one actually and one longest-match you know what i mean.
# Busiest minute: zero in all episodes, with 3, 4, 4 matches (earliest tie wins).
from pathlib import Path
import csv
import tempfile
import unittest
from channelrecap.errors import InputError
from channelrecap.fillers import parse_entries, load_entries, count_fillers
from channelrecap.manifest import read_manifest
from channelrecap.pipeline import RunOptions, transcribe, finished
from channelrecap.summary import prepare
from channelrecap.transcript import Transcript, Segment


class FillerTests(unittest.TestCase):
    def test_titles_are_text_when_csv_is_opened_in_a_spreadsheet(self) -> None:
        from channelrecap.fillers import write_csv
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transcript = Transcript(segments=[Segment(0, 1, 'Um, coffee tastes good.')])
            entries = load_entries()
            for title in ('=1+1', '+1', '-1', '@SUM(1)', '  =1', '\tplain', 'Plain title'):
                with self.subTest(title=title):
                    entry = dict(video_id='talk', date='', title=title)
                    write_csv(root, [(entry, transcript)], entries, [count_fillers(transcript, entries)])
                    with (root / 'fillers.csv').open(newline='') as stream:
                        rows = list(csv.DictReader(stream))
                    expected = title if title == 'Plain title' else "'" + title
                    self.assertTrue(all(row['title'] == expected for row in rows))
                    self.assertEqual(entry['title'], title)

    def test_entries_and_duplicates(self) -> None:
        entries = parse_entries('# comment\n ^ ~ SO # note\num\n')
        entry = next(e for e in entries if e.text == 'so')
        self.assertTrue(entry.ambiguous and entry.sentence_start_only)
        for text in ['', '# none', 'um\nUM', '~like\nlike', '^~']:
            with self.assertRaises(InputError):
                parse_entries(text)

    def test_longest_sentence_flags_and_buckets(self) -> None:
        transcript = Transcript(duration=120, segments=[Segment(0,10,'So yeah. I say so. You know what I mean.'), Segment(60,70,'Um, like, you know. So this is so.')])
        stats = count_fillers(transcript,load_entries())
        self.assertEqual(stats.counts['so yeah'],1)
        self.assertEqual(stats.counts['you know what i mean'],1)
        self.assertEqual(stats.counts['you know'],1)
        self.assertEqual(stats.counts['i mean'],0)
        self.assertEqual(stats.counts['so'],1)
        self.assertEqual((stats.clear,stats.all),(4,6))
        self.assertEqual(stats.busiest,'00:01:00 (4)')
        self.assertEqual(f'{stats.clear_rate:.1f}',f'{4000/stats.words:.1f}')
        across = count_fillers(Transcript(segments=[Segment(0,1,'You. Know.')]),parse_entries('you know'))
        self.assertEqual(across.clear,1)

    def test_empty_and_custom_replaces_default(self) -> None:
        empty = count_fillers(Transcript(),load_entries())
        self.assertEqual((empty.words, empty.clear_rate, empty.all_rate, empty.words_per_minute, empty.busiest),(0,0,0,0,''))
        custom = count_fillers(Transcript(segments=[Segment(0,1,'Um coffee coffee.')]),parse_entries('coffee'))
        self.assertEqual(custom.counts,{'coffee':2})

    def test_demo_exact_values_and_csv(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transcribe(read_manifest(helpers.ROOT / 'examples/demo/videos.csv'),root,RunOptions())
            items = finished(root)[1]
            stats = [count_fillers(t,load_entries()) for _,t in items]
            self.assertEqual([s.words for s in stats],[397,408,426])
            self.assertEqual([s.clear for s in stats],[4,3,3])
            self.assertEqual([s.all for s in stats],[7,5,5])
            self.assertEqual([s.busiest for s in stats],['00:00:00 (3)','00:00:00 (4)','00:00:00 (4)'])
            prepare(root)
            self.assertEqual((root / 'fillers.csv').read_bytes(),(helpers.ROOT / 'tests/fixtures/expected_fillers.csv').read_bytes())
            custom = root / 'custom.txt'
            custom.write_text('coffee\n', encoding='utf-8')
            prepare(root,custom)
            self.assertNotIn(',um,',(root / 'fillers.csv').read_text(encoding='utf-8'))
