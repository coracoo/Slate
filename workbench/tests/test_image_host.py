# -*- coding: utf-8 -*-
"""图床缓存按后端与账户分域，同图切换配置时不得复用旧直链。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import image_host


class ImageHostCacheTests(unittest.TestCase):
    def test_same_host_reuses_upload_but_switching_host_or_account_uploads_again(self):
        responses = [({'data': {'link': 'https://imgur.test/one.png'}}, 200),
                     ({'data': {'url': 'https://imgbb.test/two.png'}}, 200),
                     ({'data': {'url': 'https://imgbb.test/three.png'}}, 200)]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'reference.png'
            source.write_bytes(b'local-reference')
            cache = Path(folder) / 'uploads.json'
            imgur = {'type': 'imgur', 'client_id': 'first-private-client'}
            imgbb = {'type': 'imgbb', 'api_key': 'second-private-key'}
            changed = {'type': 'imgbb', 'api_key': 'third-private-key'}
            with patch.object(image_host, '_CACHE_FILE', cache), patch.object(image_host, '_http', side_effect=responses) as http:
                first = image_host.upload_image(source, imgur)
                self.assertEqual(image_host.upload_image(source, imgur), first)
                self.assertEqual(image_host.upload_image(source, imgbb), 'https://imgbb.test/two.png')
                self.assertEqual(image_host.upload_image(source, changed), 'https://imgbb.test/three.png')
                self.assertEqual(http.call_count, 3)
            cached = cache.read_text(encoding='utf-8')
            for value in ('first-private-client', 'second-private-key', 'third-private-key'):
                self.assertNotIn(value, cached, '配置指纹不得保存明文凭据')
            self.assertEqual(len(json.loads(cached)), 3)

    def test_legacy_content_only_cache_does_not_override_selected_backend(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'reference.png'
            source.write_bytes(b'local-reference')
            cache = Path(folder) / 'uploads.json'
            cache.write_text(json.dumps({image_host._sha256(source): 'https://old.test/image.png'}), encoding='utf-8')
            response = ({'result': {'variants': ['https://cloudflare.test/current.png']}}, 200)
            with patch.object(image_host, '_CACHE_FILE', cache), patch.object(image_host, '_http', return_value=response) as http:
                result = image_host.upload_image(source, {'type': 'cloudflare', 'account_id': 'account', 'api_token': 'private-token'})
                self.assertEqual(result, 'https://cloudflare.test/current.png')
                self.assertEqual(http.call_count, 1)


if __name__ == '__main__':
    unittest.main()
