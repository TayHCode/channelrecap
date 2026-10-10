import helpers
from pathlib import Path
import re
import subprocess
import unittest


class HygieneTests(unittest.TestCase):
    def test_repository_files(self) -> None:
        result = subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],cwd=helpers.ROOT,capture_output=True)
        if result.returncode:
            self.skipTest('Not a git checkout')
        forbidden = {'.png','.jpg','.jpeg','.gif','.webp','.svg','.pdf','.mp4','.mov','.wav','.mp3','.heic','.zip','.gz','.tar','.7z','.exe','.dll','.so','.sqlite','.db','.bin'}
        email = re.compile(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}',re.IGNORECASE)
        home = re.compile('/' + '(?:home|Users)' + r'/[^/\s]+|[A-Za-z]:' + r'\\Users\\[^\\\s]+')
        machine = re.compile(r'(?<![\w.-])[a-z][a-z-]*-\d{2}(?![\w.-]|\()', re.IGNORECASE)
        parts = {'token','tokens','credentials'}
        errors = []
        extra_patterns = [
            ('network address', re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')),
            ('host suffix', re.compile(r'\b[\w-]+\.(?:lo' + r'cal|ho' + r'me|inter' + r'nal|la' + r'n|co' + r'rp)\b(?!\()')),
            ('secret assignment', re.compile(r'(?i)\b(?:pass' + r'word|api' + r'_key)\s*[:=]\s*\S+|\bBea' + r'rer\s+[A-Za-z0-9._-]+')),
            ('restricted link', re.compile(r'https?://[^\s/]+/[^\s]*pri' + r'vate',re.I)),
            ('phone shape', re.compile(r'(?<!\w)\+\d{1,3}[ -]\d{2,4}[ -]\d{3,4}[ -]\d{3,4}(?!\w)')),
        ]
        index = subprocess.run(['git','ls-files','--stage'],cwd=helpers.ROOT,capture_output=True,text=True,check=True).stdout
        if any(line.startswith('160000 ') for line in index.splitlines()):
            errors.append('repository: submodule')
        for raw in sorted(set(result.stdout.split(b'\0'))):
            if not raw:
                continue
            name = raw.decode()
            path = helpers.ROOT / name
            if path.suffix.lower() == '.sh' and name != 'scripts/run_gpu.sh':
                errors.append(name + ': unexpected shell file')
            if {'vendor','third_party'}.intersection(Path(name).parts):
                errors.append(name + ': bundled code folder')
            if path.is_symlink():
                errors.append(name + ': symlink')
                continue
            content = path.read_bytes()
            if path.suffix.lower() in forbidden or b'\0' in content or len(content) > 200_000:
                errors.append(name + ': file type or size')
            if parts.intersection(re.split(r'[^a-zA-Z0-9]+',path.name.lower())):
                errors.append(name + ': filename')
            text = content.decode('utf-8',errors='replace')
            if text.startswith('version https://git-lfs.github.com/spec/'):
                errors.append(name + ': large-file pointer')
            for match in re.finditer(r'\b[A-Za-z0-9_+/=-]{32,}\b',text):
                value = match.group()
                if (re.search('[a-z]',value) and re.search('[A-Z]',value) and re.search('[0-9]',value)
                        and not re.fullmatch('[a-fA-F0-9]+',value)):
                    errors.append(f'{name}:{text[:match.start()].count(chr(10))+1}: opaque value')
            for label, pattern in [('email',email),('home path',home),('machine pattern',machine),*extra_patterns]:
                for match in pattern.finditer(text):
                    line = text[:match.start()].count('\n') + 1
                    errors.append(f'{name}:{line}: {label}')
        self.assertFalse(errors,'\n'.join(errors))
