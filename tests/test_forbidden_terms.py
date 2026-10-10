import helpers
import io
import json
import os
from pathlib import Path
import re
import subprocess
import unittest


def load_terms(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding='utf-8')
        if path.suffix.lower() == '.json' or text.lstrip().startswith(('[','{')):
            value = json.loads(text)
            if isinstance(value, list):
                terms = value
            elif isinstance(value, dict) and 'terms' in value:
                terms = value['terms']
            elif isinstance(value, dict) and isinstance(value.get('categories'), dict):
                terms = []
                for group in value['categories'].values():
                    terms.extend(group if isinstance(group, list) else group['terms'])
            else:
                raise ValueError
            if not isinstance(terms, list) or any(not isinstance(t, str) for t in terms):
                raise ValueError
        else:
            terms = [line.split('#',1)[0].strip() for line in text.splitlines()]
        return [term.strip() for term in terms if term.strip()]
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise AssertionError('Terms file could not be read or has an unsupported format.') from None


def repository_files() -> list[Path]:
    root = helpers.ROOT
    result = subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],cwd=root,capture_output=True)
    if result.returncode == 0:
        paths = [root / name.decode() for name in set(result.stdout.split(b'\0')) if name]
        return paths
    skipped = {'.git','.venv','venv','__pycache__','build','dist','scratch'}
    return [p for p in root.rglob('*') if p.is_file() and not skipped.intersection(p.relative_to(root).parts)]


def git(*args, **kwargs):
    return subprocess.run(['git',*args],cwd=helpers.ROOT,capture_output=True,check=True,**kwargs).stdout


def history_objects():
    """Read every reachable object once; retain all historical blob path names."""
    objects = git('rev-list','--all','--objects').decode().splitlines()
    ids = [line.split(' ',1)[0] for line in objects]
    stream = io.BytesIO(git('cat-file','--batch',input=('\n'.join(ids)+'\n').encode()))
    blobs, messages = {}, {}
    for expected in ids:
        oid, kind, size = stream.readline().decode().split()
        if oid != expected:
            raise AssertionError('Unexpected history object')
        data = stream.read(int(size)).decode('utf-8',errors='replace')
        if stream.read(1) != b'\n':
            raise AssertionError('Incomplete history object')
        if kind == 'blob':
            blobs[oid] = data
        elif kind == 'commit':
            messages[oid] = data.split('\n\n',1)[1]
    paths = {}
    for commit in git('rev-list','--all').decode().splitlines():
        for record in git('ls-tree','-r','-z',commit).split(b'\0'):
            if not record:
                continue
            metadata, name = record.decode().split('\t',1)
            mode, kind, oid = metadata.split()
            if kind == 'blob':
                paths.setdefault((oid,name),commit[:8])
    return blobs, messages, paths


class ForbiddenTermsTests(unittest.TestCase):
    def test_configured_terms(self) -> None:
        setting = os.environ.get('FORBIDDEN_TERMS_FILE','')
        if not setting:
            self.skipTest('FORBIDDEN_TERMS_FILE not set')
        terms = []
        for name in setting.split(os.pathsep):
            path = Path(name).expanduser().resolve()
            self.assertFalse(path.is_relative_to(helpers.ROOT.resolve()),'Terms files must be outside the repository.')
            terms.extend(load_terms(path))
        patterns = []
        for term in terms:
            escaped = re.escape(term)
            if len(term) <= 3 and term.isalnum():
                escaped = r'(?<![^\W_])' + escaped + r'(?![^\W_])'
            patterns.append(re.compile(escaped,re.IGNORECASE))
        files = repository_files()
        hits = []
        def scan(label, text, path=''):
            for number, line in enumerate([path] + text.splitlines()):
                for index, pattern in enumerate(patterns,1):
                    if pattern.search(line):
                        hits.append(f'{label}:{max(1,number)} term #{index}')
        for path in sorted(files):
            relative = path.relative_to(helpers.ROOT).as_posix()
            text = path.read_bytes().decode('utf-8',errors='replace')
            scan(relative,text,relative)
        blobs, messages, paths = history_objects()
        covered = set()
        for (oid, name), commit in paths.items():
            scan(f'{commit}:{name}',blobs[oid],name)
            covered.add(oid)
        for oid in blobs.keys() - covered:
            scan(f'object-{oid[:8]}',blobs[oid])
        for commit, message in messages.items():
            scan(f'{commit[:8]}:commit-message',message)
        print(f'scanned files={len(files)} history_blobs={len(blobs)} commits={len(messages)} hits={len(hits)}')
        self.assertFalse(hits, '\n'.join(hits))
