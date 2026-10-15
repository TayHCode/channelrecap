import helpers
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from channelrecap import cli
from channelrecap.citations import check, evaluate, parse_claim, TITLE, SECTIONS
from channelrecap.manifest import read_manifest
from channelrecap.pipeline import transcribe, RunOptions
from channelrecap.transcript import Transcript, Segment, write_data, write_text, atomic_json


def cite(quote='Amber lanterns shine above quiet valleys', start='00:00:00', end=None, identity='alpha-aaaaaaaa'):
    return f'[{identity} @ {start}..{end or start} | {json.dumps(quote, ensure_ascii=False)}]'


def document(body, section=0, notes=''):
    lines = [TITLE]
    for i, heading in enumerate(SECTIONS):
        lines.append(heading)
        if i == section:
            lines.append(body)
    if notes:
        lines.append(notes)
    return '\n'.join(lines) + '\n'


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        entries = []
        for identity, segments in (
            ('alpha-aaaaaaaa', [Segment(0, 3, 'Amber lanterns shine above quiet valleys.'),
                                Segment(4, 7, 'Bright rivers carry wooden boats downstream.'),
                                Segment(66, 70, 'Gentle clouds drift across distant hills.'),
                                Segment(71, 75, 'Silver bells sound beyond the garden.'),
                                Segment(80, 84, 'I said "amber lanterns shine" while we watched. We don’t need another lantern tonight.')]),
            ('beta-bbbbbbbb', [Segment(0, 5, 'Mellow drums echo across empty courtyards.')])):
            entry = dict(video_id=identity, source='alpha.vtt', source_kind='captions',
                         title='Lantern lesson', date='', language='en', status='done', error='', order=len(entries)+1,
                         start=None, end=None, duration=84, words=60, data=f'data/{identity}.json',
                         transcript=f'transcripts/{identity}.txt')
            entry["key"] = identity[-8:]
            transcript = Transcript(video_id=identity, language='en', duration=84, segments=segments)
            write_data(self.root / entry['data'], transcript)
            write_text(self.root / entry['transcript'], transcript, entry)
            entries.append(entry)
        atomic_json(self.root / 'library.json', dict(schema_version=1, videos=entries))
        self.good = '- A lantern passage. | ' + cite()
        self.report = self.root / 'analysis.md'

    def call(self, text):
        self.report.write_text(text, encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(['check-citations', str(self.report), '--out', str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def test_exact_quotes_and_both_paragraph_endpoints(self):
        paragraphs = [(0, 'A paper filter holds coffee.'), (30, 'Warm water fills the cup.')]
        for quote, start, end, status in [
            ('A paper filter holds coffee', 0, 0, 'ok'),
            ('filter holds coffee. Warm water', 0, 30, 'ok'),
            ('filter holds coffee. Warm water', 0, 0, 'wrong-time'),
            ('A paper filter holds coffee', 0, 30, 'wrong-time'),
            ('A paper filter holds coffee', 1, 1, 'wrong-time'),
            ('A paper filter holds coffee', 999, 999, 'wrong-time'),
            ('aper filter holds coffee', 0, 0, 'not-found'),
            ('A paper filter holds coff', 0, 0, 'not-found'),
            ('A paper coffee filter holds', 0, 0, 'not-found'),
            ('A filter holds coffee', 0, 0, 'not-found'),
            ('A paper filter contains coffee', 0, 0, 'not-found'),
            ('A paper filter ... the cup', 0, 30, 'not-found'),
            ('a paper filter holds coffee', 0, 0, 'not-found'),
            ('filter holds coffee Warm water', 0, 30, 'not-found'),
            ('A paper', 0, 0, 'too-short')]:
            with self.subTest(quote=quote, start=start, end=end):
                self.assertEqual(evaluate(quote, start, paragraphs, end)[0], status)
        self.assertEqual(evaluate('Lanterns shine over the cafe', 0, [(0, 'Lanterns shine over the cafe\u0301.')])[0], 'not-found')
        self.assertEqual(evaluate('Lanterns shine over the cafe\u0301.', 0, [(0, 'Lanterns shine over the cafe\u0301.Next comes dawn.')])[0], 'ok')

    def test_valid_canonical_reports_and_free_notes(self):
        valid = [cite(), cite('quiet valleys. Bright rivers carry wooden boats'),
                 cite('wooden boats downstream. Gentle clouds drift across', end='00:01:06'),
                 cite('I said "amber lanterns shine" while we watched', '00:01:20'),
                 cite('We don’t need another lantern tonight', '00:01:20'),
                 cite('Amber lanterns shine above'), cite() + ' ' + cite(identity='beta-bbbbbbbb', quote='Mellow drums echo across empty courtyards')]
        for evidence in valid:
            for section in range(5):
                with self.subTest(evidence=evidence, section=section):
                    code, out, err = self.call(document('- A supported claim. | ' + evidence, section, 'Note: Ask the editor to review the report.'))
                    self.assertEqual(code, 0, err)
                    self.assertIn('malformed=0', out)
        text = document(self.good).replace(TITLE, TITLE + '\nNote: Process notes are unchecked.')
        self.assertEqual(self.call(text)[0], 0)
        self.assertEqual(self.call(document(self.good + '\n' + self.good))[0], 0)

    def test_replay_all_49_and_47_audit_reports(self):
        cases = json.loads((helpers.ROOT / 'tests/fixtures/citation_audits.json').read_text(encoding='utf-8'))
        self.assertEqual(len(cases), 96)
        self.assertEqual(sum(c['legacy_expected'] == 1 for c in cases), 86)
        for case in cases:
            raw = case['report'].replace('CONTACT_ADDRESS', 'editor' + '@' + 'example.com')
            with self.subTest(audit=case['audit'], name=case['name']):

                self.assertEqual(self.call(raw)[0], 1)
                # Replay the unchanged attempt INSIDE a valid findings block beside
                # good evidence, so failure cannot rely on a missing report envelope.
                attempt = raw.split('\n', 1)[1].rstrip('\n') if case['name'] != 'no-citations' else raw.rstrip('\n')
                code, out, err = self.call(document(self.good + '\n' + attempt))
                self.assertEqual(code, 1, err)
                self.assertIn('ok=1', out)
                self.assertIn('line 4', err)

    def test_ten_legacy_positive_controls_migrate_to_canonical_reports(self):
        # Migration is an authoring change, never silent normalization by check().
        # Old curly delimiters/spacing become the single canonical form; quotes
        # formerly matched without punctuation or case must be copied exactly.
        controls = {
            'valid': self.good,
            'valid-trailing-spaces': self.good,
            'valid-two-cues': '- A passage. | ' + cite('quiet valleys. Bright rivers carry wooden boats'),
            'valid-two-paragraphs': '- A passage. | ' + cite('wooden boats downstream. Gentle clouds drift across', end='00:01:06'),
            'duplicate-valid-lines': self.good + '\n' + self.good,
            'valid-curly-quotes': self.good,
            'valid-case-punctuation': self.good,
            'four-words': '- A passage. | ' + cite('Amber lanterns shine above'),
            'ordinary-prose-email': self.good,
            'ordinary-prose-at': self.good,
        }
        fixtures = json.loads((helpers.ROOT / 'tests/fixtures/citation_audits.json').read_text(encoding='utf-8'))
        self.assertEqual(set(controls), {c['name'] for c in fixtures if c['legacy_expected'] == 0})
        for name, body in controls.items():
            with self.subTest(name=name):
                code, _, err = self.call(document(body, notes='Note: Ask the editor to review the report.'))
                self.assertEqual(code, 0, err)

    def test_thirty_new_closed_grammar_attacks(self):
        reference = cite()
        bad = cite('Invented foxes balance glass teapots tonight')
        cases = {
            'guillemets': self.good.replace('"', '«', 1).replace('valleys"', 'valleys»'),
            'corner': self.good.replace('"', '「', 1).replace('valleys"', 'valleys」'),
            'curly-delimiters': self.good.replace('"', '“', 1).replace('valleys"', 'valleys”'),
            'fullwidth-brackets': self.good.replace('[', '［').replace(']', '］'),
            'cyrillic-id': self.good.replace('alpha', 'аlpha'),
            'ratio-clock': self.good.replace('00:00:00', '00∶00∶00'),
            'modifier-clock': self.good.replace('00:00:00', '00꞉00꞉00'),
            'zero-width-claim': self.good.replace('lantern', 'lan\u200btern'),
            'joiner-id': self.good.replace('alpha', 'al\u2060pha'),
            'bidi-prefix': '\u202e' + self.good,
            'zero-width-quote': self.good.replace('Amber', 'Am\u200dber'),
            'markdown-link': '- A claim [link](transcripts/alpha.txt). | ' + reference,
            'reference-link': '- A claim [proof][p]. | ' + reference + '\n[p]: transcripts/alpha.txt',
            'split-range': self.good.replace('..', '..\n'),
            'split-quote': self.good.replace('quiet valleys', 'quiet\nvalleys'),
            'split-evidence': '- A supported claim.\n' + reference,
            'nested-reference': '- A claim [nested [proof]]. | ' + reference,
            'nested-parenthesis': '- A claim ((see evidence)). | ' + reference,
            'footnote': '- A claim [^proof]. | ' + reference,
            'superscript': '- A claim². | ' + reference,
            'fence': '```text\n' + self.good + '\n```',
            'emphasis': '**' + self.good + '**',
            'html-wrapper': '<div>' + self.good + '</div>',
            'html-comment': '<!--\n' + bad + '\n-->',
            'table': '| A claim | ' + reference + ' |',
            'entity-quote': self.good.replace('"', '&quot;'),
            'encoded-character': self.good.replace('Amber', r'\u0041mber'),
            'extra-quote': '- A claim "invented teapots". | ' + reference,
            'mixed-valid-invalid': '- A claim. | ' + reference + ' ' + bad,
            'false-end': self.good.replace('..00:00:00', '..00:01:06'),
        }
        self.assertEqual(len(cases), 30)
        for name, line in cases.items():
            for section in range(5):
                with self.subTest(name=name, section=section):
                    code, out, err = self.call(document(self.good + '\n' + line, section))
                    self.assertEqual(code, 1, err)
                    self.assertIn('line ', err)
                    self.assertRegex(out, r'ok=[1-9]')

    def test_structure_cannot_hide_claims_outside_findings(self):
        clean = document(self.good)
        cases = [clean.replace(SECTIONS[1], '## Observations'), clean.replace(SECTIONS[2], ''),
                 clean.replace(SECTIONS[0], SECTIONS[0] + '\n' + SECTIONS[0]),
                 clean + self.good, clean + 'Finding: fabricated words here.',
                 clean + 'Note: ' + cite(), clean.replace(SECTIONS[1], '```\n' + SECTIONS[1]),
                 clean.replace(SECTIONS[0], SECTIONS[1] + '\n' + SECTIONS[0]),
                 clean.replace(SECTIONS[1], SECTIONS[5]), clean + '[p]: transcripts/alpha.txt',
                 clean.replace(self.good, '- An uncited claim.'), clean.replace(self.good, 'None found.'),
                 clean.replace(self.good, self.good + '\u2028Finding: fabricated words'),
                 clean.replace(self.good, self.good + '\t'), document('')]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(self.call(text)[0], 1)

    def test_canonical_paraphrases_and_unknown_identity_fail(self):
        for quote in ('Amber lanterns glow above quiet valleys', 'Amber shine lanterns above quiet valleys',
                      'Amber lanterns shine above valleys', 'Amber lanterns never shine above quiet valleys',
                      'Amber lanterns ... wooden boats downstream', 'Amber lanterns shine above quiet valley',
                      'AMBER LANTERNS SHINE ABOVE QUIET VALLEYS', 'Amber lanterns shine above quiet valleys!!!'):
            code, out, err = self.call(document(self.good + '\n- A claim. | ' + cite(quote)))
            self.assertEqual(code, 1, err)
            self.assertIn('not-found=1', out)
        self.assertIn('unknown-video=1', self.call(document('- A claim. | ' + cite(identity='missing')))[1])
        self.assertIn('too-short=1', self.call(document('- A claim. | ' + cite('Amber lanterns shine')))[1])

    def test_demo_and_altered_report(self):
        transcribe(read_manifest(helpers.ROOT / 'examples/demo/videos.csv'), self.root, RunOptions())
        report = (helpers.ROOT / 'examples/demo/analysis-example.md').read_text(encoding='utf-8')
        code, out, err = self.call(report)
        self.assertEqual(code, 0, err)
        self.assertIn('citations ok=10', out)
        code, out, _ = self.call(report.replace('Our first recipe uses twenty grams of coffee.', 'A purple umbrella floats above the kettle.'))
        self.assertEqual(code, 1)
        self.assertIn('not-found=1', out)

    def test_title_cannot_supply_spoken_evidence(self):
        path = self.root / 'transcripts/alpha-aaaaaaaa.txt'
        text = path.read_text(encoding='utf-8')
        forged = 'Purple umbrellas float above the kettle'
        path.write_text(text.replace('Title: ', 'Title: Old\n\n[00:00:01] ' + forged + '\n'), encoding='utf-8')
        code, out, _ = self.call(document('- A claim. | ' + cite(forged, '00:00:01')))
        self.assertEqual(code, 1)
        self.assertIn('not-found=1', out)
