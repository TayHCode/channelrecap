"""Regressions for cached network use and the third fresh-install review."""
import helpers
import builtins
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from channelrecap.cli import main
from channelrecap.signals import deferrals, trend_label
from channelrecap.summary import compose_summary
from channelrecap.transcriber import Settings, WhisperTranscriber
from channelrecap.transcript import Segment, Transcript


class RoundThreeTests(unittest.TestCase):
    def test_telemetry_disabled_before_optional_imports_and_model_load(self):
        imported = builtins.__import__
        ort = SimpleNamespace(disable_telemetry_events=Mock())
        def guarded_import(name, *args, **kwargs):
            if name in {'onnxruntime', 'faster_whisper'}:
                for variable in ('ORT_DISABLE_TELEMETRY', 'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK'):
                    self.assertEqual(os.environ.get(variable), '1', (name, variable))
            return imported(name, *args, **kwargs)
        def load(*args, **kwargs):
            ort.disable_telemetry_events.assert_called_once_with()
        whisper = SimpleNamespace(download_model=Mock(return_value=helpers.CACHED_MODEL),
                                  WhisperModel=Mock(side_effect=load))
        with patch.dict(sys.modules, onnxruntime=ort, faster_whisper=whisper), \
                patch.dict(os.environ, ORT_DISABLE_TELEMETRY='0', HF_HUB_DISABLE_TELEMETRY='0', DO_NOT_TRACK='0'), \
                patch('builtins.__import__', side_effect=guarded_import):
            WhisperTranscriber(Settings(model='base'))

    def test_small_sample_trend_and_summary_state_counts(self):
        counts = [3, 4, 6, 6]
        self.assertTrue(trend_label([n * 1000 / 90 for n in counts], counts).startswith('too few to call a trend'))
        items = []
        for i, n in enumerate(counts):
            transcript = Transcript(segments=[Segment(0, 20, 'you know ' * n + 'lantern ' * (90 - 2 * n) + '.')])
            entry = dict(video_id=f'video-{i}', title='Invented', date='', start=None, end=None,
                         duration=20, words=90, transcript=f'transcripts/video-{i}.txt')
            items.append((entry, transcript))
        summary = compose_summary({'videos': []}, items)
        line = next(s for s in summary.splitlines() if s.startswith('Trend, clear fillers'))
        self.assertIn('too few to call a trend', line)
        self.assertIn('(19 matches in 4 usable videos)', line)

    def test_trend_threshold_and_relative_difference(self):
        self.assertTrue(trend_label([10, 20, 30], [9, 10, 10]).startswith('too few'))
        self.assertEqual(trend_label([10, 20, 30], [10, 10, 10]), 'rising')
        self.assertEqual(trend_label([30, 20, 10], [10, 10, 10]), 'falling')
        self.assertEqual(trend_label([10, 11, 12], [10, 10, 10]), 'steady')
        self.assertEqual(trend_label([10, 20, 10], [10, 10, 10]), 'steady')

    def test_cached_cuda_warns_without_loading_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'talk.wav'
            helpers.make_wav(source)
            output = root / 'library'
            bins = helpers.install_media_stubs(root)
            env = {'PATH': str(bins), 'STUB_LOG': str(root / 'calls.log')}
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['run', str(source), '--out', str(output)], transcriber_factory=helpers.FakeTranscriber), 0)
            factory = Mock(side_effect=AssertionError('cached rows must not load a model'))
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(main(['run', str(source), '--out', str(output), '--device', 'cuda'], transcriber_factory=factory), 0)
            factory.assert_not_called()
            self.assertIn('CUDA was not validated', stdout.getvalue())
            self.assertIn('cached media', stdout.getvalue())

    def test_python_guard_missing_ensurepip_fails_before_ok(self):
        spec = importlib.util.spec_from_file_location('guard_round3', helpers.ROOT / 'scripts/check_python.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.importlib.util, 'find_spec', return_value=None), \
                contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(module.main(), 3)
        self.assertNotIn(': ok', stdout.getvalue())
        self.assertIn('ensurepip is missing', stderr.getvalue())
        self.assertIn('uv venv', stderr.getvalue())

    def test_unfinished_promise_quote_is_marked(self):
        text = 'The milk steaming tutorial is coming soon, and I'
        hint = deferrals(Transcript(segments=[Segment(0, 5, text)])).items[0]
        self.assertEqual(hint[1], text + ' …')
        complete = 'The milk steaming tutorial is coming soon.'
        self.assertEqual(deferrals(Transcript(segments=[Segment(0, 5, complete)])).items[0][1], complete)

    def test_range_edge_promise_marks_possible_cutoff_despite_model_punctuation(self):
        text = 'The milk steaming tutorial is coming soon, and I.'
        transcript = Transcript(duration=5, offset=10, segments=[Segment(10, 15, text)])
        entry = dict(video_id='range-demo', title='Invented', date='', start=10, end=15,
                     duration=5, words=10, transcript='transcripts/range-demo.txt')
        self.assertIn(f'"{text} …"', compose_summary({'videos': []}, [(entry, transcript)]))
        for end in (None, 14, 20):
            entry['end'] = end
            summary = compose_summary({'videos': []}, [(entry, transcript)])
            self.assertIn(f'"{text}"', summary)
            self.assertNotIn(f'"{text} …"', summary)

    def test_range_edge_marks_only_terminal_promise_sentence(self):
        first = 'I will explain the grinder next week.'
        last = 'I will also compare milk steaming, and I'
        transcript = Transcript(duration=5, segments=[Segment(0, 5, first + ' ' + last)])
        entry = dict(video_id='range-demo', title='Invented', date='', start=0, end=5,
                     duration=5, words=20, transcript='transcripts/range-demo.txt')
        summary = compose_summary({'videos': []}, [(entry, transcript)])
        self.assertIn(f'"{first}"', summary)
        self.assertIn(f'"{last} …"', summary)
        self.assertNotIn(f'"{last} … …"', summary)
