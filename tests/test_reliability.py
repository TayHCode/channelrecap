"""Regression coverage for silent media, diagnostics and setup."""
import helpers
import contextlib
import csv
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from channelrecap import cli, tools
from channelrecap.errors import DamagedMediaError, NoAudioError
from channelrecap.manifest import read_sources
from channelrecap.pipeline import RunOptions, transcribe, load_library
from channelrecap.signals import deferrals
from channelrecap.speech import speech_status
from channelrecap.summary import compose_summary, prepare
from channelrecap.transcript import Segment, Transcript


class ReliabilityTests(unittest.TestCase):
    def fixture(self):
        rows = json.loads((helpers.ROOT / 'tests/fixtures/speech_eligibility.json').read_text())
        items = []
        for row in rows:
            text = 'um ' * row['fillers'] + 'lantern ' * (row['words'] - row['fillers'])
            t = Transcript(segments=[Segment(0, 60, text)] if text else [], duration=60)
            e = dict(video_id=row['label'], title=row['label'], date='', words=row['words'],
                     duration=60, start=None, end=None, transcript='transcripts/' + row['label'] + '.txt')
            items.append((e, t))
        return items

    def test_silent_and_short_rows_do_not_change_any_trend_series(self):
        items = self.fixture()
        summary = compose_summary({'videos': []}, items)
        self.assertIn('35.0, 26.0, 27.0', summary)
        self.assertIn('Trend, clear fillers per 1,000 words: no consistent direction', summary)
        self.assertNotIn('per 1,000 words: falling', summary)
        self.assertIn('no speech detected in silent: excluded from rates and trends', summary)
        self.assertIn('too few words detected (fewer than 20) in short', summary)
        self.assertIn('no_speech', summary)
        self.assertIn('too_few_words', summary)
        summary = compose_summary({'videos': []}, items[:2] + items[3:])
        self.assertIn('not enough videos for a trend', summary)
        self.assertNotIn('per 1,000 words: falling', summary)
        self.assertNotIn('per 1,000 words: rising', summary)

    def test_threshold_uses_actual_transcript_not_stale_metadata(self):
        for words, expected in ((0, 'no_speech'), (19, 'too_few_words'), (20, 'usable')):
            self.assertEqual(speech_status(Transcript(segments=[Segment(0, 1, 'lantern ' * words)])), expected)

    def test_silent_media_retained_marked_and_csv_rates_blank_after_prepare(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / 'silence.wav'
            helpers.make_wav(media)
            engine = SimpleNamespace(transcribe=lambda *a, **kw: Transcript(duration=1))
            out = root / 'library'
            with patch.object(tools, 'preflight'), patch.object(tools, 'clip_audio'), contextlib.redirect_stdout(io.StringIO()) as err:
                self.assertEqual(transcribe(read_sources([str(media)]), out, RunOptions(), transcriber_factory=lambda _: engine), (1, 0, 0))
            self.assertIn('no speech detected', err.getvalue())
            e = load_library(out)['videos'][0]
            self.assertEqual(e['speech_status'], 'no_speech')
            self.assertFalse(e['trend_usable'])
            self.assertEqual(e['status'], 'done')
            self.assertTrue((out / e['transcript']).is_file())
            # An older index without flags is safely upgraded by prepare.
            path = out / 'library.json'
            index = json.loads(path.read_text())
            for field in ('speech_status', 'trend_usable', 'notice'):
                del index['videos'][0][field]
            path.write_text(json.dumps(index))
            self.assertEqual(prepare(out), 1)
            self.assertFalse(load_library(out)['videos'][0]['trend_usable'])
            with (out / 'fillers.csv').open() as stream:
                self.assertTrue(all(row['per_1000_words'] == '' for row in csv.DictReader(stream)))
            self.assertIn('not enough videos for a trend', (out / 'summary.md').read_text())

    def test_error_classes_exit_codes_and_no_path_disclosure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            doc = root / 'wrong.doc'
            doc.write_text('not media')
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(['run', str(doc), '--out', str(root / 'out')]), 4)
            self.assertIn('unsupported file type (.doc): use audio/video', err.getvalue())
            self.assertNotIn(folder, err.getvalue())
            self.assertFalse((root / 'out').exists())
            bad = root / 'bad.mp4'
            bad.write_bytes(b'invented incomplete movie')
            for exc, code, message in ((DamagedMediaError('could not read this media file (damaged or incomplete): decoder says: moov atom not found'), 5, 'damaged or incomplete'),
                                       (NoAudioError('no audio stream'), 6, 'no audio stream')):
                with patch.object(tools, 'preflight'), patch.object(tools, 'clip_audio', side_effect=exc), contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(['run', str(bad), '--out', str(root / str(code))]), code)
                self.assertIn(message, err.getvalue())
                self.assertNotIn(folder, err.getvalue())
            with patch.object(tools.shutil, 'which', return_value=None), patch.object(tools, 'has_module', return_value=False), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(['run', str(bad), '--out', str(root / 'missing')]), 3)
            self.assertIn('ffmpeg', err.getvalue())

    def test_diagnostics_only_emit_known_reasons(self):
        for path in ('/invented/private room/take.mp4', r'C:\Invented\Private Room\take.mp4', 'file:///invented/take.mp4'):
            exc = tools.media_failure(path + ': moov atom not found', 'ffprobe')
            self.assertIsInstance(exc, DamagedMediaError)
            self.assertIn('decoder says: moov atom not found', str(exc))
            self.assertNotIn(path, str(exc))
        self.assertIsInstance(tools.media_failure('Output file does not contain any stream', 'ffmpeg'), NoAudioError)
        with patch.object(tools.subprocess, 'run', side_effect=FileNotFoundError), self.assertRaisesRegex(tools.ToolError, 'ffmpeg missing'):
            tools.invoke(['ffmpeg'])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'media tools unavailable')
    def test_real_corrupt_and_video_without_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bad = root / 'bad.mp4'
            bad.write_bytes(b'not a movie')
            with self.assertRaisesRegex(DamagedMediaError, 'decoder says:') as caught:
                tools.clip_audio(bad, root / 'out.wav', None, None)
            self.assertNotIn(folder, str(caught.exception))
            video = root / 'silent-video.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=size=16x16:rate=1', '-t', '1', '-an', str(video)], check=True)
            with self.assertRaisesRegex(NoAudioError, 'no audio stream'):
                tools.clip_audio(video, root / 'out.wav', None, None)
            with patch.object(tools.shutil, 'which', return_value=None):
                with self.assertRaises(DamagedMediaError):
                    tools.clip_audio(bad, root / 'out.wav', None, None)
                with self.assertRaises(NoAudioError):
                    tools.clip_audio(video, root / 'out.wav', None, None)

    def test_manifest_preflight_is_atomic_and_does_not_crawl_channels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            valid = helpers.ROOT / 'examples/demo/episode1.vtt'
            for invalid in (str(root / 'missing.wav'), 'https://www.youtube.com/@invented/videos', 'https://www.youtube.com/playlist?list=invented', 'https://www.youtube.com/watch?v=invented&list=invented', 'https://www.youtube.com/playlist?list='):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(cli.main(['run', str(valid), invalid, '--out', str(root / 'out')]), 2)
                self.assertFalse((root / 'out').exists())
                self.assertIn('no rows were processed', err.getvalue())
                if invalid.startswith('http'):
                    self.assertIn('not crawled', err.getvalue())

    def test_invalid_url_has_a_clear_preflight_error(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stderr(io.StringIO()) as err:
            out = Path(folder) / 'out'
            self.assertEqual(cli.main(['run', 'https://[broken', '--out', str(out)]), 2)
            self.assertIn('invalid URL', err.getvalue())
            self.assertFalse(out.exists())

    def test_signoffs_ignored_but_substantive_promises_retained(self):
        transcript = Transcript(segments=[Segment(0, 1, 'See you next week. Thanks for watching, see you next time. We will cover soil drainage next week. Thanks for watching; another video will explain pruning.')])
        hints = deferrals(transcript).items
        self.assertEqual(len(hints), 2)
        self.assertTrue(any('soil drainage' in h[1] for h in hints))
        self.assertTrue(any('pruning' in h[1] for h in hints))

    def test_python_guard_explains_old_version_before_venv(self):
        spec = importlib.util.spec_from_file_location('guard', helpers.ROOT / 'scripts/check_python.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(sys, 'version_info', (3, 9, 6)), patch.object(sys, 'version', '3.9.6 invented'), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(module.main(), 3)
        self.assertIn('channelrecap needs Python 3.11 or newer; you have 3.9.6', err.getvalue())
        self.assertIn('install uv:', err.getvalue())
        self.assertIn('uv venv --seed --python 3.12 .venv', err.getvalue())

    def test_heading_error_shows_expected_level_and_example(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            cli.main(['run', '--manifest', str(helpers.ROOT / 'examples/demo/videos.csv'), '--out', str(out)])
            report = out / 'report.md'
            report.write_text('# Video analysis\n## 1. Topics and trends\n')
            with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['check-citations', str(report), '--out', str(out)]), 1)
            self.assertIn('level-3 heading (###)', err.getvalue())
            self.assertIn('### 1. Topics and trends', err.getvalue())
