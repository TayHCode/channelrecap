import helpers
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from channelrecap.transcript import Segment, Word, Transcript, paragraphs, read_data, write_data, write_text
from channelrecap.errors import InputError


class TranscriptTests(unittest.TestCase):
    def test_top_level_data_validation(self) -> None:
        good = asdict(Transcript(segments=[Segment(0, 1, 'A warm cup.', [Word(0, 1, 'A')])]))
        cases = []
        for name in ('duration', 'offset'):
            for value in ('2', None, -1, True, [], {}, float('nan'), float('inf'), float('-inf'), 10 ** 400):
                cases.append((name, value))
        for value in ('0.9', -0.1, 1.1, True, [], {}, float('nan'), float('inf')):
            cases.append(('language_probability', value))
        for name in ('video_id', 'language'):
            for value in (None, [], 1, 'bad\x00text', '\ud800'):
                cases.append((name, value))
        for value in (True, '1', 1.0, 2, None):
            cases.append(('schema_version', value))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'data.json'
            for name, value in cases:
                with self.subTest(field=name, value=repr(value)):
                    path.write_text(json.dumps({**good, name: value}), encoding='utf-8')
                    with self.assertRaises(InputError):
                        read_data(path)
            for name in good:
                with self.subTest(missing=name):
                    path.write_text(json.dumps({k: v for k, v in good.items() if k != name}), encoding='utf-8')
                    with self.assertRaises(InputError):
                        read_data(path)
            for value in (None, 0, 1, 0.5):
                path.write_text(json.dumps({**good, 'language_probability': value}), encoding='utf-8')
                self.assertEqual(read_data(path).language_probability, value)
            for value in ('\ud800', '\x00'):
                data = json.loads(json.dumps(good))
                for key in ('text', 'word'):
                    target = data['segments'][0] if key == 'text' else data['segments'][0]['words'][0]
                    target[key] = value
                    path.write_text(json.dumps(data), encoding='utf-8')
                    with self.assertRaises(InputError):
                        read_data(path)

    def test_paragraph_rules(self) -> None:
        self.assertEqual(len(paragraphs([Segment(0,1,'One.'),Segment(3,4,'Two.')])), 2)
        self.assertEqual(len(paragraphs([Segment(0,30,'One.'),Segment(30,40,'Two.')])), 2)
        self.assertEqual(len(paragraphs([Segment(0,30,'One'),Segment(30,60,'two'),Segment(60,65,'three')])), 2)
        self.assertEqual(paragraphs([Segment(0,1,'One'),Segment(1,2,'two.')]), [(0,'One two.')])

    def test_offset_roundtrip_and_headers(self) -> None:
        transcript = Transcript(language='en', duration=2.55555, segments=[Segment(0.11111,2,'Speech is here.',[Word(0.1,0.5,'Speech')])])
        transcript.add_offset(90)
        self.assertEqual(transcript.segments[0].start, 90.111)
        self.assertEqual(transcript.segments[0].words[0].end, 90.5)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_data(root / 'data.json', transcript)
            self.assertEqual(read_data(root / 'data.json'), transcript)
            for start, end, expected in [(None,None,'whole video'),(0,None,'from 00:00:00'),(None,60,'up to 00:01:00'),(0,60,'00:00:00 to 00:01:00')]:
                entry = dict(title='Talk', video_id='talk', source='talk.wav', date='', start=start, end=end, words=3)
                write_text(root / 'talk.txt', transcript, entry)
                text = (root / 'talk.txt').read_text(encoding='utf-8')
                self.assertIn('Covered: ' + expected, text)
                self.assertIn('[00:01:30] Speech is here.', text)
                self.assertIn('Words: 3', text)
