from pathlib import Path
import unittest

from clone_keeper.core import File
from clone_keeper.validation import check_qml


@unittest.skipUnless(Path('/usr/lib/qt6/bin/qmllint').exists(), 'Qt 6 qmllint is optional')
class CandidateSyntaxTests(unittest.TestCase):
    def test_broken_candidate_syntax_is_reported_with_file_and_line(self):
        result = check_qml({'Broken.qml': File(b'import QtQuick\nItem { property string value: }')})
        self.assertTrue(result['available'])
        self.assertTrue(result['errors'])
        self.assertIn('Broken.qml:2:', result['errors'][0])

    def test_valid_qml_parses_without_running_component(self):
        result = check_qml({'Widget.qml': File(b'import QtQuick\nItem { Component.onCompleted: { throw new Error("do not execute") } }')})
        self.assertTrue(result['available'])
        self.assertEqual(result['errors'], [])
