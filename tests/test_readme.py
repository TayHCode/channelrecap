import helpers
import re
import unittest

COMMANDS = [
    'python -m pip install .',
    'channelrecap run --manifest examples/demo/videos.csv --out demo-library',
    'channelrecap check-citations examples/demo/analysis-example.md --out demo-library',
]


class ReadmeTests(unittest.TestCase):
    def test_required_sections_and_commands(self) -> None:
        text = (helpers.ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertRegex(text,r'(?m)^## .*For your own videos')
        self.assertRegex(text,r'(?m)^## .*Limitations')
        self.assertIn("It is not built for collecting transcripts of other people's videos.",' '.join(text.split()))
        for command in COMMANDS:
            self.assertIn(command,text.splitlines())
        self.assertIn('built to be used on an RTX 3090',text)
        self.assertIn('--device cuda',text)
        roadmap = (helpers.ROOT / 'docs/roadmap.md').read_text(encoding='utf-8').casefold()
        self.assertIn('voice analysis',roadmap)
        self.assertIn('frame search',roadmap)
