# -*- coding: utf-8 -*-
"""厂商迁移保留用户配置和密钥，不重新启用退役能力。"""
import sys
import unittest
import tempfile
import json
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import provider_catalog as catalog

class CatalogTests(unittest.TestCase):
    def test_server_migrates_v1_and_preserves_backup(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('server_catalog_test',Path(__file__).resolve().parents[1]/'server.py')
        server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'providers.json'
            original={'providers':[{'id':'glm-text','kind':'text','model':'private-model','api_key':'local-secret','enabled':True}]}
            path.write_text(json.dumps(original),encoding='utf-8')
            with patch.object(server,'PROV',str(path)):
                server.ensure_providers()
            data=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual([v['id'] for v in data['vendors']],list(catalog.ORDER))
            self.assertEqual(next(v for v in data['vendors'] if v['id']=='glm')['api_key'],'local-secret')
            self.assertEqual(json.loads(Path(str(path)+'.before-chrome-use.bak').read_text()),original)
    def test_order_and_retirement(self):
        rows = catalog.normalize_vendors([{'id':'local-codex','enabled':True}])
        self.assertEqual([v['id'] for v in rows], list(catalog.ORDER))
        self.assertNotIn('local-codex', [v['id'] for v in rows])

    def test_public_defaults_are_disabled_and_have_no_local_address(self):
        self.assertTrue(catalog.DEFAULT_VENDORS)
        self.assertTrue(all(not row['enabled'] for row in catalog.DEFAULT_VENDORS))
        comfy = next(row for row in catalog.DEFAULT_VENDORS if row['id'] == 'local-comfyui')
        self.assertEqual(comfy['base_url'], '')
        self.assertTrue(all(not row.get('api_key') for row in catalog.DEFAULT_VENDORS))

    def test_preserves_custom_values_and_secrets(self):
        old = {'id':'minimax','api_key':'private-key','enabled':True,
               'base_url':'https://custom.example/v1','models':{'video':'custom-model'},
               'extra':{'custom':'value'}}
        new = next(v for v in catalog.normalize_vendors([old]) if v['id']=='minimax')
        self.assertEqual(new['api_key'], 'private-key')
        self.assertEqual(new['base_url'], old['base_url'])
        self.assertEqual(new['models']['video'], 'custom-model')
        self.assertEqual(new['extra']['custom'], 'value')
        self.assertTrue(new['models']['speech'])
        self.assertEqual(catalog.normalize_vendors([new]),catalog.normalize_vendors(catalog.normalize_vendors([new])))

    def test_replaces_known_broken_defaults(self):
        rows=catalog.normalize_vendors([{'id':'minimax','base_url':'https://api.minimaxi.com/v1',
            'models':{'video':'hailuo-3'},'endpoints':{'video':'/videos/generations'}}])
        row=next(v for v in rows if v['id']=='minimax')
        self.assertEqual(row['models']['video'],'MiniMax-H3')
        self.assertEqual(row['endpoints']['video'],'/v2/video_generation')

if __name__=='__main__': unittest.main()
