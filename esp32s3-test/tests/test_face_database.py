import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from face_database import FaceDatabase


def face(vector, **extra):
    return dict(model='MBF_S8_V1', faces=1, dimensions=len(vector), embedding=vector, **extra)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'faces.json'
        self.db = FaceDatabase(self.path)

    def test_enroll_reload_and_multiple_samples(self):
        first = self.db.enroll(' An ', face([1., 0.]))
        second = self.db.enroll('an', face([0.99, 0.1]))
        self.assertEqual(first['id'], second['id'])
        reloaded = FaceDatabase(self.path)
        self.assertEqual(reloaded.people()[0]['samples'], 2)
        self.assertEqual(reloaded.recognize(face([1., 0.]))['name'], 'An')
        self.assertNotIn('embedding', reloaded.people()[0])

    def test_unknown_and_ambiguous_identities(self):
        self.db.enroll('An', face([1., 0.]))
        self.assertEqual(self.db.recognize(face([0., 1.]))['status'], 'unknown')
        self.db.enroll('Binh', face([0.999, 0.04]))
        self.assertEqual(self.db.recognize(face([1., 0.]))['status'], 'unknown')

    def test_invalid_vectors_and_face_count_rejected(self):
        for sample in [face([float('nan'), 0.]), face([0., 0.]), face([True, 0.]),
                       dict(model='MBF_S8_V1', faces=2, dimensions=2, embedding=[1., 0.]),
                       dict(model='MFN_S8_V1', faces=1, dimensions=2, embedding=[1., 0.])]:
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                self.db.enroll('An', sample)
        self.assertFalse(self.path.exists())

    def test_dimension_mismatch_and_corrupt_file(self):
        self.db.enroll('An', face([1., 0.]))
        with self.assertRaises(ValueError):
            self.db.enroll('Binh', face([1., 0., 0.]))
        self.path.write_text('{broken')
        with self.assertRaises(ValueError):
            FaceDatabase(self.path)
        self.assertEqual(self.path.read_text(), '{broken')

    def test_failed_write_keeps_existing_data(self):
        self.db.enroll('An', face([1., 0.]))
        before = self.path.read_bytes()
        with patch('face_database.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.db.enroll('Binh', face([0., 1.]))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.db.people()), 1)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == '__main__':
    unittest.main()
