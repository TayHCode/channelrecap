import helpers
import unittest
from channelrecap.errors import display_source, safe_error


class ErrorTests(unittest.TestCase):
    def test_path_details_are_discarded_as_a_whole(self) -> None:
        paths = ('/var/fixtures/hidden.wav', '"/var/quiet room/hidden take.wav"',
                 "'/opt/quiet room/hidden take.wav'", r'C:\Quiet Room\hidden take.wav',
                 'C:/Quiet Room/hidden take.wav', '\\\\' + 'example.com' + r'\shared room\hidden take.wav',
                 '//desk/shared room/hidden take.wav', r'\Quiet Room\hidden take.wav',
                 r'\\?\C:\Quiet Room\hidden take.wav', 'C:hidden.wav',
                 'file:///var/quiet%20room/hidden.wav', 'FILE://desk/shared%20room/hidden.wav',
                 'file:/var/quiet room/hidden.wav', 'file:///C:/Quiet%20Room/hidden.wav',
                 '~/quiet room/hidden.wav', '../quiet room/hidden.wav', './hidden.wav',
                 'quiet room/hidden.wav', r'quiet room\hidden.wav')
        for path in paths:
            for prefix in ('Cannot decode ', 'Error:input='):
                with self.subTest(path=path, prefix=prefix):
                    self.assertEqual(safe_error(prefix + path + ' while decoding'),
                                     'Source processing failed; local path details omitted.')
        # Supplying a shorter known path cannot expose the rest of an unrelated one.
        self.assertEqual(safe_error('Cannot decode /var/fixtures extra/hidden.wav', '/var/fixtures'),
                         'Source processing failed; local path details omitted.')

    def test_source_labels_and_path_free_errors(self) -> None:
        for path in ('/var/fixtures/talk.wav', r'C:\Drafts\talk.wav', '\\\\' + 'example.com' + r'\share\talk.wav',
                     'file:///var/fixtures/talk.wav', 'file://desk/share/talk.wav',
                     'file:///C:/Drafts/talk.wav', 'C:talk.wav', r'\Drafts\talk.wav'):
            with self.subTest(path=path):
                self.assertEqual(display_source(path), 'talk.wav')
        self.assertEqual(safe_error('No audio found in that part of the source.'),
                         'No audio found in that part of the source.')
        self.assertEqual(safe_error('decode failure\n  check the format'), 'decode failure check the format')
        self.assertEqual(len(safe_error('x' * 2000)), 500)
