import helpers
import contextlib
import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from channelrecap import cli, tools
from channelrecap.errors import InputError, InvalidTextError, display_source
from channelrecap.transcript import Transcript, Segment, atomic_json, write_data, read_data


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'talk.vtt'
        self.source.write_text('WEBVTT\n\n00:00:00 --> 00:00:06\nAmber lanterns shine above quiet valleys.\n', encoding='utf-8')
        self.manifest = self.root / 'videos.csv'
        self.out = self.root / 'library'
        self.manifest_row()

    def manifest_row(self, **changes):
        row = dict(source='talk.vtt', title='Lantern lesson', language='en', date='2026-01-10', start='', end='', extra='note')
        row.update(changes)
        with self.manifest.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)

    def call(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(map(str, args)), **kwargs)
        self.assertNotIn('Traceback', err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def run_library(self, *args, **kwargs):
        return self.call('run', '--manifest', self.manifest, '--out', self.out, *args, **kwargs)

    def snapshot(self, folder=None):
        folder = folder or self.out
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}

    def test_nul_in_every_manifest_field_is_rejected_before_any_write(self):
        self.assertEqual(self.run_library()[0], 0)
        before = self.snapshot()
        for field in ('source', 'title', 'language', 'date', 'start', 'end', 'extra'):
            with self.subTest(field=field):
                self.manifest_row(**{field: 'bad\0value'})
                code, _, err = self.run_library('--force')
                self.assertEqual(code, 2)
                self.assertIn('row 2', err)
                self.assertIn('NUL', err)
                self.assertEqual(self.snapshot(), before)
                fresh = self.root / 'unused'
                self.assertEqual(self.call('run', '--manifest', self.manifest, '--out', fresh)[0], 2)
                self.assertFalse(fresh.exists())
        self.manifest_row()
        self.assertEqual(self.run_library('--force')[0], 0)
        self.assertEqual(self.call('prepare', '--out', self.out)[0], 0)

    def test_caption_bad_text_preflight_preserves_the_entire_good_library(self):
        self.assertEqual(self.run_library()[0], 0)
        before = self.snapshot()
        text = self.source.read_text(encoding='utf-8')
        bad_inputs = [text.replace('Amber', 'Amber\0').encode(),
                      text.replace('Amber', 'Amber\ud800').encode('utf-8', 'surrogatepass'),
                      text.replace('Amber', 'Amber\udfff').encode('utf_16', 'surrogatepass'),
                      text.encode() + b'\xff', text.encode() + b'\xc0\xaf',
                      text.encode() + b'\xf4\x90\x80\x80', text.encode('utf_16')[:-1],
                      text.encode('utf_16_le')]
        for raw in bad_inputs:
            self.source.write_bytes(raw)
            for command in ('run', 'transcribe'):
                with self.subTest(raw=repr(raw[-12:]), command=command):
                    code, _, err = self.call(command, '--manifest', self.manifest, '--out', self.out, '--force')
                    self.assertEqual(code, 2)
                    self.assertIn('error:', err)
                    self.assertEqual(self.snapshot(), before)
            fresh = self.root / 'unused'
            self.assertEqual(self.call('transcribe', self.source, '--out', fresh)[0], 2)
            self.assertFalse(fresh.exists())
        # A later invalid caption also prevents mutation of an earlier valid row.
        self.source.write_text(text, encoding='utf-8')
        other = self.root / 'other.vtt'
        other.write_bytes(text.encode() + b'\xff')
        self.assertEqual(self.call('run', self.source, other, '--out', self.out, '--force')[0], 2)
        self.assertEqual(self.snapshot(), before)

    def test_bad_argv_and_text_files_are_clean_exit_two(self):
        self.assertEqual(self.run_library()[0], 0)
        before = self.snapshot()
        for bad in ('\0', '\ud800', '\udcff'):
            for flag in ('--language', '--model', '--compute-type', '--out', '--manifest', '--prompt-file', '--fillers-file'):
                with self.subTest(flag=flag, bad=ascii(bad)):
                    code, _, err = self.call('run', self.source, '--out', self.out, flag, 'bad' + bad)
                    self.assertEqual(code, 2)
                    self.assertIn('error:', err)
                    self.assertEqual(self.snapshot(), before)
        for option in ('--prompt-file', '--fillers-file'):
            path = self.root / 'text.txt'
            for raw in (b'bad\0text', b'bad\xfftext', b'bad\xed\xa0\x80'):
                path.write_bytes(raw)
                self.assertEqual(self.run_library('--force', option, path)[0], 2)
                self.assertEqual(self.snapshot(), before)
        report = self.root / 'report.md'
        for raw in (b'bad\0text', b'bad\xfftext'):
            report.write_bytes(raw)
            self.assertEqual(self.call('check-citations', report, '--out', self.out)[0], 2)

    @unittest.skipIf(os.name == 'nt', 'raw argv bytes require POSIX')
    def test_real_invalid_byte_argument_exits_two_without_output(self):
        result = subprocess.run([sys.executable, '-m', 'channelrecap', 'run', str(self.source),
                                 '--language', b'\xff', '--out', str(self.out)], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'valid Unicode', result.stderr)
        self.assertNotIn(b'Traceback', result.stderr)
        self.assertFalse(self.out.exists())

    @unittest.skipIf(os.name == 'nt', 'executable stubs require POSIX')
    def test_invalid_tool_bytes_are_isolated_and_exit_two(self):
        media = self.root / 'broken.wav'
        media.write_text('invented media', encoding='utf-8')
        for target in ('stdout', 'stderr'):
            with self.subTest(target=target):
                directory = self.root / 'bin'
                helpers.make_stub_tool(directory, 'ffmpeg', f'import sys\nsys.{target}.buffer.write(b"bad: \\xff\\n")\nsys.exit(1)\n')
                out_dir = self.root / target
                with patch.dict(os.environ, {'PATH': str(directory)}):
                    code, out, err = self.call('run', media, self.source, '--out', out_dir, transcriber_factory=helpers.FakeTranscriber)
                self.assertEqual(code, 2)
                self.assertIn('done=1 cached=0 failed=1', out)
                self.assertIn('invalid Unicode', err)
                library = json.loads((out_dir / 'library.json').read_text(encoding='utf-8'))
                self.assertEqual([v['status'] for v in library['videos']], ['failed', 'done'])
                self.assertEqual(self.call('prepare', '--out', out_dir)[0], 0)

    def test_all_downloaded_metadata_is_validated_before_download(self):
        good = dict(extractor='demo', id='sample', title='Lantern lesson', upload_date='20260110')
        for field in ('title', 'upload_date', 'extra', 'id', 'extractor'):
            for bad in ('bad\0text', 'bad\ud800'):
                with self.subTest(field=field, bad=ascii(bad)):
                    data = dict(good, **{field: bad})
                    with patch.object(tools, 'preflight'), patch.object(tools, 'downloader', return_value=['stub']), \
                         patch.object(tools, 'invoke', return_value=json.dumps(data)), patch.object(tools, 'download_audio') as download:
                        code, _, _ = self.call('run', 'https://example.com/watch', self.source, '--out', self.out, '--force', transcriber_factory=helpers.FakeTranscriber)
                    self.assertEqual(code, 2)
                    download.assert_not_called()
                    self.assertEqual(self.call('prepare', '--out', self.out)[0], 0)

    def test_invalid_speech_result_cannot_replace_good_transcripts(self):
        media = self.root / 'talk.wav'
        media.write_text('invented media', encoding='utf-8')
        with patch.object(tools, 'preflight'), patch.object(tools, 'clip_audio',
                side_effect=lambda source, output, *times: helpers.make_wav(output)):
            self.assertEqual(self.call('run', media, '--out', self.out, transcriber_factory=helpers.FakeTranscriber)[0], 0)
            before = self.snapshot()
            valid = helpers.FakeTranscriber().transcribe(media)
            bad_values = []
            for field, value in (('language', 'en\0'), ('duration', -1), ('duration', float('nan')), ('duration', 'bad'),
                                 ('language_probability', 2), ('offset', -1), ('schema_version', 2)):
                transcript = copy.deepcopy(valid)
                setattr(transcript, field, value)
                bad_values.append(transcript)
            for value in ('bad\0speech', 'bad\ud800speech'):
                transcript = copy.deepcopy(valid)
                transcript.segments[0].text = value
                bad_values.append(transcript)
            for transcript in bad_values:
                with self.subTest(transcript=repr(transcript)):
                    with patch.object(helpers.FakeTranscriber, 'transcribe', return_value=transcript):
                        code, _, err = self.call('run', media, '--out', self.out, '--force', transcriber_factory=helpers.FakeTranscriber)
                    self.assertEqual(code, 2, err)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(self.call('prepare', '--out', self.out)[0], 0)

    def test_writer_and_reader_share_validation(self):
        path = self.root / 'data.json'
        good = Transcript(segments=[Segment(0, 6, 'Amber lanterns shine above quiet valleys.')], duration=6)
        write_data(path, good)
        saved = path.read_bytes()
        self.assertEqual(read_data(path), good)
        for change in (dict(duration=-1), dict(language='en\0'), dict(language='\ud800'), dict(offset=float('inf'))):
            bad = copy.deepcopy(good)
            for key, value in change.items():
                setattr(bad, key, value)
            with self.assertRaises(InputError):
                write_data(path, bad)
            self.assertEqual(path.read_bytes(), saved)
        for value in ({'retained': 'bad\0text'}, {'retained': '\ud800'}, {'duration': float('nan')}):
            absent = self.root / 'unused' / 'bad.json'
            with self.assertRaises(InputError):
                atomic_json(absent, value)
            self.assertFalse(absent.parent.exists())

    def test_damaged_library_has_a_preserving_recovery_path(self):
        self.assertEqual(self.run_library()[0], 0)
        index = self.out / 'library.json'
        good = json.loads(index.read_text(encoding='utf-8'))
        for field in ('title', 'language'):
            bad = copy.deepcopy(good)
            bad['videos'][0][field] += '\0'
            index.write_text(json.dumps(bad), encoding='utf-8')
            damaged = self.snapshot()
            code, _, err = self.run_library('--force')
            self.assertEqual(code, 2)
            self.assertIn('new --out folder', err)
            self.assertIn('backup', err)
            self.assertEqual(self.snapshot(), damaged)
            repaired = self.root / ('repaired-' + field)
            self.assertEqual(self.call('run', '--manifest', self.manifest, '--out', repaired)[0], 0)
            self.assertEqual(self.call('prepare', '--out', repaired)[0], 0)
            self.assertEqual(self.snapshot(), damaged)
            self.assertEqual(json.loads((repaired / 'library.json').read_text(encoding='utf-8'))['videos'][0]['status'], 'done')
        index.write_text(json.dumps(good), encoding='utf-8')
        path = self.out / good['videos'][0]['data']
        data = json.loads(path.read_text(encoding='utf-8'))
        data['segments'][0]['text'] += '\0'
        path.write_text(json.dumps(data), encoding='utf-8')
        damaged = self.snapshot()
        code, _, err = self.run_library('--force')
        self.assertEqual(code, 2)
        self.assertIn('new --out folder', err)
        repaired = self.root / 'repaired-data'
        self.assertEqual(self.call('run', '--manifest', self.manifest, '--out', repaired)[0], 0)
        self.assertEqual(self.call('prepare', '--out', repaired)[0], 0)
        self.assertEqual(self.snapshot(), damaged)

    def test_home_relative_sources_do_not_leak_in_shared_files(self):
        # User-home lookup is mocked rather than changing the process HOME.
        nested = self.root / 'room' / 'sub' / 'talk.vtt'
        nested.parent.mkdir(parents=True)
        nested.write_text(self.source.read_text(encoding='utf-8'), encoding='utf-8')
        source = '~/room/sub/talk.vtt'
        original = Path.expanduser
        with patch.object(Path, 'expanduser', lambda p: nested if p == Path(source) else original(p)):
            self.assertEqual(self.call('run', source, '--out', self.out)[0], 0)
            library = json.loads((self.out / 'library.json').read_text(encoding='utf-8'))
            entry = library['videos'][0]
            self.assertEqual(entry['source'], source)
            text = (self.out / entry['transcript']).read_text(encoding='utf-8')
            self.assertIn('Source: talk.vtt', text)
            self.assertNotIn('room/sub', text)
            nested.write_text('WEBVTT\n', encoding='utf-8')
            self.assertEqual(self.call('run', source, '--out', self.out, '--force')[0], 1)
        self.assertNotIn('room/sub', (self.out / 'summary.md').read_text(encoding='utf-8'))
        for value in ('~/room/sub/talk.vtt', '~' + 'someone/room/sub/talk.vtt', r'~\room\sub\talk.vtt',
                      '/var/room/sub/talk.vtt', r'C:\room\sub\talk.vtt', 'file:///var/room/sub/talk.vtt'):
            self.assertEqual(display_source(value), 'talk.vtt')
