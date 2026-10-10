import helpers
import tempfile
from pathlib import Path
import unittest
from channelrecap.manifest import read_manifest
from channelrecap.pipeline import transcribe, RunOptions, finished
from channelrecap.summary import prepare, compose_summary, cell
from channelrecap.transcript import Transcript


class SummaryTests(unittest.TestCase):
    def test_demo_sections_and_order(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transcribe(read_manifest(helpers.ROOT / 'examples/demo/videos.csv'),root,RunOptions())
            self.assertEqual(prepare(root), 3)
            text = (root / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('Order: by date.',text)
            self.assertIn('### Recurring terms',text)
            self.assertNotIn('## Not included',text)
            library, items = finished(root)
            items[0][0]['date'] = ''
            library['videos'].append(dict(status='failed',source='broken.vtt',error='No captions'))
            text = compose_summary(library,items)
            self.assertIn('as listed (some videos have no date)',text)
            self.assertIn('## Not included',text)

    def test_escaping_empty_and_one_video(self) -> None:
        self.assertEqual(cell('A|B\nC'), 'A\\|B C')
        entry = dict(video_id='talk',title='A|B',date='',start=None,end=None,duration=0,words=0,transcript='transcripts/talk.txt')
        text = compose_summary({'videos':[]}, [(entry,Transcript())])
        self.assertIn('None found.',text)
        self.assertNotIn('### Terms used in one video only',text)
        self.assertIn('A\\|B',text)

    def test_demo_golden(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            transcribe(read_manifest(helpers.ROOT / 'examples/demo/videos.csv'),root,RunOptions())
            prepare(root)
            self.assertEqual((root / 'summary.md').read_bytes(),(helpers.ROOT / 'tests/fixtures/expected_summary.md').read_bytes())
