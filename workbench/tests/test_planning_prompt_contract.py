# -*- coding: utf-8 -*-
"""骨架与分集补批的输出边界及跨批引用契约。"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import prompt_modules as pm
from llm_result import parse_structured


class PlanningPromptContractTests(unittest.TestCase):
    def schema(self, system):
        return parse_structured(system.split('输出结构：', 1)[1])['data']

    def test_skeleton_does_not_require_episode_cards_or_out_of_range_examples(self):
        system, user = pm.units_skeleton_prompt('6集短篇，到发现真相为止', '', eps_n=15)
        schema = self.schema(system)
        self.assertNotIn('episodes', schema)
        self.assertNotIn('hooks', schema)
        self.assertEqual(schema['foreshadows'][0]['pay_in'], 'E15')
        self.assertIn('制作规则的目标集数优先', system)
        self.assertIn('15', user)

    def test_episode_batch_returns_only_cards_and_hooks_with_existing_world_as_input(self):
        outline = {'premise': '发现真相', 'arcs': [{'id': 'ARC1', 'ep_from': 'E1', 'ep_to': 'E15'}],
                   'rules': [{'id': 'R1', 'text': '能力只能判真假'}]}
        system, user = pm.units_episode_prompt('寻找证据', '', outline=outline,
            episode_ids=['E4', 'E5'], arc=outline['arcs'][0], eps_n=15,
            known=['@character:hero | 甲'], foreshadows=[{'id': 'FS1', 'set_in': 'E1', 'pay_in': 'E15'}])
        self.assertEqual(set(self.schema(system)), {'episodes', 'hooks'})
        self.assertNotIn('arc_id', self.schema(system)['episodes'][0])
        self.assertIn('E4、E5', system)
        self.assertIn('能力只能判真假', user)
        self.assertIn('FS1', user)
        self.assertIn('E15', user)
        self.assertNotIn('set_in/pay_in 都存在于本批', system)

    def test_single_episode_skeleton_does_not_invent_cross_episode_foreshadow(self):
        system, _ = pm.units_skeleton_prompt('甲发现真相', '', eps_n=1)
        self.assertEqual(self.schema(system)['foreshadows'], [])


if __name__ == '__main__':
    unittest.main()
