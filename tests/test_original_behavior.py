"""Behavior pins established against the unchanged imported pipeline."""
import helpers
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from channelrecap import tools, pipeline
from channelrecap.manifest import read_manifest, slug, source_key
from channelrecap.timecodes import parse_time, format_time
from channelrecap.transcriber import Settings, WhisperTranscriber, transcribe_audio
from channelrecap.transcript import Segment, Transcript
from channelrecap.errors import RowError
from channelrecap.errors import InputError


class OriginalBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_timecodes(self):
        """P1; D3/D12: strict input errors preserve base sixty values."""
        for text, expected in [('90',90),('01:30',90),('01:02:03.5',3723.5)]:
            self.assertEqual(parse_time(text), expected)
        for text in ['words','','-1','1:-2','1:2:3:4']:
            with self.subTest(text=text), self.assertRaises(InputError):
                parse_time(text)

    def manifest(self, text):
        path = self.root/'input.csv'
        path.write_text(text, encoding='utf-8-sig')
        return path

    def test_manifest_bom_blank_and_duration(self):
        """P2: BOM, blank source rows, and duration."""
        entries = read_manifest(self.manifest('url,start,end\n,,\nhttps://example.com/a,2,9\n'))
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0].end - entries[0].start,7)

    def test_manifest_errors(self):
        """P2; D2/D12: validated inputs report row numbers and empty input."""
        for text, message in [('url,start,end\n,,\nhttps://example.com/a,bad,9\n','row 3'),
                              ('url,start,end\nhttps://example.com/a,9,9\n','row 2'),
                              ('url,start,end\n,,\n','No usable rows')]:
            with self.subTest(text=text), self.assertRaisesRegex(InputError,message):
                read_manifest(self.manifest(text))

    def test_slug_and_key(self):
        """P3/P4; D4: source-range keys use the public digest format."""
        self.assertEqual(slug('  Coffee & WATER! '),'coffee-water')
        first = source_key('https://example.com/a',1,9)
        self.assertEqual(first,source_key('https://example.com/a',1,9))
        for source,start,end in [('https://example.com/b',1,9),('https://example.com/a',2,9),('https://example.com/a',1,10)]:
            self.assertNotEqual(first,source_key(source,start,end))

    def test_clip_arguments(self):
        """P5; D2/D12: optional range interface and checked tool wrapper retain exact flags."""
        source,dest = self.root/'source.wav',self.root/'clip.wav'
        with patch.object(tools,'invoke',side_effect=lambda *a,**k: helpers.make_wav(dest)) as call:
            tools.clip_audio(source,dest,2.1254,2.1254 + 6.5004)
        self.assertEqual(call.call_args.args[0],['ffmpeg','-hide_banner','-loglevel','error','-y',
            '-ss','2.125','-i',str(source),'-t','6.500','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(dest)])

    def test_download_arguments_and_cache(self):
        """P6; D7: exact download flags with extractor-prefixed cache names."""
        for suffix in ['json','part','vtt','srt']:
            (self.root/f'demo_sample.{suffix}').write_text('side file', encoding='utf-8')
        audio = self.root/'demo_sample.m4a'
        def download(*args, **kwargs):
            audio.write_text('audio double', encoding='utf-8')
        with patch.object(tools,'downloader',return_value=['yt-dlp']), patch.object(tools,'invoke',side_effect=download) as call:
            self.assertEqual(tools.download_audio('https://example.com/a',{'extractor':'demo','id':'sample'},self.root),audio)
            self.assertEqual(tools.download_audio('https://example.com/a',{'extractor':'demo','id':'sample'},self.root),audio)
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.args[0],['yt-dlp','--no-playlist','--no-overwrites','--format',
            'bestaudio/best','--output',str(self.root/'%(extractor)s_%(id)s.%(ext)s'),'https://example.com/a'])

    def test_model_constructor(self):
        """P7: GPU constructor loads the resolved local cache with the selected precision."""
        constructor = Mock()
        with patch.dict(sys.modules, {'faster_whisper':SimpleNamespace(WhisperModel=constructor, download_model=Mock(return_value=helpers.CACHED_MODEL))}):
            WhisperTranscriber(Settings(model='large-v3',device='cuda'))
        constructor.assert_called_once_with(helpers.CACHED_MODEL,device='cuda',compute_type='float16',local_files_only=True)

    def test_transcribe_call_and_mapping(self):
        """P8/P9: exact call, null word times and probability precision."""
        words = [SimpleNamespace(start=None,end=None,word=' Um',probability=.81234567),
                 SimpleNamespace(start=.123456,end=.678912,word=' coffee',probability=.999999)]
        segment = SimpleNamespace(start=.123456,end=1.987654,text='  Um coffee  ',words=words)
        info = SimpleNamespace(language='en',language_probability=.91234567,duration=2.123456)
        model = Mock()
        model.transcribe.return_value = (iter([segment]),info)
        output = transcribe_audio(model,self.root/'clip.wav',language='',initial_prompt=None)
        model.transcribe.assert_called_once_with(str(self.root/'clip.wav'),language=None,beam_size=5,
                                                vad_filter=True,word_timestamps=True,initial_prompt=None)
        self.assertEqual(output,{'language':'en','language_probability':.91235,'duration':2.123,
            'segments':[{'start':.123,'end':1.988,'text':'Um coffee','words':[
                {'start':None,'end':None,'word':' Um','probability':.81235},
                {'start':.123,'end':.679,'word':' coffee','probability':1.0}]}]})

    def test_empty_prompt(self):
        """P8: empty prompts have no effect."""
        prompt = self.root/'prompt.txt'
        prompt.write_text('  \n', encoding='utf-8')
        self.assertIsNone(Settings(prompt=prompt.read_text(encoding='utf-8')).initial_prompt)
        self.assertIsNone(Settings().initial_prompt)

    def options(self, **changes):
        return pipeline.RunOptions(**changes)

    def run_batch(self, options, failed=False):
        rows = read_manifest(self.manifest('url,start,end,title\nhttps://example.com/a,0,4,First\nhttps://example.com/a,4,8,Second\n'))
        def result(*args, **kwargs):
            return Transcript(language='en',language_probability=1.0,duration=4.0,
                              segments=[Segment(0,4,'Coffee water is clear.')])
        def download(*args):
            path = self.root/'source.wav'
            path.write_text('audio double', encoding='utf-8')
            return path
        engine = Mock(spec=['transcribe'])
        engine.transcribe.side_effect = [RowError('synthetic failure'),result()] if failed else result
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            stack.enter_context(patch.object(tools,'preflight'))
            metadata = stack.enter_context(patch.object(tools,'link_metadata',return_value={'extractor':'demo','id':'sample','date':''}))
            audio = stack.enter_context(patch.object(tools,'download_audio',side_effect=download))
            clip = stack.enter_context(patch.object(tools,'clip_audio'))
            model = Mock(return_value=engine)
            pipeline.transcribe(rows,self.root/'out',options,transcriber_factory=model)
        entries = pipeline.load_library(self.root/'out')['videos']
        return entries, (metadata.call_count,audio.call_count,clip.call_count,model.call_count,engine.transcribe.call_count)

    def test_lazy_once_resume_and_force(self):
        """P7/P10; D5: library status replaces per-video state; same lazy/resume behavior."""
        result,calls = self.run_batch(self.options())
        self.assertEqual([x['status'] for x in result],['done','done'])
        self.assertEqual(calls,(2,2,2,1,2))
        result,calls = self.run_batch(self.options())
        self.assertEqual(calls,(0,0,0,0,0))
        result,calls = self.run_batch(self.options(force=True))
        self.assertEqual(calls,(2,2,2,1,2))

    def test_failure_isolation(self):
        """P11; D5/D12: library errors isolate rows without tracebacks."""
        result,calls = self.run_batch(self.options(),failed=True)
        self.assertEqual([x['status'] for x in result],['failed','done'])
        self.assertEqual(calls[-1],2)

    def test_site_captions_arguments_and_failure(self):
        """P12; D8/D12: optional reference folder and safe tool-error boundary."""
        folder = self.root/'site-captions/sample'
        with patch.object(tools,'downloader',return_value=['yt-dlp']), patch.object(tools,'invoke',side_effect=RowError('synthetic failure')) as call:
            tools.save_site_captions('https://example.com/a',folder,'fr')
        self.assertEqual(call.call_args.args[0],['yt-dlp','--no-playlist','--skip-download','--write-subs',
            '--write-auto-subs','--sub-langs','fr.*,fr','--sub-format','vtt','--output',
            str(folder/'%(id)s.%(ext)s'),'https://example.com/a'])
        self.assertFalse(folder.exists())

    def test_display_time(self):
        """P13: whole seconds are floored."""
        self.assertEqual(format_time(3723.999),'01:02:03')


if __name__ == '__main__':
    unittest.main()
