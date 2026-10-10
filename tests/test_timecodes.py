import helpers
import unittest
from channelrecap.errors import InputError
from channelrecap.timecodes import parse_time, format_time, covered


class TimeTests(unittest.TestCase):
    def test_valid(self) -> None:
        for text, expected in [('90',90),('90.5',90.5),('01:30',90),('01:02:03.5',3723.5),('75:30',4530)]:
            with self.subTest(text=text):
                self.assertEqual(parse_time(text), expected)

    def test_invalid(self) -> None:
        for text in ['', 'words', '-1', '1:-2', '1:2:3:4', '01:75', '1:61:00', 'nan', 'inf', '1.5:00']:
            with self.subTest(text=text), self.assertRaises(InputError):
                parse_time(text)

    def test_display(self) -> None:
        self.assertEqual(format_time(3723.99), '01:02:03')
        self.assertEqual(covered(None, None), 'whole video')
        self.assertEqual(covered(60, None), 'from 00:01:00')
        self.assertEqual(covered(None, 60), 'up to 00:01:00')
        self.assertEqual(covered(0, 60), '00:00:00 to 00:01:00')
