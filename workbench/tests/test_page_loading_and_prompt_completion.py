# -*- coding: utf-8 -*-
"""请求内读取复用和旧分镜提示词补齐。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'tools'), str(Path(__file__).resolve().parents[2]/'previs_system/tools')]
import skill_lib
import project_store
from production_jobs import llm_task


class LoadingAndPromptTests(unittest.TestCase):
    def test_skill_scope_reuses_reads_but_next_request_observes_edit(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)/'image-style'
            folder.mkdir()
            path = folder/'example.md'
            path.write_text('---\nid: example\nname: 原风格\n---\n正文', encoding='utf-8')
            with patch.object(skill_lib, 'SKILLS_DIR', td), patch('builtins.open', wraps=open) as opened:
                with skill_lib.read_scope():
                    first = skill_lib.list_skills()
                    first[0]['name'] = '调用方修改'
                    with skill_lib.read_scope():
                        self.assertEqual(skill_lib.list_skills()[0]['name'], '原风格')
                    self.assertEqual(opened.call_count, 1)
                path.write_text('---\nid: example\nname: 新风格\n---\n正文', encoding='utf-8')
                with skill_lib.read_scope():
                    self.assertEqual(skill_lib.list_skills()[0]['name'], '新风格')
                    self.assertEqual(opened.call_count, 2)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root/'分镜/剧本_E1.json'
        self.path.parent.mkdir()
        self.board = {'shots': [{'id': f'S{i}', 'dur': 4, 'action': '固定动作', 'prompt_image': '原关键帧', 'prompt_video': '原视频', 'prompt_grid': ''} for i in range(1, 6)], 'video_units': [{'id': 'v1', 'shot_ids': ['S1', 'S2'], 'prompt_grid': '原V宫格'}]}
        self.path.write_text(json.dumps(self.board, ensure_ascii=False), encoding='utf-8')

    def packet(self):
        snapshot, revision = project_store.read_json(self.path)
        return {'action': 'prompts', 'only_missing': True, 'snapshot': snapshot, 'board': self.path.name, 'board_revision': revision}

    def response(self, messages, **kwargs):
        rows = json.loads(messages[1]['content'])['shots']
        self.assertLessEqual(len(rows), 4)
        return json.dumps({'shots': [{'id': r['id'], 'prompt_grid': '3×3；镜内关键瞬间', 'prompt_image': '不应覆盖原文'} for r in rows]})

    def test_fills_missing_in_batches_and_preserves_existing_fields_and_units(self):
        client = Mock(); client.chat.side_effect = self.response
        llm_task(self.root, self.packet(), client)
        data, _ = project_store.read_json(self.path)
        self.assertEqual(client.chat.call_count, 2)
        expected = copy.deepcopy(self.board)
        for row in expected['shots']:
            row.update(prompt_grid='3×3；镜内关键瞬间', prompt_grid_source='llm')
        self.assertEqual(data, expected)
        llm_task(self.root, self.packet(), client)
        self.assertEqual(client.chat.call_count, 2)

    def test_failed_batch_keeps_completed_batch_for_next_retry(self):
        client = Mock()
        count = 0
        def respond(messages, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('模拟生成失败')
            return self.response(messages, **kwargs)
        client.chat.side_effect = respond
        with self.assertRaisesRegex(ValueError, '模拟生成失败'):
            llm_task(self.root, self.packet(), client)
        data, _ = project_store.read_json(self.path)
        self.assertEqual(sum(bool(s['prompt_grid']) for s in data['shots']), 4)
        client.chat.side_effect = self.response
        llm_task(self.root, self.packet(), client)
        self.assertEqual(client.chat.call_count, 3)

    def test_concurrent_edit_stops_write(self):
        packet = self.packet()
        project_store.update_json(self.path, lambda b: b['shots'][0].update(prompt_grid='用户新增'))
        client = Mock(); client.chat.side_effect = self.response
        with self.assertRaises(project_store.RevisionConflict):
            llm_task(self.root, packet, client)
        self.assertEqual(project_store.read_json(self.path)[0]['shots'][0]['prompt_grid'], '用户新增')
