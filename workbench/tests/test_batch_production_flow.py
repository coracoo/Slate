# -*- coding: utf-8 -*-
"""批量规划写入与上下游依据的离线验收。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import story_units
import story_planning_versions as planning
from production_jobs import prompt_basis


class BatchFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / '剧本').mkdir()
        (self.project / '素材').mkdir()
        self.path = self.project / '剧本/分集.json'
        self.path.write_text(json.dumps({'rev': 4, 'episodes': [
            {'id': 'E1', 'summary': '旧概要', 'text': '完整正文' * 700, 'cast_refs': ['@character:a']},
            {'id': 'E2', 'summary': '不可改', 'text': '原正文'}]}), encoding='utf-8')
        (self.project / '素材/人物.json').write_text(json.dumps({'characters': [{'id': 'a', 'biography': '小传尾部', 'states': [{'id': 'hurt', 'look_diff': '左袖破损'}]}]}), encoding='utf-8')

    def test_selected_plan_changes_preserve_body_and_other_episode(self):
        original = json.loads(self.path.read_text('utf-8'))
        revision = planning.current_revision(self.project)
        result = story_units.edit_episodes_batch(self.project, [{'id': 'E1', 'fields': {'summary': '新概要'}}], revision)
        stored = json.loads(self.path.read_text('utf-8'))
        self.assertEqual(result['applied'], ['E1'])
        self.assertEqual(stored['episodes'][0]['text'], original['episodes'][0]['text'])
        self.assertTrue(stored['episodes'][0]['planning_review_required'])
        self.assertEqual(stored['episodes'][1], original['episodes'][1])
        self.assertEqual(stored['script_edit_revisions']['5'], ['E1'])
        self.assertTrue(list((self.project / '剧本/.versions').rglob('*')))
        with self.assertRaises(story_units._project_store().RevisionConflict):
            story_units.edit_episodes_batch(self.project, [{'id': 'E2', 'fields': {'summary': '旧基线覆盖'}}], revision)

    def test_invalid_second_item_does_not_write_first(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            story_units.edit_episodes_batch(self.project, [{'id': 'E1', 'fields': {'summary': '新'}}, {'id': 'MISSING', 'fields': {'summary': '坏'}}], planning.current_revision(self.project))
        self.assertEqual(self.path.read_bytes(), before)

    def test_prompt_basis_contains_full_adopted_episode_and_derived_assets(self):
        shots = [{'id': 'S1', 'actor_refs': ['@character:a']}, {'id': 'S2', 'action': '承接上一镜'}]
        basis = prompt_basis(self.project, {'shots': shots}, '剧本_E1.json')
        self.assertEqual(basis['episode']['text'], '完整正文' * 700)
        self.assertEqual(basis['assets']['@character:a']['states'][0]['look_diff'], '左袖破损')
        self.assertEqual(basis['episode_shots'], shots)
