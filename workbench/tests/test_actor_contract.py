# -*- coding: utf-8 -*-
"""角色表演契约的离线回归测试。"""

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "previs_system" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _board():
    return {
        "actors": {
            "a": {"name": "甲", "role": "克制的人"},
            "b": {"name": "乙", "role": "外向的人"},
        },
        "shots": [
            {"id": "S1", "dur": 4, "pos": [0, 2, 1], "look": [0, 0, 1]},
            {"id": "S2", "dur": 3, "pos": [1, 2, 1], "look": [0, 0, 1]},
        ],
    }


class ActorContractTests(unittest.TestCase):
    def test_rejects_unknown_actor_and_out_of_shot_beat(self):
        from actor_contract import validate_performance

        request = {
            "actor_ids": ["a"],
            "shot_id": "S1",
            "dur": 4,
            "allowed_fact_ids": [],
            "locked": {"pos": [0, 2, 1]},
        }
        packet = {
            "shot_id": "S1",
            "actors": [{
                "actor_id": "missing",
                "evidence_fact_ids": ["secret"],
                "beats": [{"at": 3, "duration": 2, "visible_action": "转头"}],
            }],
        }
        codes = {item["code"] for item in validate_performance(packet, request)}
        self.assertIn("UNKNOWN_ACTOR", codes)
        self.assertIn("KNOWLEDGE_LEAK", codes)
        self.assertIn("BEAT_OUT_OF_SHOT", codes)

    def test_rejects_non_finite_beat_values(self):
        from actor_contract import validate_performance

        request = {"actor_ids": ["a"], "shot_id": "S1", "dur": 4}
        packet = {
            "shot_id": "S1",
            "actors": [{"actor_id": "a", "beats": [
                {"at": math.inf, "duration": 1, "visible_action": "转头"},
            ]}],
        }
        codes = {item["code"] for item in validate_performance(packet, request)}
        self.assertIn("NON_FINITE_NUMBER", codes)

    def test_rejects_fixed_camera_mutation_and_duplicate_event(self):
        from actor_contract import validate_performance, validate_context

        request = {
            "actor_ids": ["a"],
            "shot_id": "S1",
            "dur": 4,
            "locked": {"pos": [0, 2, 1], "dur": 4},
        }
        packet = {
            "shot_id": "S1",
            "actors": [{"actor_id": "a", "beats": []}],
            "pos": [2, 2, 1],
            "dur": 2,
        }
        codes = {item["code"] for item in validate_performance(packet, request)}
        self.assertIn("FIXED_FIELD_MUTATION", codes)

        context = {
            "continuities": [{"id": "main", "initial_state": {"a": {}}}],
            "facts": [],
            "events": [
                {"id": "ev1", "continuity_id": "main", "order": 1, "deltas": []},
                {"id": "ev1", "continuity_id": "main", "order": 2, "deltas": []},
            ],
        }
        context_codes = {item["code"] for item in validate_context(context, _board())}
        self.assertIn("DUPLICATE_EVENT_ID", context_codes)

    def test_validates_actor_card_source_and_locked_fields(self):
        from actor_contract import validate_context
        context = {
            "continuities": [{"id": "main", "initial_state": {"a": {}}}],
            "actor_cards": {
                "a": {"source": "人物档案", "locked_fields": ["personality", "unknown_field"]},
                "missing": {"source": 123}
            },
            "facts": [], "events": []
        }
        codes = {item["code"] for item in validate_context(context, _board())}
        self.assertIn("INVALID_LOCKED_FIELD", codes)
        self.assertIn("UNKNOWN_ACTOR", codes)
    def test_reports_missing_continuity_and_unknown_fact_references(self):
        from actor_contract import validate_context

        context = {
            "continuities": [],
            "facts": [{"id": "f1", "text": "公开消息"}],
            "events": [{
                "id": "ev1", "continuity_id": "main", "order": 1,
                "deltas": [{"actor_id": "a", "known_facts_add": ["secret"]}],
            }],
        }
        codes = {item["code"] for item in validate_context(context, _board())}
        self.assertIn("MISSING_CONTINUITY", codes)
        self.assertIn("UNKNOWN_FACT", codes)

    def test_rejects_empty_actors_and_missing_speaker(self):
        """在场角色非空时拒绝空包；说话人必须出现在表演包里。"""
        from actor_contract import validate_performance

        request = {
            "actor_ids": ["a", "b"], "shot_id": "S1", "dur": 4,
            "lines": [{"speaker": "a", "text": "台词"}],
        }
        empty_codes = {item["code"] for item in validate_performance({"shot_id": "S1", "actors": []}, request)}
        self.assertIn("EMPTY_ACTORS", empty_codes)
        self.assertIn("MISSING_SPEAKER", empty_codes)

        missing_codes = {item["code"] for item in validate_performance(
            {"shot_id": "S1", "actors": [{"actor_id": "b", "beats": []}]}, request)}
        self.assertNotIn("EMPTY_ACTORS", missing_codes)
        self.assertIn("MISSING_SPEAKER", missing_codes)

        ok_codes = {item["code"] for item in validate_performance(
            {"shot_id": "S1", "actors": [{"actor_id": "a", "beats": []}]}, request)}
        self.assertNotIn("EMPTY_ACTORS", ok_codes)
        self.assertNotIn("MISSING_SPEAKER", ok_codes)

        # 空场镜头（无在场角色、无台词）：actors 为空是合法的，不报 EMPTY_ACTORS。
        quiet = {"actor_ids": [], "shot_id": "S1", "dur": 4, "lines": []}
        quiet_codes = {item["code"] for item in validate_performance({"shot_id": "S1", "actors": []}, quiet)}
        self.assertNotIn("EMPTY_ACTORS", quiet_codes)

    def test_missing_speaker_normalizes_character_prefix(self):
        from actor_contract import validate_performance

        request = {
            "actor_ids": ["a"], "shot_id": "S1", "dur": 4,
            "lines": [{"speaker": "@character:a", "text": "台词"}],
        }
        codes = {item["code"] for item in validate_performance(
            {"shot_id": "S1", "actors": [{"actor_id": "a", "beats": []}]}, request)}
        self.assertNotIn("MISSING_SPEAKER", codes)


if __name__ == "__main__":
    unittest.main()

