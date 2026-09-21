# -*- coding: utf-8 -*-
import io
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from reference_media import upload, resolve


class ReferenceMediaTests(unittest.TestCase):
    def test_local_url_and_public_only_boundary(self):
        with tempfile.TemporaryDirectory() as directory, patch('media_gateway.load', return_value={'base_url':''}):
            p = Path(directory)
            row = upload(p, 'audio', 'voice.wav', io.BytesIO(b'RIFFsample'), 10)
            refs = resolve(p, [row['path'], 'https://example.com/a.wav'], 'audio', {'id':'doubao'})
            self.assertTrue(refs[0]['sha256'])
            self.assertEqual(refs[1]['url'], 'https://example.com/a.wav')
            with self.assertRaisesRegex(ValueError, '公网 URL'):
                resolve(p, [row['path']], 'audio', {'id':'agnes'})
            with self.assertRaises(ValueError): resolve(p, ['../outside.wav'], 'audio', {'id':'doubao'})
            with self.assertRaises(ValueError): resolve(p, ['http://127.0.0.1/a.wav'], 'audio', {'id':'doubao'})

    def test_truncated_upload_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'上传中断'):
                upload(Path(directory), 'video', 'clip.mp4', io.BytesIO(b'a'), 10)
            self.assertFalse(list(Path(directory).rglob('*.mp4')))
