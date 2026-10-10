"""Regressions from generated-speech and fresh-install review."""
import helpers
import contextlib
import io
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from channelrecap.cli import main
from channelrecap.errors import ToolError
from channelrecap.signals import deferrals
from channelrecap.summary import compose_summary
from channelrecap.transcriber import Settings, WhisperTranscriber
from channelrecap.transcript import Segment, Transcript
from channelrecap import tools


class ReviewTests(unittest.TestCase):
    def test_promises_and_future_explanations(self):
        positives = ["I promised a grinder comparison too.",
                     "The milk steaming tutorial is coming soon.",
                     "I promise a grinder comparison in the next video.",
                     "I'm promising a grinder comparison too.",
                     "We are promising a tutorial next time.",
                     "I've promised a grinder comparison too.",
                     "We have promised a steaming tutorial.",
                     "I was promising a follow-up on steaming.",
                     "I'll compare the grinders.", "We'll explain steaming.",
                     "Next time we compare the grinders.",
                     "Later we will explain steaming.",
                     "Later we explain steaming.", "We can discuss steaming later.",
                     "Coming up, a grinder comparison.",
                     "The follow-up will explain steaming.",
                     "Thanks for watching; we will cover drainage next week, see you then.",
                     "We'll cover drainage next time; thanks for watching, see you next week."]
        for text in positives:
            with self.subTest(text=text):
                items = deferrals(Transcript(segments=[Segment(12, 20, text)])).items
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0][:2], (12, text))
        negatives = ["See you next week.", "Thanks for watching, see you next time.",
                     "I'll see you next week.", "We'll see you next time.",
                     "I will see you next week.", "We'll see you later.",
                     "This is a promising grinder.", "The promise ring is silver.",
                     "A later harvest tastes sweeter.", "Later ongoing work continues.",
                     "Compare the first warm sip with a later cooler sip.",
                     "They compromised on the grinder."]
        for text in negatives:
            with self.subTest(text=text):
                self.assertEqual(deferrals(Transcript(segments=[Segment(0, 2, text)])).items, [])

    def test_cached_model_path_is_loaded_without_online_resolution(self):
        module = SimpleNamespace(download_model=Mock(return_value=helpers.CACHED_MODEL), WhisperModel=Mock())
        with patch.dict(sys.modules, faster_whisper=module):
            WhisperTranscriber(Settings(model='tiny'))
        module.download_model.assert_called_once_with('tiny', local_files_only=True)
        self.assertEqual(module.WhisperModel.call_args.args, (helpers.CACHED_MODEL,))
        self.assertTrue(module.WhisperModel.call_args.kwargs['local_files_only'])

    def test_missing_model_offline_never_attempts_download(self):
        module = SimpleNamespace(download_model=Mock(side_effect=FileNotFoundError), WhisperModel=Mock())
        with patch.dict(sys.modules, faster_whisper=module), patch.dict(os.environ, HF_HUB_OFFLINE='1'):
            with self.assertRaisesRegex(ToolError, 'local cache.*HF_HUB_OFFLINE=1'):
                WhisperTranscriber(Settings(model='tiny'))
        module.download_model.assert_called_once_with('tiny', local_files_only=True)
        module.WhisperModel.assert_not_called()

    def test_first_download_resolves_once_then_loads_local_path(self):
        module = SimpleNamespace(download_model=Mock(side_effect=[FileNotFoundError(), helpers.CACHED_MODEL]), WhisperModel=Mock())
        with patch.dict(sys.modules, faster_whisper=module), patch.dict(os.environ, HF_HUB_OFFLINE='0'), contextlib.redirect_stdout(io.StringIO()):
            WhisperTranscriber(Settings(model='tiny'))
        self.assertEqual(module.download_model.call_count, 2)
        self.assertEqual(module.WhisperModel.call_args.args, (helpers.CACHED_MODEL,))
        self.assertTrue(module.WhisperModel.call_args.kwargs['local_files_only'])

    def test_local_model_folder_never_resolves_hub(self):
        module = SimpleNamespace(download_model=Mock(), WhisperModel=Mock())
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, faster_whisper=module):
            helpers.make_model(Path(folder))
            WhisperTranscriber(Settings(model=folder))
        module.download_model.assert_not_called()

    def test_incomplete_cache_offline_does_not_load_or_download(self):
        with tempfile.TemporaryDirectory() as folder:
            helpers.make_model(Path(folder))
            for missing in ('model.bin', 'tokenizer.json', 'config.json', 'vocabulary.json'):
                with self.subTest(missing=missing):
                    helpers.make_model(Path(folder))
                    (Path(folder) / missing).unlink()
                    module = SimpleNamespace(download_model=Mock(return_value=folder), WhisperModel=Mock())
                    with patch.dict(sys.modules, faster_whisper=module):
                        with self.assertRaisesRegex(ToolError, 'local cache'):
                            WhisperTranscriber(Settings(model='tiny'))
                    module.download_model.assert_called_once_with('tiny', local_files_only=True)
                    module.WhisperModel.assert_not_called()

    def test_incomplete_local_folder_has_clear_error(self):
        module = SimpleNamespace(download_model=Mock(), WhisperModel=Mock())
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, faster_whisper=module):
            with self.assertRaisesRegex(ToolError, 'Local speech model folder is incomplete'):
                WhisperTranscriber(Settings(model=folder))
        module.download_model.assert_not_called()
        module.WhisperModel.assert_not_called()

    def test_first_download_warning_uses_stdout_and_restores_logging(self):
        logger = logging.getLogger('huggingface_hub')
        original_handlers, original_propagate = logger.handlers[:], logger.propagate
        def download(model, *, local_files_only=False):
            if local_files_only:
                raise FileNotFoundError
            logger.warning('Invented unauthenticated download notice')
            return helpers.CACHED_MODEL
        module = SimpleNamespace(download_model=Mock(side_effect=download), WhisperModel=Mock())
        with patch.dict(sys.modules, faster_whisper=module), patch.dict(os.environ, HF_HUB_OFFLINE='0'), contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            WhisperTranscriber(Settings(model='tiny'))
        self.assertIn('Invented unauthenticated download notice', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(logger.handlers, original_handlers)
        self.assertEqual(logger.propagate, original_propagate)

    def test_excluded_video_words_per_minute_blank_and_success_notices_stdout(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'library'
            caption = Path(folder) / 'short.vtt'
            caption.write_text('WEBVTT\n\n00:00 --> 00:03\nJust four spoken words.\n')
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(['run', str(caption), '--out', str(out)]), 0)
            self.assertEqual(stderr.getvalue(), '')
            self.assertIn('excluded from rates and trends; transcript retained.', stdout.getvalue())
            self.assertIn('done=1 cached=0 failed=0', stdout.getvalue())
            text = (out / 'summary.md').read_text()
            row = next(line for line in text.splitlines() if line.startswith('| 1 |'))
            self.assertEqual(row.split('|')[8].strip(), '')

    def test_decoder_diagnostics_equal_with_or_without_probe(self):
        self.assertEqual(str(tools.media_failure('moov atom not found', 'ffprobe')),
                         str(tools.media_failure('moov atom not found', 'ffmpeg')))
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'noaudio.mp4'
            source.write_bytes(b'invented')
            with patch.object(tools.shutil, 'which', return_value='ffprobe'), patch.object(tools, 'invoke', return_value='{"streams": []}'):
                with self.assertRaises(tools.NoAudioError) as caught:
                    tools.clip_audio(source, Path(folder) / 'out.wav', None, None)
            self.assertEqual(str(caught.exception), str(tools.media_failure('no audio stream', 'ffmpeg')))

    def test_mps_explains_supported_device(self):
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(main(['run', 'invented.wav', '--device', 'mps']), 2)
        self.assertIn('Apple GPU acceleration (MPS/Metal) is not supported; use --device cpu', stderr.getvalue())
