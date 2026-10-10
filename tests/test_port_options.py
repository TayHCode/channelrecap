import helpers
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace
import sys

from channelrecap.cli import main, parser
from channelrecap.pipeline import RunOptions, transcribe, load_library
from channelrecap.manifest import read_manifest
from channelrecap.transcript import Word, Segment, Transcript, write_data, read_data, validate_transcript
from channelrecap.errors import InputError


class PortOptionsTests(unittest.TestCase):
    def test_hint_opt_in_and_prompt_alias(self):
        from channelrecap import tools
        from channelrecap.transcriber import VERBATIM_HINT
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root/'talk.wav'
            helpers.make_wav(source)
            prompt = root/'names.txt'
            prompt.write_text('  \n', encoding='utf-8')
            engine = Mock()
            module = SimpleNamespace(download_model=Mock(return_value=helpers.CACHED_MODEL),WhisperModel=Mock(return_value=engine))
            for hint in [False,True]:
                out = root/str(hint)
                engine.transcribe.return_value = (iter([SimpleNamespace(start=0,end=1,text='Um coffee tastes good.',words=[])]),
                    SimpleNamespace(language='en',language_probability=1.0,duration=1.0))
                args = ['transcribe',str(source),'--output',str(out),'--initial-prompt-file',str(prompt)]
                if hint:
                    args.append('--verbatim-hint')
                with patch.dict(sys.modules,{'faster_whisper':module}), patch.object(tools,'preflight'), patch.object(tools,'clip_audio'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(args),0)
                self.assertEqual(engine.transcribe.call_args.kwargs['initial_prompt'],VERBATIM_HINT if hint else None)
                self.assertEqual(load_library(out)['videos'][0]['transcription']['verbatim_hint'],hint)

    def test_defaults_and_original_flag_aliases(self):
        args = parser().parse_args(['transcribe','talk.wav','--output','library','--initial-prompt-file','names.txt'])
        self.assertEqual((args.out,args.prompt_file),(Path('library'),Path('names.txt')))
        self.assertEqual((args.model,args.device,args.language),('small','cpu',None))
        self.assertFalse(args.save_site_captions)

    def test_null_word_times_probability_and_offset_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'data.json'
            transcript = Transcript(duration=1,segments=[Segment(0,1,'Um coffee',[
                Word(None,None,' Um',.87543),Word(.1,.8,' coffee',.98765)])])
            transcript.add_offset(60)
            write_data(path,transcript)
            result = read_data(path)
            self.assertEqual(result.segments[0].words,[Word(None,None,' Um',.87543),Word(60.1,60.8,' coffee',.98765)])

    def test_word_probability_validation_and_partial_times(self):
        for probability in [None,0,.5,1]:
            validate_transcript(Transcript(duration=1,segments=[Segment(0,1,'Coffee',[
                Word(None,.5,' Coffee',probability),Word(.5,None,' water',probability)])]))
        for probability in [-.1,1.1,float('nan'),float('inf'),'high',True]:
            with self.subTest(probability=probability), self.assertRaises(InputError):
                validate_transcript(Transcript(duration=1,segments=[Segment(0,1,'Coffee',[
                    Word(None,None,' Coffee',probability)])]))
        with self.assertRaises(InputError):
            validate_transcript(Transcript(duration=1,segments=[Segment(None,1,'Coffee')]))

    @unittest.skipIf(os.name == 'nt','executable stubs require POSIX')
    def test_site_captions_opt_in_languages_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = helpers.install_media_stubs(root)
            log = root/'calls.txt'
            manifest = root/'sources.csv'
            manifest.write_text('source,start,end,language\nhttps://example.com/a,0,1,fr\nhttps://example.com/b,0,1,\n', encoding='utf-8')
            rows = read_manifest(manifest)
            with patch.dict(os.environ,PATH=str(directory),STUB_LOG=str(log)), contextlib.redirect_stderr(io.StringIO()):
                out = root/'100% library'
                self.assertEqual(transcribe(rows,out,RunOptions(save_site_captions=True,language='de'),transcriber_factory=helpers.FakeTranscriber),(2,0,0))
                calls = [args for name,args in map(json.loads,log.read_text(encoding='utf-8').splitlines()) if '--write-subs' in args]
                self.assertEqual([args[args.index('--sub-langs')+1] for args in calls],['fr.*,fr','de.*,de'])
                self.assertTrue(all('100%% library' in args[args.index('--output')+1] for args in calls))
                self.assertEqual(len(list((out/'site-captions').glob('*/*.vtt'))),2)
                log.write_text('', encoding='utf-8')
                self.assertEqual(transcribe(rows,root/'off',RunOptions(),transcriber_factory=helpers.FakeTranscriber),(2,0,0))
                self.assertNotIn('--write-subs',log.read_text(encoding='utf-8'))
                with patch.dict(os.environ,STUB_CAPTIONS_FAIL='1'):
                    self.assertEqual(transcribe(rows,root/'failed',RunOptions(save_site_captions=True),transcriber_factory=helpers.FakeTranscriber),(2,0,0))
                calls = [args for name,args in map(json.loads,log.read_text(encoding='utf-8').splitlines()) if '--write-subs' in args]
                self.assertEqual(calls[-1][calls[-1].index('--sub-langs')+1],'en.*,en')
                self.assertEqual(list((root/'failed/site-captions').iterdir()),[])

    def test_fail_fast_exit_without_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bad = root/'bad.vtt'
            good = root/'good.vtt'
            bad.write_text('WEBVTT', encoding='utf-8')
            good.write_text('WEBVTT\n\n00:00 --> 00:01\nCoffee tastes good.\n', encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
                code = main(['transcribe',str(bad),str(good),'--out',str(root/'lib'),'--fail-fast'])
            self.assertEqual(code,1)
            self.assertNotIn('Traceback',err.getvalue())
            self.assertEqual(len(load_library(root/'lib')['videos']),1)
