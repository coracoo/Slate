# -*- coding: utf-8 -*-
"""角色表演生成和有限修正的离线回归测试。"""

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "previs_system" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _request():
    return {
        "shot_id": "S1",
        "dur": 4,
        "actor_ids": ["a"],
        "allowed_fact_ids": [],
        "locked": {"pos": [0, 2, 1], "dur": 4},
        "actors": [{"actor_id": "a", "role": "克制的职业演员", "known_facts": []}],
    }


def _valid_packet():
    return {
        "shot_id": "S1",
        "actors": [{
            "actor_id": "a",
            "evidence_fact_ids": [],
            "beats": [
                {"at": 0, "duration": 1, "visible_action": "保持直视"},
                {"at": 1, "duration": 1, "visible_action": "轻轻转开视线"},
            ],
            "intent": "想追问但克制住",
        }],
    }


class ActorRuntimeTests(unittest.TestCase):
    def test_invalid_first_response_is_corrected_once(self):
        from actor_runtime import perform

        responses = iter([
            {"content": '{"shot_id":"S1","actors":[{"actor_id":"missing"}]}', "finish_reason": "stop"},
            {"content": json.dumps(_valid_packet(), ensure_ascii=False), "finish_reason": "stop"},
        ])
        messages_seen = []

        def call_llm(messages):
            messages_seen.append(messages)
            return next(responses)

        result = perform(_request(), call_llm, max_attempts=2)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(messages_seen), 2)
        self.assertIn("职业演员", messages_seen[0][0]["content"])
        self.assertIn("UNKNOWN_ACTOR", messages_seen[1][-1]["content"])

    def test_partial_response_is_invalid_after_two_attempts(self):
        from actor_runtime import perform

        calls = []

        def call_llm(messages):
            calls.append(messages)
            return {"content": '{"shot_id":"S1","actors":[', "finish_reason": "length"}

        result = perform(_request(), call_llm, max_attempts=9)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(calls), 2)
        self.assertIn("PARTIAL_OUTPUT", {e["code"] for e in result["errors"]})

    def test_semantic_self_check_is_warning_and_not_authority(self):
        from actor_runtime import perform

        packet = _valid_packet()
        packet["ooc_check"] = {"passed": False, "reason": "语义上可能崩人设"}
        result = perform(
            _request(),
            lambda messages: {"content": json.dumps(packet, ensure_ascii=False), "finish_reason": "stop"},
        )
        self.assertEqual(result["status"], "ready")
        self.assertTrue(any(item["code"] == "SEMANTIC_REVIEW" for item in result["warnings"]))


    def test_accepts_structured_object_with_leading_text(self):
        from actor_runtime import perform

        packet = json.dumps(_valid_packet(), ensure_ascii=False)
        result = perform(
            _request(),
            lambda messages: {"content": "模型说明：\n" + packet, "finish_reason": "stop"},
        )
        self.assertEqual(result["status"], "ready")
if __name__ == "__main__":
    unittest.main()


