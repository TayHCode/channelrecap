import helpers
# Planted demo facts: water and grind size recur in all three episodes.
# Water rises from 2 to 3 to 16 occurrences; later lessons answer the sour-cup question.
# Storage and cleaning questions remain open; temperature stability gets one short explanation.
from collections import Counter
import unittest
from channelrecap.manifest import read_manifest
from channelrecap.captions import read_captions
from channelrecap.transcript import Transcript, Segment, word_count
from channelrecap.signals import term_counts, frequent, recurring, unique_terms, trend_label, rate
from channelrecap.words import package_text


class SignalTests(unittest.TestCase):
    def test_filter_pairs_thresholds(self) -> None:
        transcript = Transcript(segments=[Segment(0,1,'The red kettle red kettle. Red and kettle. Like 123 an aa.')])
        counts = term_counts(transcript)
        self.assertEqual(counts['red kettle'], 2)
        for term in ['the','and','like','123','aa','kettle red']:
            if term == 'kettle red':
                self.assertEqual(counts[term], 1)
            else:
                self.assertEqual(counts[term], 0)
        self.assertEqual(frequent(counts), [('kettle',3), ('red',3), ('red kettle',2)])
        self.assertEqual(term_counts(Transcript(segments=[Segment(0,1,'Red. Kettle.')]))['red kettle'], 0)
        self.assertGreaterEqual(len(package_text('stopwords_en.txt').splitlines()), 150)

    def test_recurring_and_unique(self) -> None:
        counts = [Counter({'coffee':4,'kettle':3}), Counter({'coffee':2,'milk':4})]
        self.assertEqual(recurring(counts), ['coffee'])
        self.assertEqual(unique_terms(counts,0), [('kettle',3)])

    def test_trends(self) -> None:
        cases = [([1, 2], [10, 10], 'not enough videos for a trend (needs 3 usable videos)'),
                 ([10, 15, 20], [10, 15, 20], 'rising'),
                 ([40, 30, 20, 10], [40, 30, 20, 10], 'falling'),
                 ([10, 10, 999, 10, 10], [10] * 5, 'steady'),
                 ([64.5, 0, 23.8, 64.5], [2, 0, 1, 2], 'too few to call a trend'),
                 ([60.6, 0, 24.4, 66.7], [2, 0, 1, 2], 'too few to call a trend'),
                 ([32.3, 0, 23.8, 32.3], [1, 0, 1, 1], 'too few to call a trend'),
                 ([0, 0, 0], [0, 0, 0], 'too few to call a trend'),
                 ([10, 0, 5, 10], [10] * 4, 'steady'),
                 ([10, 50, 5, 40, 3, 20], [10] * 6, 'no consistent direction'),
                 ([10, 11, 12], [3, 3, 3], 'too few to call a trend'),
                 ([10, 11, 12], [10, 10, 10], 'steady')]
        for values, counts, expected in cases:
            with self.subTest(values=values, counts=counts):
                self.assertTrue(trend_label(values, counts).startswith(expected))
        with self.assertRaises(ValueError):
            trend_label([1, 2, 3], [10])
        self.assertEqual(rate(1, 0), 0)

    def test_demo_recurring(self) -> None:
        counts, sizes = [], []
        for row in read_manifest(helpers.ROOT / 'examples/demo/videos.csv'):
            cues = read_captions(row.path,start=row.start,end=row.end)
            transcript = Transcript(segments=[Segment(c.start,c.end,c.text) for c in cues])
            counts.append(term_counts(transcript))
            sizes.append(word_count(' '.join(c.text for c in cues)))
        self.assertTrue(all(c['water'] >= 2 and c['grind size'] >= 2 for c in counts))
        self.assertEqual(trend_label([rate(c['water'], n) for c,n in zip(counts,sizes)], [c['water'] for c in counts]), 'too few to call a trend (needs 3 matches per video and 30 total)')


class GapTests(unittest.TestCase):
    def test_unpunctuated_caption_hints_are_bounded_and_timed(self) -> None:
        from channelrecap.signals import questions, deferrals
        from channelrecap.summary import compose_summary
        transcript = Transcript(segments=[Segment(0, 10, 'coffee ' * 100),
            Segment(10, 20, 'why does the water taste different ' + 'coffee ' * 100),
            Segment(20, 30, 'we will explain this in another video ' + 'coffee ' * 300)])
        asked = questions(transcript).items
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0][0], 10)
        self.assertIn('why does the water taste different', asked[0][1])
        self.assertEqual(asked[0][2], 'possible question; no punctuation')
        deferred = deferrals(transcript).items
        self.assertEqual(len(deferred), 1)
        self.assertIn('another video', deferred[0][1])
        self.assertLessEqual(len(deferred[0][1]), 284)
        self.assertLessEqual(len(asked[0][1]), 284)
        entry = dict(video_id='talk', title='Talk', date='', start=None, end=None, duration=30, words=500, transcript='transcripts/talk.txt')
        text = compose_summary({'videos': []}, [(entry, transcript)])
        self.assertIn('possible question; no punctuation', text)
        self.assertTrue(all(len(line) < 400 for line in text.splitlines() if line.startswith('- [')))

    def test_excerpt_offsets_keep_original_unicode_text(self) -> None:
        from channelrecap.signals import questions
        from channelrecap.words import split_words
        for prefix in ('Straße', 'İstanbul', 'İ İ', 'Cafe\u0301', '\ufb03', 'שלום', '🫖', '\ufeff'):
            with self.subTest(prefix=prefix):
                transcript = Transcript(segments=[Segment(0, 2, prefix + ' how do amber lanterns glow tonight')])
                expected = ('… ' if split_words(prefix) else prefix + ' ') + 'how do amber lanterns glow tonight …'
                self.assertEqual(questions(transcript).items[0][1], expected)

    def test_question_and_deferral_rules(self) -> None:
        from channelrecap.signals import questions, deferrals
        transcript = Transcript(segments=[Segment(12,20,"Why? Why is the water warm? We'll get to this later on. Later ongoing work continues.")])
        self.assertEqual(questions(transcript).items,[(12,'Why is the water warm?')])
        self.assertEqual(deferrals(transcript).items,[(12,"We'll get to this later on.",'later on')])

    def test_caps(self) -> None:
        from channelrecap.summary import compose_summary
        transcript = Transcript(segments=[Segment(n,n+1,'Why is this cup warm? Another video will explain.') for n in range(35)])
        entry = dict(video_id='talk',title='Talk',date='',start=None,end=None,duration=35,words=350,transcript='transcripts/talk.txt')
        text = compose_summary({'videos':[]},[(entry,transcript)])
        self.assertIn('(+5 more)',text)
        self.assertIn('(+15 more)',text)

    def test_dense_hints_count_overflow_without_building_unused_excerpts(self) -> None:
        from unittest.mock import patch
        from channelrecap import signals
        for count in (250, 2000):
            with self.subTest(count=count):
                transcript = Transcript(segments=[Segment(0, 3, 'how do amber lanterns glow tonight ' * count)])
                with patch.object(signals, 'excerpt', wraps=signals.excerpt) as excerpts:
                    result = signals.questions(transcript)
                self.assertEqual(len(result.items), 30)
                self.assertEqual(result.omitted, count - 30)
                self.assertEqual(excerpts.call_count, 30)
                self.assertTrue(all('how do amber lanterns glow tonight' in hint[1] for hint in result.items))
        transcript = Transcript(segments=[Segment(n, n + 1, 'Another video will explain this.') for n in range(1000)])
        with patch.object(signals, 'excerpt', wraps=signals.excerpt) as excerpts:
            result = signals.deferrals(transcript)
        self.assertEqual((len(result.items), result.omitted, excerpts.call_count), (20, 980, 20))
