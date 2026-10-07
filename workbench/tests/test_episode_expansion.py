# -*- coding: utf-8 -*-
"""单集扩写以分集卡为准，不能重新发现或覆盖全剧结构。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import creation_pipeline as pipeline


class EpisodeExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.base = self.project / '剧本'
        self.base.mkdir()

    def write(self, name, data):
        (self.base / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def expand(self, episode='E1'):
        with patch.object(pipeline, 'pick_vendor', return_value={}), \
             patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', return_value='甲：新的单集正文') as chat, \
             patch.object(pipeline.story_units, 'units_block', return_value=''), \
             patch.object(pipeline.story_units, 'gap_report', return_value={'blocking': False}):
            pipeline.cmd_expand(str(self.project), None, None, 6, episode)
            return chat

    def test_expansion_preserves_latest_cards_and_does_not_rewrite_outline(self):
        (self.base / '构想.txt').write_text('主角守住秘密的故事', encoding='utf-8')
        self.write('大纲.json', {'main_line': '守住秘密', 'episodes': [{'id': 'E1', 'summary': '旧概要'}]})
        self.write('分集.json', {'mode': 'generated', 'anchor_rev': 3, 'episodes': [
            {'id': 'E1', 'summary': '新概要', 'beats': ['发现证据'], 'fs_plant': ['F1'], 'text': ''},
            {'id': 'E2', 'summary': '延续', 'text': '保留第二集正文'}]})
        before = (self.base / '大纲.json').read_bytes()
        chat = self.expand()
        book = json.loads((self.base / '分集.json').read_text(encoding='utf-8'))
        self.assertEqual(chat.call_count, 1)
        self.assertIn('新概要', str(chat.call_args.args))
        self.assertEqual(book['anchor_rev'], 3)
        self.assertEqual(book['episodes'][0]['fs_plant'], ['F1'])
        self.assertEqual(book['episodes'][1]['text'], '保留第二集正文')
        self.assertEqual((self.base / '大纲.json').read_bytes(), before)

    def test_imported_episode_expands_without_extra_idea_or_outline(self):
        (self.base / '剧本.txt').write_text('甲：导入剧本原文，继续讲述原有故事。', encoding='utf-8')
        self.write('分集.json', {'mode': 'imported', 'episodes': [
            {'id': 'E1', 'summary': '导入概要', 'text': '甲：原文'},
            {'id': 'E2', 'text': '乙：第二集原文'}]})
        self.expand()
        book = json.loads((self.base / '分集.json').read_text(encoding='utf-8'))
        self.assertEqual(book['mode'], 'generated')
        self.assertEqual(book['episodes'][1]['text'], '乙：第二集原文')
        self.assertFalse((self.base / '大纲.json').exists())
        self.assertFalse((self.base / '构想.txt').exists())

    def test_expanding_imported_slice_preserves_other_episode_text(self):
        (self.base / '剧本.txt').write_text('甲：第一集乙：第二集', encoding='utf-8')
        self.write('分集.json', {'mode': 'imported', 'episodes': [
            {'id': 'E1', 'char_start': 0, 'char_end': 5},
            {'id': 'E2', 'char_start': 5, 'char_end': 10}]})
        self.expand()
        book = json.loads((self.base / '分集.json').read_text(encoding='utf-8'))
        self.assertEqual(book['episodes'][1]['text'], '乙：第二集')

    def test_text_copy_uses_latest_manual_edit_after_expansion_commit(self):
        import episode_editor as editor
        (self.base / '构想.txt').write_text('主角守住秘密的故事', encoding='utf-8')
        self.write('分集.json', {'mode': 'generated', 'episodes': [{'id': 'E1', 'text': '甲：旧正文'}]})
        original = pipeline._dump_episodes
        def commit_then_edit(*args, **kwargs):
            original(*args, **kwargs)
            editor.save_episode(self.project, 'E1', '甲：扩写完成后人工修改的新稿',
                                editor.project_store.current_revision(self.base / '分集.json'))
        with patch.object(pipeline, '_dump_episodes', side_effect=commit_then_edit):
            self.expand()
        self.assertEqual((self.base / '分集剧本_E1.txt').read_text(encoding='utf-8'), '甲：扩写完成后人工修改的新稿')

    def test_missing_episode_stops_before_llm_and_file_writes(self):
        self.write('分集.json', {'episodes': [{'id': 'E1', 'summary': '只有第一集'}]})
        before = (self.base / '分集.json').read_bytes()
        with patch.object(pipeline, 'chat_retry') as chat:
            with self.assertRaisesRegex(ValueError, '分集'):
                pipeline.cmd_expand(str(self.project), None, '一个故事构想', 6, 'E9')
            chat.assert_not_called()
        self.assertEqual((self.base / '分集.json').read_bytes(), before)
        self.assertFalse((self.base / '构想.txt').exists())

    def test_split_records_imported_source_without_turning_text_into_generated(self):
        text = '甲：导入原稿中的场景和台词。' * 10
        (self.base / '剧本.txt').write_text(text, encoding='utf-8')
        self.write('source.json', {'mode': 'imported'})
        with patch.object(pipeline, 'pick_vendor', return_value={}), \
             patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', return_value=json.dumps({'episodes': [{'id': 'E1', 'start': text[:10], 'end': text[-10:]}]})):
            pipeline.cmd_episodes(str(self.project), None, str(self.base / '分集.json'))
        self.assertEqual(pipeline.script_repository.script_mode(str(self.project)), 'imported')
        self.assertEqual(pipeline.script_repository.load_script(str(self.project)), text)

    def test_overview_rejects_body_edited_during_model_call_and_retains_generated_diagnostic(self):
        self.write('分集.json', {'mode': 'generated', 'episodes': [{'id': 'E1', 'text': '原正文'}]})
        def generated(*args, **kwargs):
            self.write('分集.json', {'mode': 'generated', 'episodes': [{'id': 'E1', 'text': '人工新正文'}]})
            return json.dumps({'summary': '模型生成的概要'})
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
                patch.object(pipeline, 'chat_retry', side_effect=generated), \
                self.assertRaises(pipeline.project_store.RevisionConflict):
            pipeline.cmd_overview(str(self.project), None, 'E1')
        book = json.loads((self.base / '分集.json').read_text('utf-8'))
        self.assertEqual(book['episodes'][0]['text'], '人工新正文')
        saved = list((self.base / '.work/生成冲突').glob('*.json'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text('utf-8'))['data']['episodes'][0]['summary'], '模型生成的概要')

    def test_split_rejects_source_imported_during_model_call(self):
        text = '原始故事中的完整台词和事件。' * 10
        path = self.base / '剧本.txt'
        path.write_text(text, encoding='utf-8')
        self.write('source.json', {'mode': 'imported'})
        def generated(*args, **kwargs):
            path.write_text('人工重新导入的新剧本', encoding='utf-8')
            return json.dumps({'episodes': [{'id': 'E1', 'start': text[:10], 'end': text[-10:]}]})
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
                patch.object(pipeline, 'chat_retry', side_effect=generated), \
                self.assertRaises(pipeline.project_store.RevisionConflict):
            pipeline.cmd_episodes(str(self.project), None, str(self.base / '分集.json'))
        self.assertFalse((self.base / '分集.json').exists())
        self.assertEqual(path.read_text('utf-8'), '人工重新导入的新剧本')

    def test_units_uses_independent_script_methods_and_can_save_idea(self):
        class StopGeneration(Exception):
            pass
        with patch.object(pipeline, 'pick_vendor', return_value={}), \
             patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline.skill_lib, 'style_for', return_value='选中的独立节奏策略'), \
             patch.object(pipeline.PM, 'units_story_prompt', side_effect=StopGeneration) as prompt:
            with self.assertRaises(StopGeneration):
                pipeline.cmd_units(str(self.project), None, eps_n=2, idea_text='主角守住秘密的故事')
        self.assertEqual((self.base / '构想.txt').read_text(encoding='utf-8'), '主角守住秘密的故事')
        self.assertEqual(prompt.call_args.kwargs['style_text'], '选中的独立节奏策略')


if __name__ == '__main__':
    unittest.main()
