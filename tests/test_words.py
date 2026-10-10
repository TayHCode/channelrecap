import helpers
import unittest
from channelrecap.words import split_words, spoken_words, sentences
from channelrecap.transcript import Segment


class WordTests(unittest.TestCase):
    def test_folded_word_positions_map_back_to_original_sentences(self) -> None:
        from channelrecap.words import word_spans
        for text in ('İstanbul how do amber lanterns glow tonight', 'İ İ why is the cup warm',
                     'Cafe\u0301 \ufb03 how do lanterns glow', 'שלום كيف bright cups glow',
                     '\ufeff🫖 why does the kettle hiss', 'Straße why is water warm'):
            with self.subTest(text=text):
                spans = word_spans(text)
                self.assertEqual([word for word, _, _ in spans], split_words(text))
                for word, begin, end in spans:
                    self.assertIn(word, split_words(text[begin:end]))
                result = sentences([Segment(1, 2, '  ' + text + '  '), Segment(3, 4, 'why is this cup blue')])[0]
                for word in result.words:
                    self.assertIn(word.text, split_words(result.text[word.begin:word.end]))
                self.assertEqual(result.words[-5].start, 3)

    def test_unicode_and_apostrophes(self) -> None:
        self.assertEqual(split_words('“Don’t” café 我们 foo_bar 42'), ["don't", 'café', '我们', 'foo', 'bar', '42'])

    def test_carry_and_start_flags(self) -> None:
        segments = [Segment(1,3,'So this carries'), Segment(3,6,'into another segment. Well, yes! Last fragment')]
        result = sentences(segments)
        self.assertEqual([s.text for s in result], ['So this carries into another segment.', 'Well, yes!', 'Last fragment'])
        self.assertEqual([s.start for s in result], [1,3,3])
        words = spoken_words(segments)
        self.assertEqual([w.text for w in words if w.sentence_start], ['so','well','last'])
        self.assertEqual(words[3].start, 3)
