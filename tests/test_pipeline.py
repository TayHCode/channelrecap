import helpers
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from channelrecap import pipeline, tools
from channelrecap.errors import ToolError
from channelrecap.manifest import read_manifest, read_sources
from channelrecap.pipeline import RunOptions, transcribe, load_library
from channelrecap.transcript import read_data


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'lib'
        self.caption = self.root / 'talk.vtt'
        self.caption.write_text('WEBVTT\n\n00:00 --> 00:03\nCoffee tastes good with water.\n', encoding='utf-8')
        self.rows = read_sources([str(self.caption)])
        helpers.FakeTranscriber.created = 0
        self.err = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def test_captions_resume_force_limit(self) -> None:
        with patch.object(tools.shutil, 'which', return_value=None), patch.object(tools, 'has_module', return_value=False):
            self.assertEqual(transcribe(self.rows, self.out, RunOptions()), (1,0,0))
            self.assertEqual(transcribe(self.rows, self.out, RunOptions(), transcriber_factory=lambda _: self.fail('model created')), (0,1,0))
            self.assertEqual(transcribe(self.rows, self.out, RunOptions(force=True)), (1,0,0))
        second = self.root / 'other.vtt'
        second.write_text(self.caption.read_text(encoding='utf-8'), encoding='utf-8')
        rows = read_sources([str(self.caption), str(second)])
        other = self.root / 'limited'
        self.assertEqual(transcribe(rows, other, RunOptions(limit=1)), (1,0,0))
        self.assertEqual(len(load_library(other)['videos']), 1)

    def test_missing_artifact_is_rebuilt(self) -> None:
        transcribe(self.rows, self.out, RunOptions())
        entry = load_library(self.out)['videos'][0]
        (self.out / entry['transcript']).unlink()
        self.assertEqual(transcribe(self.rows, self.out, RunOptions()), (1,0,0))

    def test_failed_row_and_checkpoints(self) -> None:
        bad = self.root / 'empty.vtt'
        bad.write_text('WEBVTT\n', encoding='utf-8')
        rows = read_sources([str(bad), str(self.caption)])
        real = pipeline.atomic_json
        statuses = []
        def checkpoint(path, value):
            real(path, value)
            statuses.append([v['status'] for v in load_library(self.out)['videos']])
        with patch.object(pipeline, 'atomic_json', side_effect=checkpoint):
            self.assertEqual(transcribe(rows, self.out, RunOptions()), (1,0,1))
        self.assertEqual(statuses, [['failed'], ['failed','done']])
        self.assertEqual(transcribe(rows, self.root / 'fast', RunOptions(fail_fast=True)), (0,0,1))

    def test_preflight_leaves_output_absent(self) -> None:
        media = self.root / 'media.wav'
        media.write_text('placeholder', encoding='utf-8')
        with patch.object(tools.shutil, 'which', return_value=None), patch.object(tools, 'has_module', return_value=False), self.assertRaises(ToolError):
            transcribe(read_sources([str(media)]), self.out, RunOptions())
        self.assertFalse(self.out.exists())

    @unittest.skipIf(os.name == 'nt', 'executable stubs require POSIX')
    def test_media_links_cache_and_cleanup(self) -> None:
        directory = helpers.install_media_stubs(self.root)
        log = self.root / 'calls.txt'
        manifest = self.root / 'links.csv'
        manifest.write_text('source,start,end\nhttps://example.com/watch?v=sample,10,11\nhttps://example.com/watch?v=sample,20,21\n', encoding='utf-8')
        rows = read_manifest(manifest)
        with patch.dict(os.environ, {'PATH': str(directory), 'STUB_LOG': str(log)}):
            self.assertEqual(transcribe(rows, self.out, RunOptions(), transcriber_factory=helpers.FakeTranscriber), (2,0,0))
            self.assertEqual(helpers.FakeTranscriber.created, 1)
            calls = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
            downloads = [args for name,args in calls if name == 'yt-dlp' and '--output' in args]
            self.assertEqual(len(downloads), 1)
            self.assertIn('--no-playlist', downloads[0])
            self.assertEqual(len([1 for name,args in calls if '--dump-single-json' in args]), 2)
            self.assertFalse((self.out / '.cache').exists())
            entries = load_library(self.out)['videos']
            self.assertEqual(entries[0]['date'], '2026-01-10')
            self.assertEqual(read_data(self.out / entries[1]['data']).segments[0].words[0].start, 20)
            self.assertEqual(transcribe(rows, self.out, RunOptions(), transcriber_factory=helpers.FakeTranscriber), (0,2,0))
            self.assertEqual(helpers.FakeTranscriber.created, 1)
            self.assertEqual(len(log.read_text(encoding='utf-8').splitlines()), len(calls))
            transcribe(rows, self.out, RunOptions(force=True, keep_audio=True, keep_downloads=True), transcriber_factory=helpers.FakeTranscriber)
            self.assertEqual(len(list((self.out / '.cache/audio').glob('*.wav'))), 2)
            self.assertEqual(len(list((self.out / '.cache/downloads').glob('*.wav'))), 1)
            manifest.write_text('source,start,end,title\nhttps://example.com/watch?v=sample,10,11,Changed\n', encoding='utf-8')
            transcribe(read_manifest(manifest), self.out, RunOptions())
            self.assertEqual(load_library(self.out)['videos'][0]['video_id'], entries[0]['video_id'])

    @unittest.skipIf(os.name == 'nt', 'executable stubs require POSIX')
    def test_link_rejections_and_empty_audio(self) -> None:
        directory = helpers.install_media_stubs(self.root)
        rows = read_sources(['https://example.com/watch?v=sample'])
        with patch.dict(os.environ, {'PATH': str(directory), 'STUB_LOG': str(self.root / 'calls.txt')}):
            for mode, expected in [('playlist','playlist or channel'), ('live','has not finished'), ('upcoming','has not finished')]:
                with patch.dict(os.environ, {'STUB_LINK_MODE': mode}):
                    self.assertEqual(transcribe(rows, self.out, RunOptions(), transcriber_factory=helpers.FakeTranscriber), (0,0,1))
                    self.assertIn(expected, load_library(self.out)['videos'][0]['error'])
            with patch.dict(os.environ, {'STUB_SECONDS':'0.1'}):
                transcribe(rows, self.out, RunOptions(), transcriber_factory=helpers.FakeTranscriber)
                self.assertIn('No audio found', load_library(self.out)['videos'][0]['error'])
            self.assertEqual(helpers.FakeTranscriber.created, 0)

    @unittest.skipIf(os.name == 'nt', 'executable stubs require POSIX')
    def test_local_media_and_interrupt_preserve_finished(self) -> None:
        directory = helpers.install_media_stubs(self.root)
        media = self.root / 'talk.wav'
        media.write_text('placeholder', encoding='utf-8')
        rows = read_sources([str(self.caption), str(media)])
        def interrupted(_):
            raise KeyboardInterrupt
        with patch.dict(os.environ, {'PATH': str(directory), 'STUB_LOG': str(self.root / 'calls.txt')}):
            with self.assertRaises(KeyboardInterrupt):
                transcribe(rows, self.out, RunOptions(), transcriber_factory=interrupted)
            self.assertEqual(load_library(self.out)['videos'][0]['status'], 'done')
            self.assertFalse((self.out / '.cache').exists())
            self.assertEqual(transcribe(rows, self.out, RunOptions(), transcriber_factory=helpers.FakeTranscriber), (1,1,0))


class AdapterTests(unittest.TestCase):
    def test_decode_type_error_is_isolated_to_one_row(self) -> None:
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from channelrecap.transcriber import Settings, WhisperTranscriber
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = root / 'first.wav', root / 'second.wav'
            helpers.make_wav(first)
            helpers.make_wav(second)
            info = SimpleNamespace(language='en', language_probability=1.0, duration=1.0)
            segment = SimpleNamespace(start=0, end=1, text='A paper filter holds coffee.', words=[])
            def fail_later():
                raise TypeError('decoder mismatch')
                yield
            for failure in (TypeError('decoder mismatch'), (fail_later(), info)):
                with self.subTest(failure=type(failure).__name__):
                    engine = Mock()
                    engine.transcribe.side_effect = [failure, (iter([segment]), info)]
                    module = SimpleNamespace(download_model=Mock(return_value=helpers.CACHED_MODEL), WhisperModel=Mock(return_value=engine))
                    def clip(source, output, start, end):
                        helpers.make_wav(output)
                    with patch.dict(sys.modules, {'faster_whisper': module, 'ctranslate2': SimpleNamespace()}), patch.object(tools, 'preflight'), patch.object(tools, 'clip_audio', side_effect=clip), contextlib.redirect_stderr(io.StringIO()):
                        out = root / type(failure).__name__
                        self.assertEqual(transcribe(read_sources([str(first), str(second)]), out, RunOptions()), (1, 0, 1))
                    entries = load_library(out)['videos']
                    self.assertEqual([e['status'] for e in entries], ['failed', 'done'])
                    self.assertIn('decoder mismatch', entries[0]['error'])

    def test_adapter_options_times_and_progress(self) -> None:
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from channelrecap.transcriber import Settings, WhisperTranscriber, VERBATIM_HINT
        word = SimpleNamespace(start=0.12345, end=0.8, word='Coffee', probability=.9)
        segment = SimpleNamespace(start=0.12345, end=1.0, text=' Coffee tastes good. ', words=[word])
        engine = Mock()
        engine.transcribe.return_value = (iter([segment]), SimpleNamespace(language='en', language_probability=0.99, duration=1.0))
        module = SimpleNamespace(download_model=Mock(return_value=helpers.CACHED_MODEL), WhisperModel=Mock(return_value=engine))
        cuda = SimpleNamespace(get_cuda_device_count=lambda: 0)
        with patch.dict(sys.modules, {'faster_whisper':module, 'ctranslate2':cuda}):
            adapter = WhisperTranscriber(Settings(prompt='coffee terms'))
            progress = []
            transcript = adapter.transcribe(Path('clip.wav'), progress=progress.append)
        self.assertEqual(module.WhisperModel.call_args.kwargs, dict(device='cpu',compute_type='int8',local_files_only=True))
        self.assertEqual(engine.transcribe.call_args.kwargs, dict(language=None,beam_size=5,vad_filter=True,word_timestamps=True,initial_prompt='coffee terms'))
        self.assertEqual(transcript.segments[0].start, 0.123)
        self.assertEqual(transcript.segments[0].text, 'Coffee tastes good.')
        self.assertEqual(progress, list(range(5,101,5)))
        self.assertNotIn('faster_whisper', sys.modules)

    def test_gpu_failure_and_auto_fallback(self) -> None:
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock
        from channelrecap.transcriber import Settings, WhisperTranscriber
        module = SimpleNamespace(download_model=Mock(return_value=helpers.CACHED_MODEL), WhisperModel=Mock(side_effect=RuntimeError('unavailable')))
        cuda = SimpleNamespace(get_cuda_device_count=lambda: 1)
        with patch.dict(sys.modules, {'faster_whisper':module, 'ctranslate2':cuda}):
            with self.assertRaisesRegex(ToolError, 'docs/gpu.md'):
                WhisperTranscriber(Settings(device='cuda'))
            module.WhisperModel.side_effect = [RuntimeError('unavailable'), object()]
            with contextlib.redirect_stderr(io.StringIO()):
                adapter = WhisperTranscriber(Settings(device='auto'))
            self.assertEqual((adapter.device, adapter.compute_type), ('cpu','int8'))
