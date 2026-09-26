# -*- coding: utf-8 -*-
"""旁白（narrator）纳入音色绑定的回归。

用户 09-25 定的口径：**旁白进 ④ 音色绑定；不绑定则为不生成、不注入**。
这条同时锁住两件事：
1. 旁白可以拿到音色（state/resolve/bind/video_voices/prepare 全链路）；
2. 但旁白**永远不进人物档案**——AGENTS 的禁建档与 validate_dialogue 的 RESERVED_ACTOR
   口径不能因为"能配音色"就被破，所以绑定数据落在 sidecar 素材/音色/旁白.json。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system' / 'tools'))
sys.path.insert(0, str(ROOT / 'workbench' / 'tools'))

import voice_assets                      # noqa: E402
from production_media import digest      # noqa: E402


CFG = {'id': 'doubao-plan', 'base_url': 'https://example.invalid/v1', 'api_key': 'k-test',
       'models': {'speech': 'tts-model'}, 'endpoints': {}}


class NarratorVoiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self._tmp.name)
        (self.proj / '素材' / '音色').mkdir(parents=True)
        (self.proj / '分镜').mkdir(parents=True)
        sample = self.proj / '素材' / '音色' / 'narr.mp3'
        sample.write_bytes(b'audio-sample-bytes')
        self.voice = {'id': 'vn1', 'revision': 1, 'name': '旁白声', 'voice_id': 'cloud-narr',
                      'sample': '素材/音色/narr.mp3', 'sha256': digest(sample),
                      'profile': voice_assets.profile(CFG), 'origin': 'upload', 'tts_verified': True}
        (self.proj / '素材' / '音色' / '音色库.json').write_text(
            json.dumps({'voices': [self.voice]}, ensure_ascii=False), encoding='utf-8')
        (self.proj / '素材' / '人物.json').write_text(json.dumps(
            {'characters': [{'id': 'hero', 'name': '主角', 'states': [{'id': 'S1', 'label': '受伤'}]}]},
            ensure_ascii=False), encoding='utf-8')
        (self.proj / '分镜' / '剧本_E1.json').write_text(json.dumps({'shots': [
            {'id': 'S1', 'dur': 4, 'lines': [{'at': 0, 'dur': 3, 'speaker': 'narrator', 'line': '仙山恢复平静'},
                                             {'at': 1, 'dur': 2, 'speaker': 'hero', 'line': '你终于来了'}]},
        ]}, ensure_ascii=False), encoding='utf-8')

    def tearDown(self):
        self._tmp.cleanup()

    def _shots(self):
        return json.loads((self.proj / '分镜' / '剧本_E1.json').read_text(encoding='utf-8'))['shots']

    def test_narrator_appears_in_page_without_touching_the_character_archive(self):
        st = voice_assets.state(str(self.proj))
        row = next((c for c in st['characters'] if c['id'] == 'narrator'), None)
        self.assertIsNotNone(row, '④ 页面必须出现旁白这一行，否则用户无从绑定')
        self.assertEqual(row['name'], '旁白')
        self.assertIsNone(row['voice_binding'], '未绑定要显示"待绑定"而不是空对象')
        archived = json.loads((self.proj / '素材' / '人物.json').read_text(encoding='utf-8'))
        self.assertEqual([c['id'] for c in archived['characters']], ['hero'],
                         '旁白进响应可以，进人物档案就是破禁建档口径（validate_dialogue 会报 RESERVED_ACTOR）')

    def test_bind_writes_sidecar_and_resolve_returns_it(self):
        out = voice_assets.bind(str(self.proj), {'character_id': 'narrator',
                                                 'voice_asset_id': 'vn1', 'revision': 1})
        self.assertTrue(out['ok'])
        sidecar = self.proj / '素材' / '音色' / '旁白.json'
        self.assertTrue(sidecar.is_file(), '绑定必须落在 sidecar')
        self.assertIn('vn1', sidecar.read_text(encoding='utf-8'))
        self.assertEqual(voice_assets.resolve(str(self.proj), 'narrator')['voice_id'], 'cloud-narr')
        archived = json.loads((self.proj / '素材' / '人物.json').read_text(encoding='utf-8'))
        self.assertNotIn('narrator', json.dumps(archived, ensure_ascii=False), '绑定后档案仍不得出现旁白')
        # 解绑=恢复"不出声"，不是删档案
        voice_assets.bind(str(self.proj), {'character_id': 'narrator', 'voice_asset_id': ''})
        self.assertEqual(voice_assets.narrator_binding(str(self.proj)), {})
        with self.assertRaises(ValueError):
            voice_assets.resolve(str(self.proj), 'narrator')

    def test_video_voices_skips_unbound_narrator_and_includes_bound_one(self):
        voice_assets.bind(str(self.proj), {'character_id': 'hero', 'voice_asset_id': 'vn1', 'revision': 1})
        with self.assertRaises(ValueError):
            voice_assets.resolve(str(self.proj), 'narrator')
        ids = [v['character_id'] for v in voice_assets.video_voices(str(self.proj), self._shots())]
        self.assertEqual(ids, ['hero'], '未绑定的旁白不得混进音色注入（不绑定=不注入）')
        voice_assets.bind(str(self.proj), {'character_id': 'narrator', 'voice_asset_id': 'vn1', 'revision': 1})
        ids = [v['character_id'] for v in voice_assets.video_voices(str(self.proj), self._shots())]
        self.assertEqual(sorted(ids), ['hero', 'narrator'], '绑定后旁白随其他说话人一起注入')

    def test_speech_generation_refuses_unbound_narrator_instead_of_defaulting(self):
        # 非 MiniMax 厂商原本会静默回落 'alloy'——旁白未绑定时必须拒绝生成而不是随便给个声音
        with self.assertRaises(ValueError) as cm:
            voice_assets.prepare(str(self.proj), {'action': 'speech', 'board': '剧本_E1.json',
                                                  'character_id': 'narrator'}, CFG)
        self.assertIn('不绑定则不生成配音', str(cm.exception))
        voice_assets.bind(str(self.proj), {'character_id': 'narrator', 'voice_asset_id': 'vn1', 'revision': 1})
        packet = voice_assets.prepare(str(self.proj), {'action': 'speech', 'board': '剧本_E1.json',
                                                       'character_id': 'narrator'}, CFG)
        self.assertEqual([t['text'] for t in packet['lines']], ['仙山恢复平静'], '只取旁白自己的台词行')
        self.assertEqual(packet['voice']['voice_id'], 'cloud-narr')

    def test_narrator_rejects_state_and_variant_bindings(self):
        with self.assertRaises(ValueError):
            voice_assets.bind(str(self.proj), {'character_id': 'narrator', 'state': 'S1', 'voice_asset_id': 'vn1'})
        with self.assertRaises(ValueError):
            voice_assets.bind(str(self.proj), {'character_id': 'narrator', 'mode': 'variant',
                                               'name': '角色音乐·旁白', 'voice_asset_id': 'vn1', 'revision': 1})
        self.assertFalse((self.proj / '素材' / '音色' / '旁白.json').exists(), '被拒的绑定不得留下文件')


if __name__ == '__main__':
    unittest.main()
