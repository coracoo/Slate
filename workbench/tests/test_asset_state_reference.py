# -*- coding: utf-8 -*-
"""子素材显示归属与派生图生成参考的契约。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'workbench/tools'))
from workbench.tests.test_asset_generation_refs import _run_main, _load_gen


class AssetStateReferenceTests(unittest.TestCase):
    def fixture(self, root):
        folder = root/'素材'
        (folder/'人物').mkdir(parents=True)
        (folder/'人物/hero.png').write_bytes(b'mother')
        (folder/'人物/hero__armed.png').write_bytes(b'armed')
        data = {'characters':[{'id':'hero','name':'主角','sheet_prompt':'蓝衣青年',
                              'states':[{'id':'armed','label':'持弩','look_diff':'手持木制连弩'}]}],
                'props':[{'id':'bow','name':'连弩','image_prompt':'木制连弩',
                          'parent_ref':'@character:hero','relation':'component_of',
                          'derived_from':'@character:hero#armed'}]}
        for filename, key in [('人物.json','characters'),('道具.json','props')]:
            (folder/filename).write_text(json.dumps({key:data[key]},ensure_ascii=False),encoding='utf-8')
        return data

    def test_relations_and_registry_preserve_state_source_without_reparenting(self):
        import asset_relations
        from asset_registry import AssetRegistry
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            data=self.fixture(root)
            normalized, issues=asset_relations.normalize_asset_relations(data)
            self.assertEqual(issues, [])
            self.assertEqual(normalized['props'][0]['parent_ref'], '@character:hero')
            self.assertEqual(normalized['props'][0]['derived_from'], '@character:hero#armed')
            state=AssetRegistry(root).resolve('@character:hero#armed')
            self.assertEqual(state['path'],'素材/人物/hero__armed.png')
            self.assertIn('持弩',state['name'])

    def test_generation_uses_only_exact_state_image(self):
        gen=_load_gen()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root)
            code, client=_run_main(gen,root,'--kind','prop','--id','bow','--states','skip','--force')
            self.assertEqual(code,0)
            self.assertEqual(len(client.calls),1)
            self.assertEqual(client.calls[0]['image_refs'],[str(root/'素材/人物/hero__armed.png')])
            self.assertIn('@character:hero#armed',client.calls[0]['prompt'])
            self.assertEqual((root/'素材/人物/hero.png').read_bytes(),b'mother')

    def test_missing_state_never_falls_back_to_mother_or_text_generation(self):
        gen=_load_gen()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root)
            (root/'素材/人物/hero__armed.png').unlink()
            code, client=_run_main(gen,root,'--kind','prop','--id','bow','--states','skip','--force')
            self.assertEqual(code,1)
            self.assertEqual(client.calls,[])

    def test_batch_dependency_and_chatgpt_use_same_state_source(self):
        gen=_load_gen()
        import chatgpt_queue
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root)
            plans=gen.collect_asset_image_plan(root)
            waves=gen.plan_waves(plans)
            self.assertEqual([p['id'] for p in waves[0]],['hero'])
            self.assertEqual([p['id'] for p in waves[1]],['bow'])
            plan=chatgpt_queue.resolve_asset_execution_plan(str(root),'@prop:bow')
            self.assertEqual([r['path'] for r in plan['refs']],['素材/人物/hero__armed.png'])
            self.assertEqual(plan['reference_tokens'],['@character:hero#armed'])

    def test_service_saves_and_rejects_unknown_state(self):
        import asset_service
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.fixture(root)
            result=asset_service.edit_asset(str(root),'@prop:bow',{'derived_from':'@character:hero'})
            result=asset_service.edit_asset(str(root),'@prop:bow',{'derived_from':'@character:hero#armed'})
            self.assertEqual(result['asset']['derived_from'],'@character:hero#armed')
            with self.assertRaises(ValueError):
                asset_service.edit_asset(str(root),'@prop:bow',{'derived_from':'@character:hero#missing'})
