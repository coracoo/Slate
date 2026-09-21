# -*- coding: utf-8 -*-
"""角色状态和镜内知情范围的离线回归测试。"""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "previs_system" / "tools"
WORKBENCH_TOOLS = ROOT / "workbench" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(WORKBENCH_TOOLS) not in sys.path:
    sys.path.insert(0, str(WORKBENCH_TOOLS))


def _context():
    return {
        "continuities": [
            {
                "id": "main",
                "initial_state": {
                    "a": {"known_facts": [], "props": {"holding": "phone"}},
                    "b": {"known_facts": [], "props": {}},
                },
            },
            {"id": "flashback", "initial_state": {"a": {"known_facts": []}}},
        ],
        "facts": [
            {"id": "f1", "text": "乙承认昨晚不在家"},
            {"id": "secret", "text": "甲曾经隐瞒了证据"},
        ],
        "events": [
            {
                "id": "ev1", "continuity_id": "main", "order": 1,
                "deltas": [{"actor_id": "a", "known_facts_add": ["f1"]}],
            },
            {
                "id": "ev2", "continuity_id": "main", "order": 2,
                "deltas": [{"actor_id": "a", "emotion": "紧张"}],
            },
        ],
    }


class ActorStateTests(unittest.TestCase):
    def test_state_at_orders_and_deduplicates_events(self):
        from actor_state import state_at

        before = state_at(_context(), "main", [])
        after = state_at(_context(), "main", ["ev1", "ev1"])
        self.assertEqual(before["a"]["known_facts"], [])
        self.assertEqual(after["a"]["known_facts"], ["f1"])
        self.assertIsNone(after["a"].get("emotion"))

    def test_state_at_rejects_cross_continuity_event(self):
        from actor_state import state_at

        with self.assertRaises(ValueError):
            state_at(_context(), "main", ["flashback-event"])

    def test_visible_context_does_not_leak_unknown_fact_text(self):
        from actor_state import state_at, visible_context

        state = state_at(_context(), "main", [])
        visible = visible_context(state, "a", _context()["facts"])
        visible_text = json.dumps(visible, ensure_ascii=False)
        self.assertNotIn("乙承认昨晚不在家", visible_text)
        self.assertNotIn("隐瞒了证据", visible_text)
        self.assertEqual(visible["known_facts"], [])

        state = state_at(_context(), "main", ["ev1"])
        visible = visible_context(state, "a", _context()["facts"])
        self.assertIn("乙承认昨晚不在家", json.dumps(visible, ensure_ascii=False))
        self.assertNotIn("隐瞒了证据", json.dumps(visible, ensure_ascii=False))

    def test_merge_proposals_detects_same_object_ownership_conflict(self):
        from actor_state import merge_proposals, state_at

        state = state_at(_context(), "main", [])
        proposals = [
            {"actor_id": "a", "proposed_delta": {"props_set": {"holding": "phone"}}},
            {"actor_id": "b", "proposed_delta": {"props_set": {"holding": "phone"}}},
        ]
        result = merge_proposals(state, proposals, {"mutable_fields": ["emotion", "gaze"]})
        self.assertIn("OBJECT_OWNERSHIP_CONFLICT", {e["code"] for e in result["errors"]})


    def test_beat_event_snapshots_switch_knowledge_only_after_event(self):
        from actor_pipeline import build_request
        context = _context()
        board = {"actors": {"a": {"name": "甲"}, "b": {"name": "乙"}}, "shots": [{
            "id": "S1", "dur": 4, "speaker": "a",
            "acting_beats": [
                {"at": 0, "duration": 1, "after_event_ids": []},
                {"at": 1, "duration": 1, "after_event_ids": ["ev1"]}
            ]
        }]}
        request = build_request(board, "S1", context=context)
        self.assertEqual(request["beat_contexts"][0]["allowed_fact_ids_by_actor"]["a"], [])
        self.assertIn("f1", request["beat_contexts"][1]["allowed_fact_ids_by_actor"]["a"])
        self.assertEqual(request["allowed_fact_ids_by_actor_beat"]["a"]["0"], [])
        self.assertIn("f1", request["allowed_fact_ids_by_actor_beat"]["a"]["1"])

    def test_beat_evidence_uses_time_specific_permissions(self):
        from actor_contract import validate_performance
        request = {"actor_ids": ["a"], "shot_id": "S1", "dur": 4,
                   "allowed_event_ids": ["ev1"], "allowed_fact_ids_by_actor": {"a": ["f1"]},
                   "allowed_fact_ids_by_actor_beat": {"a": {"0": [], "1": ["f1"]}}}
        packet = {"shot_id": "S1", "actors": [{"actor_id": "a", "beats": [
            {"at": 0, "duration": 1, "evidence_fact_ids": ["f1"]},
            {"at": 1, "duration": 1, "evidence_fact_ids": ["f1"], "after_event_ids": ["ev1"]}
        ]}]}
        issues = validate_performance(packet, request)
        self.assertEqual(sum(item["code"] == "KNOWLEDGE_LEAK" for item in issues), 1)
        self.assertFalse(any(item["code"] == "UNKNOWN_EVENT" for item in issues))
    def test_merge_proposals_detects_existing_owner_and_rejects_fact_delta(self):
        from actor_state import merge_proposals

        state = {"a": {"props": {"holding": "phone"}}, "b": {"props": {}}}
        proposals = [
            {"actor_id": "b", "proposed_delta": {"props_set": {"holding": "phone"}}},
            {"actor_id": "a", "proposed_delta": {"known_facts_add": ["secret"]}},
        ]
        result = merge_proposals(state, proposals, {"mutable_fields": ["emotion", "gaze"]})
        codes = {item["code"] for item in result["errors"]}
        self.assertIn("OBJECT_OWNERSHIP_CONFLICT", codes)
        self.assertIn("KNOWLEDGE_DELTA_FORBIDDEN", codes)
if __name__ == "__main__":
    unittest.main()






