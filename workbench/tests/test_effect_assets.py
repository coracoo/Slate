# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import effect_assets
import asset_registry
import chatgpt_queue
import gen_asset_images
import skill_lib
from visual_asset_prompt import subject_prompt, generation_kind


class EffectAssetsTests(unittest.TestCase):
    def test_conversion_preserves_history_and_generates_without_character_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '素材'
            root.mkdir()
            actor = {'id': 'hero', 'name': '角色', 'states': [
                {'id': sid, 'label': sid, 'look_diff': '金色光点', 'episodes': ['E1']} for sid in ('a', 'b')]}
            (root / '人物.json').write_text(json.dumps({'characters': [actor]}), encoding='utf-8')
            (root / '人物').mkdir()
            old = root / '人物' / 'hero__a.png'
            old.write_bytes(b'old image')
            (root / '素材图.json').write_text(json.dumps({'人物': {'hero': {'states': {'a': {'path': '素材/人物/hero__a.png'}}}}}), encoding='utf-8')
            for _ in range(2):
                ref = effect_assets.promote_states(folder, 'hero', ['a', 'b'], 'gold_light', '金色光点', '云层中的金色光点，角色不现身')
            props = json.loads((root / '道具.json').read_text(encoding='utf-8'))['props']
            self.assertEqual(len(props), 1)
            self.assertEqual(old.read_bytes(), b'old image')
            registry = asset_registry.AssetRegistry(folder)
            self.assertFalse(registry.resolve('@character:hero').get('states'))
            self.assertEqual(registry.resolve(ref)['related_refs'], ['@character:hero'])
            graph = gen_asset_images.collect_asset_image_plan(folder, 'prop', 'gold_light', 'local-comfyui')[0]
            self.assertEqual(graph['reference_tokens'], [])
            with self.assertRaisesRegex(ValueError, '独立视觉素材'):
                chatgpt_queue._asset_source(folder, {'kind': 'character', 'id': 'hero'}, 'a')
            with patch.object(skill_lib, 'resolve_asset_style_text', return_value=('', 'none')):
                prompt, _ = skill_lib.compose_asset_image_prompt(folder, subject_prompt('prop', props[0]), kind=generation_kind('prop', props[0]))
            self.assertIn('金色光点', prompt)
            self.assertNotIn('五段从左到右', prompt)
            self.assertNotIn('单个主体居中', prompt)
            self.assertTrue(list((root / '.versions').glob('*.json')))
            queued = chatgpt_queue.queue_assets(folder, [ref])
            manifest = json.loads((Path(folder) / '创作' / 'creation.json').read_text(encoding='utf-8'))
            job = manifest['items'][-1]
            self.assertEqual(job['refs'], [])
            self.assertIn('显现或特效画面', job['prompt_assembled'])
            self.assertNotIn('五段从左到右', job['prompt_assembled'])
