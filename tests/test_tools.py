import helpers
from pathlib import Path
import contextlib
import io
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from channelrecap import tools
from channelrecap.errors import ToolError, RowError


class ToolTests(unittest.TestCase):
    def test_download_template_escapes_percent_and_cache_ids_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / '100% audio'
            cache.mkdir()
            other = cache / 'demo_v1.5.wav'
            other.write_text('other invented source', encoding='utf-8')
            expected = cache / 'demo_v1.wav'
            def download(args, **kwargs):
                template = args[args.index('--output') + 1]
                self.assertIn('100%% audio', template)
                self.assertIn('%(id)s', template)
                expected.write_text('wanted invented source', encoding='utf-8')
                return ''
            with patch.object(tools, 'downloader', return_value=['stub']), patch.object(tools, 'invoke', side_effect=download) as invoke:
                self.assertEqual(tools.download_audio('https://example.com/watch', {'extractor': 'demo', 'id': 'v1'}, cache), expected)
                invoke.assert_called_once()
                self.assertEqual(tools.download_audio('https://example.com/watch', {'extractor': 'demo', 'id': 'v1.5'}, cache), other)
                invoke.assert_called_once()

    def test_clip_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'clip.wav'
            def fake(args, **kwargs):
                helpers.make_wav(output)
                return ''
            with patch.object(tools, 'invoke', side_effect=fake) as run:
                tools.clip_audio(Path('-source.wav'), output, 12.5, 15)
                args = run.call_args.args[0]
                self.assertEqual(args[args.index('-ss') + 1], '12.500')
                self.assertLess(args.index('-ss'), args.index('-i'))
                self.assertEqual(args[args.index('-t') + 1], '2.500')
                self.assertIn('./-source.wav', args)
                tools.clip_audio(Path('source.wav'), output, None, None)
                self.assertNotIn('-ss', run.call_args.args[0])
                self.assertNotIn('-t', run.call_args.args[0])

    def test_resolution_and_missing(self) -> None:
        with patch.object(tools.shutil, 'which', return_value='stub'), patch.object(tools, 'has_module', return_value=True):
            self.assertEqual(tools.downloader(), ['stub'])
        with patch.object(tools.shutil, 'which', return_value=None), patch.object(tools, 'has_module', return_value=True):
            self.assertEqual(tools.downloader()[-2:], ['-m','yt_dlp'])
        with patch.object(tools.shutil, 'which', return_value=None), patch.object(tools, 'has_module', return_value=False):
            with self.assertRaises(ToolError) as caught:
                tools.preflight({'link'})
            for name in ['ffmpeg','faster-whisper','yt-dlp']:
                self.assertIn(name, str(caught.exception))
            tools.preflight({'captions'})

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_real_ffmpeg_tone(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            helpers.make_wav(root / 'tone.wav', 3)
            tools.clip_audio(root / 'tone.wav', root / 'clip.wav', 1, 2)
            with wave.open(str(root / 'clip.wav')) as audio:
                self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 1, places=2)
                self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1,2,16000))

    def test_metadata_rejects_unsafe_identity(self) -> None:
        with patch.object(tools, 'downloader', return_value=['stub']), patch.object(tools, 'invoke', return_value='{"extractor":"../bad","id":"x"}'):
            with self.assertRaises(RowError):
                tools.link_metadata('https://example.com/watch')

    def test_doctor(self) -> None:
        with patch.object(tools.shutil, 'which', return_value=None), patch.object(tools, 'has_module', return_value=False), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tools.doctor(), 3)
            self.assertEqual(out.getvalue().strip().splitlines()[-1], 'doctor missing=ffmpeg,faster-whisper')
        with patch.object(tools.shutil, 'which', return_value='stub'), patch.object(tools, 'has_module', return_value=True), patch.object(tools, 'check_audio_decode'), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tools.doctor(), 0)
            self.assertEqual(out.getvalue().strip().splitlines()[-1], 'doctor ok')

    def test_doctor_decodes_audio_and_reports_incompatible_decoder(self) -> None:
        def decode(audio, sampling_rate):
            self.assertEqual(sampling_rate, 16000)
            with wave.open(audio) as stream:
                self.assertEqual((stream.getnframes(), stream.getnchannels(), stream.getsampwidth()), (1600, 1, 2))
            return [0.0] * 1600
        decoder = Mock(side_effect=decode)
        module = SimpleNamespace(decode_audio=decoder)
        with patch.dict(sys.modules, {'faster_whisper.audio': module}), patch.object(tools.shutil, 'which', return_value='stub'), patch.object(tools, 'has_module', return_value=True):
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(tools.doctor(), 0)
            self.assertIn('audio decode: ok', out.getvalue())
            decoder.assert_called_once()
            decoder.side_effect = TypeError("open() got an unexpected keyword argument 'metadata_errors'")
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(tools.doctor(), 3)
            self.assertEqual(out.getvalue().strip().splitlines()[-1], 'doctor missing=audio-decode')
            self.assertIn('av<19', out.getvalue())
            self.assertIn('TypeError', out.getvalue())
            self.assertIn('from the clone folder, run: python -m pip install', out.getvalue())
        self.assertNotIn('faster_whisper', sys.modules)

    def test_extras_and_repair_command_exclude_incompatible_decoder(self) -> None:
        import tomllib
        extras = tomllib.loads((helpers.ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['optional-dependencies']
        for name in ('all', 'transcribe'):
            self.assertIn('av<19', extras[name])
        with patch.object(tools.shutil, 'which', return_value='stub'), patch.object(tools, 'has_module', return_value=False):
            with self.assertRaises(ToolError) as caught:
                tools.preflight({'media'})
        self.assertIn(tools.TRANSCRIBE_INSTALL, str(caught.exception))
        self.assertIn('from the clone folder, run: python -m pip install', str(caught.exception))
