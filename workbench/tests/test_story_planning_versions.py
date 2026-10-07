# -*- coding: utf-8 -*-
"""重新规划的候选隔离、完整版本与采用/恢复契约。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import story_planning_versions as planning
import story_units
import project_store


class PlanningVersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write('剧本/分集.json', {'units_version': 1, 'mode': 'generated', 'rev': 5,
            'episodes': [{'id': 'E1', 'title': '旧集', 'text': '旧正文'}]})
        self.write('剧本/大纲.json', {'units_version': 1, 'anchor_rev': 2, 'premise': '旧主线',
            'arcs': [{'id': 'OLD', 'ep_from': 'E1', 'ep_to': 'E1'}]})
        self.write('剧本/埋线.json', {'foreshadows': [{'id': 'OLD'}], 'hooks': []})
        self.write('剧本/brief.json', {'total_episodes': 3})
        self.write('剧本/style.json', {'script': 'three-act'})
        self.write('素材/人物.json', {'characters': [{'id': 'hero', 'name': '甲',
            'bio_arc': '旧弧光', 'bio_language': '人工短句', 'locked_fields': ['bio_language'],
            'voice_binding': {'voice_id': 'voice-1'}, 'image_path': '素材/甲.png'}]})
        self.write('素材/场景.json', {'scenes': []})
        self.write('素材/道具.json', {'props': []})
        for name, text in [('构想.txt', '甲寻找真相的完整故事'), ('剧本.txt', '旧全剧正文'), ('分集剧本_E1.txt', '旧正文')]:
            (self.root / '剧本' / name).write_text(text, encoding='utf-8')

    def write(self, relative, doc, root=None):
        path = (root or self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')

    def generate_revision(self, project, **kwargs):
        return planning.generate(project, revision_mode='rewrite', instructions='在旧故事基础上调整分集', **kwargs)

    def legacy_history(self):
        # 模拟停用前已经存在的本地存档，不能通过当前接口新建或恢复。
        ident = 'history-' + 'a' * 16
        directory = self.root / '剧本/.versions/规划' / ident
        for name, content in planning._collect(self.root).items():
            path = directory / '内容' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.write('version.json', {'id': ident, 'status': 'history', 'label': '旧版存档',
                                   'created_at': '2026-09-26T12:00:00'}, directory)
        return ident

    def adopt_revision(self, **kwargs):
        revision = planning.current_revision(self.root)
        result = self.generate_revision(self.root, **kwargs)
        self.assertTrue(result['ok'])
        planning.adopt(self.root, result['version_id'], revision)
        return result

    def runner(self, workspace, target):
        self.assertFalse(story_units.is_anchored(workspace))
        self.assertEqual(story_units.load_units(workspace)['episodes'], [])
        row = story_units.load_units(workspace)['characters'][0]
        self.assertEqual(row['bio_arc'], '旧弧光')
        self.assertEqual(row['bio_language'], '人工短句')
        self.assertEqual(row['voice_binding']['voice_id'], 'voice-1')
        self.write('剧本/分集.json', {'units_version': 1, 'mode': 'generated', 'episodes': [
            {'id': f'E{i}', 'summary': f'新剧情{i}', 'arc_id': 'NEW', 'beats': ['寻找'],
             'cast_refs': ['@character:hero']} for i in range(1, target + 1)]}, root=workspace)
        self.write('剧本/大纲.json', {'units_version': 1, 'premise': '新主线',
            'arcs': [{'id': 'NEW', 'ep_from': 'E1', 'ep_to': f'E{target}'}]}, root=workspace)
        self.write('剧本/埋线.json', {'foreshadows': [], 'hooks': []}, root=workspace)
        story_units.reset_referenced_settings(workspace)
        story_units.apply_entities(workspace, {'characters': [{'ref': '@character:hero',
            **{key: '完整人物设定' for key in story_units.CHAR_TEXT_KEYS}, 'bio_arc': '新弧光',
            'appearance': dict(face='宽额', hair='短发', body_type='瘦高', outfit='布衣')}]})
        return {'ok': True, 'incomplete': []}

    def test_revision_isolated_until_explicit_adoption_without_planning_history(self):
        # N90 语义：generate 只产候选不落当前；显式 adopt 后当前规划变为候选内容
        before = planning.current_revision(self.root)
        result = self.generate_revision(self.root, target_episodes=3, runner=self.runner)
        self.assertTrue(result['ok'])
        self.assertEqual(planning.current_revision(self.root), before)
        self.assertFalse((self.root / '剧本/.versions/规划').exists())
        self.assertEqual(planning.list_versions(self.root), [])
        candidate = planning.current_candidate(self.root)
        self.assertEqual(candidate['candidate']['id'], result['version_id'])
        self.assertTrue(candidate['candidate']['can_adopt'])
        preview = planning.preview_candidate(self.root, result['version_id'])
        self.assertTrue(any(group['label'] == '剧本/分集.json' for group in preview['groups']))
        adopted = planning.adopt(self.root, result['version_id'], before)
        self.assertTrue(adopted['changed'])
        self.assertNotEqual(planning.current_revision(self.root), before)
        self.assertEqual(len(story_units.load_units(self.root)['episodes']), 3)
        self.assertEqual(story_units.load_units(self.root)['outline']['premise'], '新主线')
        self.assertEqual(planning._meta(self.root, result['version_id'])['status'], 'applied')

    def test_changed_planning_retains_body_and_old_body_can_be_viewed(self):
        import episode_editor
        result = self.adopt_revision(target_episodes=3, runner=self.runner)
        self.assertTrue(result['ok'])
        row = story_units.load_units(self.root)['episodes'][0]
        self.assertEqual(row['text'], '旧正文')
        self.assertTrue(row['planning_review_required'])
        self.assertEqual((self.root / '剧本/分集剧本_E1.txt').read_text(encoding='utf-8'), '旧正文')
        history = episode_editor.episode_history(self.root, 'E1')
        self.assertIn('旧正文', [r['text'] for r in history])
        self.assertEqual(story_units.load_units(self.root)['characters'][0]['voice_binding']['voice_id'], 'voice-1')

    def test_planning_adopt_and_restore_are_disabled_without_mutation(self):
        history_id = self.legacy_history()
        before = planning.current_revision(self.root)
        for fn in (planning.adopt, planning.restore):
            with self.assertRaises(ValueError):
                fn(self.root, history_id, before)
        self.assertEqual(planning.current_revision(self.root), before)

    def test_failed_generation_preserves_current_and_same_request_resumes_progress(self):
        before = planning.current_revision(self.root)
        result = self.generate_revision(self.root, target_episodes=3, runner=lambda *_: {'ok': False, 'incomplete': ['模型失败']})
        self.assertFalse(result['ok'])
        self.assertEqual(planning.current_revision(self.root), before)
        resumed = self.generate_revision(self.root, target_episodes=3, runner=self.runner)
        self.assertTrue(resumed['ok'])
        self.assertEqual(result['version_id'], resumed['version_id'])

    def test_interrupted_bound_job_becomes_resumable_without_resending_generation(self):
        def interrupted(*args):
            raise KeyboardInterrupt()
        with patch.dict(os.environ, {'SLATE_JOB_ID': '42', 'SLATE_JOB_ATTEMPT_ID': '42-a1'}), \
                self.assertRaises(KeyboardInterrupt):
            self.generate_revision(self.root, target_episodes=3, runner=interrupted)
        meta = planning._current_meta(self.root)
        self.assertEqual(meta['job_id'], 42)
        self.assertEqual(meta['job_attempt_id'], '42-a1')
        self.assertGreater(meta['runner_pid'], 0)
        active = planning.current_candidate(self.root,
            job_lookup=lambda jid: {'status': 'failed', 'process_alive': True})
        self.assertEqual(active['candidate']['status'], 'generating')
        with patch.object(planning, '_runner_alive', return_value=False):
            stopped = planning.current_candidate(self.root, job_lookup=lambda jid: None)
        self.assertEqual(stopped['candidate']['status'], 'failed')
        self.assertTrue(stopped['candidate']['can_resume'])
        self.assertIn('中断', stopped['candidate']['error'])

    def test_finished_job_with_live_or_unknown_runner_cannot_resume(self):
        def interrupted(*args):
            raise KeyboardInterrupt()
        with patch.dict(os.environ, {'SLATE_JOB_ID': '42', 'SLATE_JOB_ATTEMPT_ID': '42-a1'}), \
                self.assertRaises(KeyboardInterrupt):
            self.generate_revision(self.root, target_episodes=3, runner=interrupted)
        for alive in (True, None):
            with self.subTest(alive=alive), patch.object(planning, '_runner_alive', return_value=alive):
                current = planning.current_candidate(self.root,
                    job_lookup=lambda jid: {'status': 'interrupted', 'process_alive': False})
            self.assertEqual(current['candidate']['status'], 'generating')
            self.assertFalse(current['candidate']['can_resume'])
            self.assertIn('核查', current['candidate']['error'])

    def test_unbound_cli_candidate_is_not_declared_interrupted_by_job_lookup(self):
        def interrupted(*args):
            raise KeyboardInterrupt()
        with patch.dict(os.environ, {'SLATE_JOB_ID': '', 'SLATE_JOB_ATTEMPT_ID': ''}), \
                self.assertRaises(KeyboardInterrupt):
            self.generate_revision(self.root, target_episodes=3, runner=interrupted)
        current = planning.current_candidate(self.root, job_lookup=lambda jid: None)
        self.assertEqual(current['candidate']['status'], 'generating')

    def test_extend_keeps_body_when_refs_only_change_spelling_or_order(self):
        # 引用的连字符、下划线和重复顺序不能被误判为剧情框架变化。
        prior = {'id': 'E1', 'title': '旧集', 'summary': '寻找真相', 'beats': ['寻找'],
                 'cast_refs': ['@character:hero-one', '@character:friend'], 'text': '已审核正文'}
        candidate = {**prior, 'cast_refs': ['@character:friend', '@character:hero_one',
                                         '@character:hero-one']}
        candidate.pop('text')
        workspace = self.root / '独立候选'
        self.write('剧本/分集.json', {'episodes': [candidate]}, workspace)
        self.write('剧本/大纲.json', {'arcs': [{'id': 'A1', 'ep_from': 'E1', 'ep_to': 'E1'}]}, workspace)

        changes = planning._finish_revision(workspace, {'mode': 'extend', 'episodes': [prior]})

        self.assertEqual(changes, {'preserved': ['E1'], 'changed': [], 'added': [], 'removed': []})
        self.assertEqual(story_units.load_units(workspace)['episodes'][0]['text'], '已审核正文')

    def test_wrong_episode_ids_do_not_publish(self):
        def wrong(workspace, target):
            self.runner(workspace, target)
            book = story_units.load_units(workspace)['episodes_doc']
            book['episodes'][-1]['id'] = 'E9'
            self.write('剧本/分集.json', book, workspace)
            return {'ok': True}
        before = planning.current_revision(self.root)
        result = self.generate_revision(self.root, target_episodes=3, runner=wrong)
        self.assertFalse(result['ok'])
        self.assertEqual(planning.current_revision(self.root), before)

    def test_current_edit_during_generation_is_not_overwritten(self):
        before = planning.current_revision(self.root)
        def concurrently_edited(workspace, target):
            result = self.runner(workspace, target)
            self.write('剧本/大纲.json', {'premise': '用户更新的主线'})
            return result
        result = self.generate_revision(self.root, target_episodes=3, runner=concurrently_edited)
        self.assertTrue(result['ok'])
        self.assertFalse(planning.current_candidate(self.root)['candidate']['can_adopt'])
        with self.assertRaises(project_store.RevisionConflict):
            planning.adopt(self.root, result['version_id'], before)
        self.assertEqual(story_units.load_units(self.root)['outline']['premise'], '用户更新的主线')
        self.assertEqual(len(story_units.load_units(self.root)['episodes']), 1)

    def test_write_failure_rolls_back_current_planning(self):
        import asset_repository
        before = planning.current_revision(self.root)
        original = asset_repository._write
        calls = 0
        def broken(path, content):
            nonlocal calls
            if Path(path).parent in (self.root / '剧本', self.root / '素材'):
                calls += 1
                if calls == 2:
                    raise OSError('模拟磁盘写入失败')
            return original(path, content)
        result = self.generate_revision(self.root, target_episodes=3, runner=self.runner)
        self.assertTrue(result['ok'])
        with patch.object(asset_repository, '_write', side_effect=broken), self.assertRaises(OSError):
            planning.adopt(self.root, result['version_id'], before)
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(planning.current_revision(self.root), before)

    def test_metadata_failure_rolls_back_current_planning(self):
        import asset_repository
        before = planning.current_revision(self.root)
        write = asset_repository._write
        def broken(path, content):
            if Path(path).name == 'version.json' and json.loads(content).get('status') == 'applied':
                raise OSError('模拟进度记录写入失败')
            return write(path, content)
        result = self.generate_revision(self.root, target_episodes=3, runner=self.runner)
        self.assertTrue(result['ok'])
        with patch.object(asset_repository, '_write', side_effect=broken), self.assertRaises(OSError):
            planning.adopt(self.root, result['version_id'], before)
        self.assertEqual(planning.current_revision(self.root), before)

    def test_legacy_body_history_is_read_only_and_empty_plans_are_excluded(self):
        import episode_editor
        history_id = self.legacy_history()
        book = story_units.load_units(self.root)['episodes_doc']
        book['episodes'][0]['text'] = '已沉淀的新正文'
        self.write('剧本/分集.json', book)
        before = planning.current_revision(self.root)
        history = episode_editor.episode_history(self.root, 'E1')
        self.assertEqual({r['text'] for r in history}, {'旧正文', '已沉淀的新正文'})
        self.assertTrue(history[0]['current'])
        self.assertEqual(planning.current_revision(self.root), before)
        self.assertTrue(planning.version_project(self.root, history_id).is_dir())
        self.assertIn('已沉淀的新正文', episode_editor.script_history(self.root)[0]['text'])

    def test_imported_script_without_episode_plan_has_current_and_history(self):
        import episode_editor
        import versions
        (self.root / '剧本/分集.json').unlink()
        versions.snapshot(str(self.root / '剧本/剧本.txt'))
        (self.root / '剧本/剧本.txt').write_text('新导入正文', encoding='utf-8')
        history = episode_editor.script_history(self.root)
        self.assertTrue(history[0]['current'])
        self.assertEqual(history[0]['text'], '新导入正文')
        self.assertIn('旧全剧正文', [r['text'] for r in history])

    def test_confirmed_body_clears_planning_review_and_saves_prior_text(self):
        import episode_editor
        self.adopt_revision(target_episodes=3, runner=self.runner)
        revision = project_store.current_revision(self.root / '剧本/分集.json')
        episode_editor.save_episode(self.root, 'E1', '核对后的新正文', revision)
        row = story_units.load_units(self.root)['episodes'][0]
        self.assertNotIn('planning_review_required', row)
        self.assertEqual({v['text'] for v in episode_editor.episode_history(self.root, 'E1')},
                         {'旧正文', '核对后的新正文'})


if __name__ == '__main__':
    unittest.main()
