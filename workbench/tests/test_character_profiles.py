# -*- coding: utf-8 -*-
"""角色工作区的数据保护及已有下游字段兼容回归。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import character_profiles as profiles


class CharacterProfilesTests(unittest.TestCase):
    def test_derivative_editor_reports_validation_and_rechecks_after_save(self):
        data = self.initial
        data['characters'][0]['states'] = [{'id':'young','label':'持弩','look_diff':'手持连弩；随后连弩脱手'}]
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        screen = profiles.state(self.project)
        state = screen['characters'][0]['states'][0]
        self.assertTrue(any('前后动作' in w for w in state['visual_status']['warnings']))
        profiles.save(self.project, 'a', {'states':[{'id':'young','look_diff':'手持装三枚封仙钉的连弩'}]}, expected_revision=screen['revision'])
        saved = profiles.state(self.project)['characters'][0]['states'][0]
        self.assertFalse(any('前后动作' in w for w in saved['visual_status']['warnings']))
        self.assertNotIn('visual_status', json.loads(self.path.read_text('utf-8'))['characters'][0]['states'][0])

    def test_batch_confirmation_is_one_atomic_write_with_one_snapshot(self):
        revision = profiles.state(self.project)['revision']
        result = profiles.save_batch(self.project, [
            {'character_id': 'a', 'patch': {'appearance': {'face': '窄脸', 'sources': {'face': 'authored'}}}},
            {'character_id': 'b', 'patch': {'appearance': {'hair': '短卷发', 'sources': {'hair': 'authored'}}}},
        ], expected_revision=revision)
        self.assertEqual(result['saved'], ['a', 'b'])
        self.assertEqual(len(list((self.path.parent / '.versions').glob('*.json'))), 1)
        self.assertEqual(profiles.state(self.project)['characters'][1]['appearance']['hair'], '短卷发')
        self.assertEqual(profiles.state(self.project)['characters'][0]['voice_binding']['voice_asset_id'], 'voice1')

    def test_invalid_or_stale_batch_does_not_partially_adopt_characters(self):
        revision = profiles.state(self.project)['revision']
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            profiles.save_batch(self.project, [
                {'character_id': 'a', 'patch': {'biography': '不能部分写入'}},
                {'character_id': 'missing', 'patch': {'biography': '不存在'}},
            ], expected_revision=revision)
        self.assertEqual(self.path.read_bytes(), before)
        profiles.save(self.project, 'b', {'biography': '新的资料'}, expected_revision=revision)
        with self.assertRaises(profiles.project_store.RevisionConflict):
            profiles.save_batch(self.project, [{'character_id': 'a', 'patch': {'biography': '旧审核'}}], expected_revision=revision)
        self.assertNotIn('biography', profiles.state(self.project)['characters'][0])

    def test_batch_rejects_duplicate_targets_and_empty_selection(self):
        revision = profiles.state(self.project)['revision']
        for items in ([], [{'character_id':'a', 'patch':{'voice':'甲'}}, {'character_id':'a','patch':{'voice':'乙'}}]):
            with self.assertRaises(ValueError):
                profiles.save_batch(self.project, items, expected_revision=revision)

    def test_state_edits_preserve_identifiers_and_other_states(self):
        self.initial['characters'][0]['states'] = [
            {'id':'young','look_diff':'旧衣','output_asset_ref':'@character:a'},
            {'id':'injured','look_diff':'左臂包扎','locked_fields':['look_diff']}]
        self.path.write_text(json.dumps(self.initial, ensure_ascii=False), encoding='utf-8')
        rev = profiles.state(self.project)['revision']
        result = profiles.save(self.project, 'a', {'states':[
            {'id':'young','look_diff':'新灰衣'}, {'id':'injured','look_diff':'左臂包扎'}]}, expected_revision=rev)
        self.assertEqual(result['character']['states'][0]['output_asset_ref'], '@character:a')
        self.assertEqual(result['character']['states'][1], self.initial['characters'][0]['states'][1])
        with self.assertRaisesRegex(ValueError, '不能更换 ID'):
            profiles.save(self.project, 'a', {'states':[{'id':'new'}]}, expected_revision=result['revision'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.path = self.project / "素材" / "人物.json"
        self.path.parent.mkdir()
        self.initial = {"anchor_rev": "keep", "characters": [
            {"id": "a", "name": "甲", "acting": {"goal": "旧目标", "custom": "保留"},
             "appearance": {"look": "旧外观", "custom": "保留"},
             "voice_binding": {"voice_asset_id": "voice1"}, "states": [{"id": "young"}]},
            {"id": "b", "name": "乙"}]}
        self.path.write_text(json.dumps(self.initial, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_save_preserves_existing_fields_and_makes_snapshot(self):
        original = profiles.state(self.project)
        result = profiles.save(self.project, "a", {"biography": "新小传", "acting": {"personality": "沉稳"},
            "appearance": {"look": "新外观"}, "relations": [{"to_ref": "@character:b", "kind": "师徒"}]},
            expected_revision=original["revision"])
        row = result["character"]
        self.assertEqual(row["acting"], {"goal": "旧目标", "personality": "沉稳", "custom": "保留"})
        self.assertEqual(row["voice_binding"], {"voice_asset_id": "voice1"})
        self.assertEqual(row["appearance"]["custom"], "保留")
        self.assertIn("biography", row["locked_fields"])
        self.assertNotEqual(original["revision"], result["revision"])
        self.assertTrue(list((self.path.parent / ".versions").glob("*.json")))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["anchor_rev"], "keep")

    def test_concurrent_voice_change_rejects_stale_text_edit(self):
        revision = profiles.state(self.project)["revision"]
        profiles.project_store.update_json(self.path, lambda doc: doc["characters"][0].update(voice="已从另页修改"))
        with self.assertRaises(profiles.project_store.RevisionConflict):
            profiles.save(self.project, "a", {"biography": "不得写入"}, expected_revision=revision)
        self.assertNotIn("biography", profiles.state(self.project)["characters"][0])

    def test_narrator_is_response_only_and_cannot_be_target(self):
        sidecar = self.project / "素材" / "音色" / "旁白.json"
        sidecar.parent.mkdir()
        sidecar.write_text('{"voice_binding":{"voice_asset_id":"narrator-voice","revision":2}}', encoding="utf-8")
        data = profiles.state(self.project)
        self.assertTrue(data["characters"][-1]["reserved"])
        self.assertEqual(data["characters"][-1]["voice_binding"]["voice_asset_id"], "narrator-voice")
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))["characters"]), 2)
        with self.assertRaises(ValueError):
            profiles.save(self.project, "narrator", {"biography": "旁白"}, expected_revision=data["revision"])

    def test_rejects_unknown_fields_missing_revision_and_bad_relation(self):
        revision = profiles.state(self.project)["revision"]
        for patch in ({"id": "changed"}, {"acting": {"facts": "秘密"}},
                      {"relations": [{"to_ref": "@character:narrator"}]}):
            with self.assertRaises(ValueError):
                profiles.save(self.project, "a", patch, expected_revision=revision)
        with self.assertRaises(ValueError):
            profiles.save(self.project, "a", {"biography": "新"})
        self.assertEqual(profiles.state(self.project)["revision"], revision)

    def test_saving_character_does_not_mutate_adopted_shot_performance(self):
        path = self.project / "分镜" / "剧本_一.json"
        path.parent.mkdir()
        content = '{"acting_context":{"actor_cards":{"a":{"goal":"镜内目标"}}},"shots":[{"performance":{"status":"ready"}}]}'
        path.write_text(content, encoding="utf-8")
        profiles.save(self.project, "a", {"acting": {"goal": "全剧目标"}},
                      expected_revision=profiles.state(self.project)["revision"])
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_saved_acting_is_available_to_existing_actor_pipeline(self):
        import actor_pipeline
        profiles.save(self.project, "a", {"acting": {"personality": "遇强则强", "expression_rules": "先停顿再说话"}},
                      expected_revision=profiles.state(self.project)["revision"])
        # 分镜别名通过人物名称对应项目档案，仍可读取同一份演绎设定。
        board = {"actors": {"hero": {"name": "甲"}}, "shots": []}
        card = actor_pipeline.actor_cards_from_assets(self.project, board)["hero"]
        self.assertEqual(card["personality"], "遇强则强")
        self.assertEqual(card["expression_rules"], "先停顿再说话")


if __name__ == "__main__":
    unittest.main()
