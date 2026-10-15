import helpers
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from channelrecap import NAME, __version__, cli, tools
from channelrecap.transcript import atomic_json


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'lib'

    def call(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(args),**kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_unicode_captions_titles_and_paths_prepare_successfully(self) -> None:
        import csv
        from channelrecap.transcript import read_data
        values = ('İstanbul', 'Cafe\u0301', 'שלום', '🫖', '\ufeff', r'\ud800')
        for position, value in enumerate(values):
            with self.subTest(kind=position):
                source = self.root / (value.replace(chr(92), '_') + '.vtt')
                speech = value + ' how do amber lanterns glow tonight'
                source.write_text('WEBVTT\n\n00:00 --> 00:03\n' + speech + '\n', encoding='utf-8-sig')
                manifest = self.root / 'unicode.csv'
                with manifest.open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(['source', 'title'])
                    writer.writerow([source.name, value + ' lantern lesson'])
                out_dir = self.root / (value.replace(chr(92), '_') + ' library')
                code, _, err = self.call('run', '--manifest', str(manifest), '--out', str(out_dir))
                self.assertEqual(code, 0, err)
                self.assertEqual(self.call('prepare', '--out', str(out_dir))[0], 0)
                entry = json.loads((out_dir / 'library.json').read_text(encoding='utf-8'))['videos'][0]
                self.assertEqual(entry['title'], value + ' lantern lesson')
                self.assertEqual(read_data(out_dir / entry['data']).segments[0].text, speech)
                self.assertIn('how do amber lanterns glow tonight', (out_dir / 'summary.md').read_text(encoding='utf-8'))
                self.assertIn(value + ' lantern lesson', (out_dir / entry['transcript']).read_text(encoding='utf-8'))

    def test_lone_surrogates_fail_cleanly_before_writing_outputs(self) -> None:
        for value in ('\ud800', '\udcff'):
            with self.subTest(value=ascii(value)):
                code, _, err = self.call('run', 'bad' + value + '.vtt', '--out', str(self.out))
                self.assertEqual(code, 2)
                self.assertIn('valid Unicode', err)
                self.assertFalse(self.out.exists())
                manifest = self.root / 'bad.csv'
                manifest.write_bytes(('source,title\ntalk.vtt,bad' + value).encode('utf-8', errors='surrogatepass'))
                self.assertEqual(self.call('run', '--manifest', str(manifest), '--out', str(self.out))[0], 2)
                self.assertEqual(self.call('prepare', '--out', str(self.out) + value)[0], 2)
                metadata = json.dumps({'extractor': 'demo', 'id': 'talk', 'title': value})
                with patch.object(tools, 'downloader', return_value=['stub']), patch.object(tools, 'invoke', return_value=metadata):
                    with self.assertRaisesRegex(tools.RowError, 'invalid Unicode'):
                        tools.link_metadata('https://example.com/watch')

    def test_version_module_and_default_list(self) -> None:
        self.assertEqual(self.call('--version')[:2], (0,f'{NAME} {__version__}\n'))
        result = subprocess.run([sys.executable,'-m',NAME,'--version'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertEqual(result.stdout,f'{NAME} {__version__}\n')
        code, out, _ = self.call('fillers','--print-default-list')
        self.assertEqual(code,0)
        self.assertIn('~^so',out)

    def test_help_names_and_options_match_commands(self) -> None:
        for command in ('run', 'transcribe', 'prepare', 'fillers', 'check-citations', 'doctor'):
            code, out, _ = self.call(command, '--help')
            self.assertEqual(code, 0)
            self.assertIn(f'usage: {NAME} {command}', out)
        self.assertNotIn('--fillers-file', self.call('transcribe', '--help')[1])
        self.assertEqual(self.call('transcribe', '--fillers-file', 'list.txt')[0], 2)
        self.assertIn('Maximum pending rows', self.call('run', '--help')[1])
        self.assertIn('Check optional tools', self.call('--help')[1])

    def test_demo_run_stdout_files_and_no_optional_imports(self) -> None:
        code, out, _ = self.call('run','--manifest',str(helpers.ROOT / 'examples/demo/videos.csv'),'--out',str(self.out))
        self.assertEqual(code,0)
        self.assertEqual(out.splitlines(),[f'done=3 cached=0 failed=0 prepared=3 out={self.out}'])
        for name in ['summary.md','AGENT_PROMPT.md','AGENTS.md','CLAUDE.md','library.json','fillers.csv']:
            self.assertTrue((self.out / name).is_file())
        self.assertEqual(len(list((self.out / 'transcripts').glob('*.txt'))),3)
        code, out, _ = self.call('fillers','--out',str(self.out))
        self.assertEqual((code,out.strip().splitlines()[-1]),(0,f"fillers videos=3 csv={self.out / 'fillers.csv'}"))
        self.assertNotIn('faster_whisper',sys.modules)
        self.assertNotIn('yt_dlp',sys.modules)

    def test_bad_usage_and_no_finished_data(self) -> None:
        self.assertEqual(self.call('run')[0],2)
        self.assertEqual(self.call('run','missing.wav','--manifest','missing.csv')[0],2)
        self.assertEqual(self.call('run','missing.wav')[0],2)
        self.assertEqual(self.call('prepare','--out',str(self.out))[0],2)
        self.assertEqual(self.call('check-citations','missing.md','--out',str(self.out))[0],2)
        self.assertFalse(self.out.exists())
        atomic_json(self.out / 'library.json',dict(schema_version=1,videos=[]))
        self.assertEqual(self.call('prepare','--out',str(self.out))[0],1)
        self.assertEqual(self.call('fillers','--out',str(self.out))[0],1)

    def test_invalid_paths_are_clear_usage_errors_without_outputs(self) -> None:
        for source in ('bad\x00.vtt', '~' + 'no_such_caption_user/x.vtt'):
            with self.subTest(source=source):
                code, _, err = self.call('run', source, '--out', str(self.out))
                self.assertEqual(code, 2)
                self.assertIn('error:', err)
                self.assertNotIn('Traceback', err)
                self.assertFalse(self.out.exists())
        self.out.write_text('keep this file', encoding='utf-8')
        for output in (self.out, self.out / 'child'):
            code, _, err = self.call('run', '--manifest', str(helpers.ROOT / 'examples/demo/videos.csv'), '--out', str(output))
            self.assertEqual(code, 2)
            self.assertIn('--out must be a folder', err)
        self.assertEqual(self.out.read_text(encoding='utf-8'), 'keep this file')

    def test_bad_library_shapes_and_missing_fields_are_usage_errors(self) -> None:
        self.call('run', '--manifest', str(helpers.ROOT / 'examples/demo/videos.csv'), '--out', str(self.out))
        path = self.out / 'library.json'
        good = json.loads(path.read_text(encoding='utf-8'))
        missing_date = json.loads(path.read_text(encoding='utf-8'))
        del missing_date['videos'][0]['date']
        bad_order = json.loads(path.read_text(encoding='utf-8'))
        bad_order['videos'][0]['order'] = []
        bad_data = json.loads(path.read_text(encoding='utf-8'))
        bad_data['videos'][0]['data'] = None
        for value in ([], {'schema_version': 1, 'videos': [None]}, missing_date, bad_order, bad_data):
            for command in ('prepare', 'fillers', 'check-citations', 'run'):
                with self.subTest(value=type(value).__name__, command=command):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    args = [command, '--out', str(self.out)]
                    if command == 'check-citations':
                        args.append(str(helpers.ROOT / 'examples/demo/analysis-example.md'))
                    elif command == 'run':
                        args += ['--manifest', str(helpers.ROOT / 'examples/demo/videos.csv')]
                    code, _, err = self.call(*args)
                    self.assertEqual(code, 2)
                    self.assertIn('error:', err)
                    self.assertNotIn('Traceback', err)
        path.write_text(json.dumps(good), encoding='utf-8')
        (self.out / good['videos'][0]['data']).write_text('[]', encoding='utf-8')
        self.assertEqual(self.call('prepare', '--out', str(self.out))[0], 2)

    def test_invalid_transcript_fields_leave_derived_files_unchanged(self) -> None:
        self.assertEqual(self.call('run', '--manifest', str(helpers.ROOT / 'examples/demo/videos.csv'), '--out', str(self.out))[0], 0)
        library = json.loads((self.out / 'library.json').read_text(encoding='utf-8'))
        path = self.out / library['videos'][-1]['data']
        good = json.loads(path.read_text(encoding='utf-8'))
        preserved = {p: p.read_bytes() for p in self.out.iterdir() if p.is_file()}
        for field, values in (('duration', ['2', None, -1, float('nan'), float('inf'), True]),
                              ('offset', ['2', None, -1]), ('language_probability', [2, 'high']),
                              ('language', [[], '\ud800']), ('video_id', [None])):
            for value in values:
                with self.subTest(field=field, value=repr(value)):
                    path.write_text(json.dumps({**good, field: value}), encoding='utf-8')
                    for command in ('prepare', 'fillers'):
                        code, _, err = self.call(command, '--out', str(self.out))
                        self.assertEqual(code, 2)
                        self.assertIn('error: Cannot read transcript data', err)
                        self.assertNotIn('Traceback', err)
                        self.assertEqual({p: p.read_bytes() for p in preserved}, preserved)

    def test_escaped_surrogates_in_stored_titles_and_paths_are_usage_errors(self) -> None:
        self.assertEqual(self.call('run', '--manifest', str(helpers.ROOT / 'examples/demo/videos.csv'), '--out', str(self.out))[0], 0)
        path = self.out / 'library.json'
        good = json.loads(path.read_text(encoding='utf-8'))
        summary = (self.out / 'summary.md').read_bytes()
        csv = (self.out / 'fillers.csv').read_bytes()
        for field in ('title', 'source', 'transcript', 'data', 'language', 'video_id'):
            with self.subTest(field=field):
                data = json.loads(json.dumps(good))
                data['videos'][0][field] += '\udcff'
                path.write_text(json.dumps(data), encoding='utf-8')
                index = path.read_bytes()
                for command in ('prepare', 'fillers'):
                    code, _, err = self.call(command, '--out', str(self.out))
                    self.assertEqual(code, 2)
                    self.assertIn('error: Cannot read library.json', err)
                self.assertEqual((self.out / 'summary.md').read_bytes(), summary)
                self.assertEqual((self.out / 'fillers.csv').read_bytes(), csv)
                self.assertEqual(path.read_bytes(), index)

    def test_bad_remote_title_fails_only_its_row(self) -> None:
        source = self.root / 'talk.vtt'
        source.write_text('WEBVTT\n\n00:00 --> 00:03\nA paper filter holds coffee.\n', encoding='utf-8')
        metadata = json.dumps({'extractor': 'demo', 'id': 'talk', 'title': ['invalid']})
        with patch.object(tools, 'preflight'), patch.object(tools, 'downloader', return_value=['stub']), patch.object(tools, 'invoke', return_value=metadata):
            code, out, err = self.call('run', 'https://example.com/watch', str(source), '--out', str(self.out), transcriber_factory=helpers.FakeTranscriber)
        self.assertEqual(code, 1)
        self.assertIn('done=1 cached=0 failed=1', out)
        self.assertIn('title must be text', err)
        self.assertNotIn('Traceback', err)

    def test_decode_failure_does_not_put_machine_paths_in_shared_files(self) -> None:
        source = self.root / 'folder with spaces' / 'broken.wav'
        source.parent.mkdir()
        source.write_text('invented invalid audio', encoding='utf-8')
        caption = self.root / 'talk.vtt'
        caption.write_text('WEBVTT\n\n00:00 --> 00:03\nA paper filter holds coffee.\n', encoding='utf-8')
        def failed(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', f'Cannot decode "{source}"; output "{args[-1]}"')
        with patch.object(tools, 'preflight'), patch.object(tools.subprocess, 'run', side_effect=failed):
            code, _, _ = self.call('run', str(source), str(caption), '--out', str(self.out), transcriber_factory=helpers.FakeTranscriber)
        self.assertEqual(code, 5)
        library = json.loads((self.out / 'library.json').read_text(encoding='utf-8'))
        self.assertNotIn(str(self.root), library['videos'][0]['error'])
        summary = (self.out / 'summary.md').read_text(encoding='utf-8')
        self.assertIn('broken.wav', summary)
        self.assertNotIn(str(self.root), summary)
        speech = (self.out / library['videos'][1]['transcript']).read_text(encoding='utf-8')
        self.assertIn('Source: talk.vtt', speech)
        self.assertNotIn(str(self.root), speech)

        library['videos'][0]['error'] = f'Cannot decode "{source}"'
        atomic_json(self.out / 'library.json', library)
        self.assertEqual(self.call('prepare', '--out', str(self.out))[0], 0)
        self.assertNotIn(str(self.root), (self.out / 'summary.md').read_text(encoding='utf-8'))
        for detail in ('"/var/quiet room/hidden take.wav"', 'file:///var/quiet%20room/hidden.wav',
                       r'C:\Quiet Room\hidden.wav', '\\\\' + 'example.com' + r'\shared room\hidden.wav',
                       'file://desk/shared%20room/hidden.wav'):
            with self.subTest(detail=detail):
                library['videos'][0]['error'] = 'Cannot decode ' + detail
                atomic_json(self.out / 'library.json', library)
                index = (self.out / 'library.json').read_bytes()
                self.assertEqual(self.call('prepare', '--out', str(self.out))[0], 0)
                summary = (self.out / 'summary.md').read_text(encoding='utf-8')
                self.assertIn('local path details omitted', summary)
                self.assertNotIn('hidden', summary)
                self.assertEqual((self.out / 'library.json').read_bytes(), index)

    def test_failures_and_preflight(self) -> None:
        empty = self.root / 'empty.vtt'
        empty.write_text('WEBVTT\n', encoding='utf-8')
        self.assertEqual(self.call('transcribe',str(empty),'--out',str(self.out))[0],1)
        media = self.root / 'talk.wav'
        media.write_text('placeholder', encoding='utf-8')
        with patch.object(tools.shutil,'which',return_value=None),patch.object(tools,'has_module',return_value=False):
            code, _, err = self.call('transcribe',str(media),'--out',str(self.root / 'absent'))
            self.assertEqual(code,3)
            self.assertIn('ffmpeg',err)
            self.assertIn('faster-whisper',err)
            self.assertEqual(self.call('doctor')[0],3)
        with patch.object(tools.shutil,'which',return_value='stub'),patch.object(tools,'has_module',return_value=True),patch.object(tools,'check_audio_decode'):
            self.assertEqual(self.call('doctor')[0],0)

    def test_all_failed_force_refreshes_reports_and_preserves_transcripts(self) -> None:
        sources = [self.root / name for name in ('first.vtt', 'second.vtt')]
        for source in sources:
            source.write_text('WEBVTT\n\n00:00 --> 00:03\nA paper filter holds coffee.\n', encoding='utf-8')
        args = ['run', *map(str, sources), '--out', str(self.out)]
        self.assertEqual(self.call(*args)[0], 0)
        old_summary = (self.out / 'summary.md').read_bytes()
        old_csv = (self.out / 'fillers.csv').read_bytes()
        preserved = {p: p.read_bytes() for folder in ('transcripts', 'data') for p in (self.out / folder).iterdir()}
        pointer = self.out / 'AGENTS.md'
        pointer.write_text('User notes to preserve.\n', encoding='utf-8')
        for source in sources:
            source.write_text('WEBVTT\n', encoding='utf-8')
        code, out, _ = self.call(*args, '--force')
        self.assertEqual(code, 1)
        self.assertIn('done=0 cached=0 failed=2', out)
        self.assertIn('prepared=0', out)
        self.assertEqual([e['status'] for e in json.loads((self.out / 'library.json').read_text(encoding='utf-8'))['videos']], ['failed', 'failed'])
        summary = (self.out / 'summary.md').read_text(encoding='utf-8')
        self.assertNotEqual(summary.encode(), old_summary)
        self.assertIn('from 0 videos', summary)
        self.assertIn('No finished videos are available', summary)
        self.assertIn('## Not included', summary)
        self.assertEqual(summary.count(': failed:'), 2)
        self.assertEqual(len((self.out / 'fillers.csv').read_text(encoding='utf-8').splitlines()), 1)
        self.assertEqual({p: p.read_bytes() for p in preserved}, preserved)
        self.assertEqual(pointer.read_text(encoding='utf-8'), 'User notes to preserve.\n')

        self.assertEqual(self.call('prepare', '--out', str(self.out))[0], 1)
        (self.out / 'fillers.csv').write_bytes(old_csv)
        self.assertEqual(self.call('fillers', '--out', str(self.out))[0], 1)
        self.assertEqual(len((self.out / 'fillers.csv').read_text(encoding='utf-8').splitlines()), 1)

    @unittest.skipIf(os.name == 'nt','executable stubs require POSIX')
    def test_interrupt_and_full_fake_run(self) -> None:
        directory = helpers.install_media_stubs(self.root)
        media = self.root / 'talk.wav'
        media.write_text('placeholder', encoding='utf-8')
        def interrupt(_):
            raise KeyboardInterrupt
        with patch.dict(os.environ,{'PATH':str(directory),'STUB_LOG':str(self.root / 'calls.txt')}):
            self.assertEqual(self.call('run',str(media),'--out',str(self.out),transcriber_factory=interrupt)[0],130)
            self.assertEqual(self.call('run',str(media),'--out',str(self.out),transcriber_factory=helpers.FakeTranscriber)[0],0)
        self.assertNotIn('faster_whisper',sys.modules)
        self.assertNotIn('yt_dlp',sys.modules)
