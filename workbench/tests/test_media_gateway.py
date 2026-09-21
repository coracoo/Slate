# -*- coding: utf-8 -*-
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import media_gateway as gateway


class GatewayTests(unittest.TestCase):
    def test_signed_link_prefix_port_expiry_and_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root/'项目'; project.mkdir()
            (project/'a.mp4').write_bytes(b'video')
            with patch.object(gateway,'CONFIG',root/'gateway.json'):
                config = gateway.save({'base_url':'https://example.com:8443/previs','ttl_seconds':3600})
                self.assertNotIn('secret',config)
                url = gateway.signed_url(project,'a.mp4')
                self.assertTrue(url.startswith('https://example.com:8443/previs/api/public-reference?'))
                params = {k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}
                self.assertEqual(gateway.verify(root,params),project/'a.mp4')
                with patch('media_gateway.time.time',return_value=int(params['expires'])+1):
                    with self.assertRaises(ValueError): gateway.verify(root,params)
                params['path']='../secret.mp4'
                with self.assertRaises(ValueError): gateway.verify(root,params)
