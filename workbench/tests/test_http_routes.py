# -*- coding: utf-8 -*-
"""真实 HTTP 边界：响应次数、文件流、静态兜底和上传体不被分发器消费。"""
import json
import http.client
import socket
import tempfile
import threading
import os as _os, unittest
_os.environ.setdefault('SLATE_NO_AUTH', '1')   # HTTP 边界测试不做口令认证
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch, MagicMock
from http.server import ThreadingHTTPServer
import workbench.server as server


class HttpRouteTests(unittest.TestCase):
    def test_runninghub_catalog_and_task_query_keep_secret_on_server(self):
        import os
        os.environ['SLATE_RUNNINGHUB_KEY_TYPE'] = 'SHARED'   # 固定企业 key 语境，避免测试依赖真网探测
        try:
            code, _, payload = self.request('/api/runninghub/catalog')
        finally:
            os.environ.pop('SLATE_RUNNINGHUB_KEY_TYPE', None)
        self.assertEqual(code, 200, payload)
        data = json.loads(payload)
        self.assertGreater(len(data['apis']), 300)
        self.assertIn('seedream-v5-pro/image-to-image', [r['id'] for r in data['models']['image_edit']])
        cfg = dict(id='runninghub', enabled=True, api_key='saved-secret', base_url='https://www.runninghub.ai', models={})
        with patch.object(server, 'load_vendors', return_value=[cfg]):
            from runninghub_client import RunningHubClient
            with patch.object(RunningHubClient, 'query', return_value={'taskId':'t', 'status':'RUNNING'}) as query:
                code, _, payload = self.request('/api/runninghub/query', 'POST', b'{"task_id":"t"}')
                self.assertEqual(code, 200, payload)
                self.assertNotIn(b'saved-secret', payload)
                query.assert_called_once()
            with patch.object(RunningHubClient, 'call', return_value={'taskId':'t', 'status':'RUNNING'}) as call:
                code, _, payload = self.request('/api/runninghub/submit', 'POST', b'{"endpoint":"minimax/hailuo-h3/text-to-video", "payload":{"prompt":"hello"}}')
                self.assertEqual(code, 200, payload)
                self.assertNotIn(b'saved-secret', payload)
                self.assertEqual(call.call_count, 1)
        code, _, _ = self.request('/api/runninghub/query', 'POST', b'{"task_id":"../escape"}')
        self.assertEqual(code, 400)

    def test_runninghub_query_downloads_all_outputs_and_serves_only_result_files(self):
        from runninghub_client import RunningHubClient
        cfg=dict(id='runninghub', enabled=False, api_key='secret', base_url='https://www.runninghub.ai', models={})
        result={'taskId':'t','status':'SUCCESS','results':[{'url':'https://example.test/result.glb','outputType':'glb'}]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(server,'ROOT',str(Path(tmp)/'workbench')), patch.object(server,'VIDEO',tmp), patch.object(server,'load_vendors',return_value=[cfg]):
            def save(data, out_path=None, **kwargs):
                Path(out_path).parent.mkdir(parents=True,exist_ok=True)
                Path(out_path).write_bytes(b'model-file')
            with patch.object(RunningHubClient,'query',return_value=result), patch.object(RunningHubClient,'output',side_effect=save):
                code,_,payload=self.request('/api/runninghub/query','POST',b'{"task_id":"t","download":true}')
            self.assertEqual(code,200,payload)
            file=json.loads(payload)['files'][0]
            self.assertEqual(self.request(file['url'])[2],b'model-file')
            self.assertEqual(self.request('/api/runninghub/file?task_id=t&name=../../providers.json')[0],400)

    def test_asset_completion_passes_exact_selection_to_job(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'proj_dir',return_value=folder):
            path=Path(folder)/'素材/道具.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'props':[{'id':'p'},{'id':'q'}]}),encoding='utf-8')
            with patch.object(server.H,'spawn_job',return_value=987) as spawn:
                code,_,payload=self.request('/api/units/build','POST',json.dumps({'project':'test','stage':'entity','asset_refs':['@prop:p']}).encode())
                self.assertEqual(code,200,payload)
                args=spawn.call_args.args[1]
                self.assertEqual(json.loads(args[args.index('--asset-refs')+1]),['@prop:p'])
                spawn.reset_mock()
                for refs in ([],['@prop:missing']):
                    self.assertEqual(self.request('/api/units/build','POST',json.dumps({'project':'test','stage':'entity','asset_refs':refs}).encode())[0],400)
                spawn.assert_not_called()

    def test_batch_episode_edit_has_revision_and_partial_scope(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'proj_dir',return_value=folder):
            path=Path(folder)/'剧本/分集.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'episodes':[{'id':'E1','summary':'旧','text':'已采用正文'},{'id':'E2','summary':'不变'}]}),encoding='utf-8')
            planning=server.tools_mod('story_planning_versions.py')
            body=json.dumps({'project':'test','kind':'episodes','revision':planning.current_revision(folder),'items':[{'id':'E1','fields':{'summary':'新'}}]}).encode()
            code,_,payload=self.request('/api/units/edit','POST',body)
            self.assertEqual(code,200,payload)
            self.assertEqual(json.loads(payload)['applied'],['E1'])
            stored=json.loads(path.read_text('utf-8'))
            self.assertEqual(stored['episodes'][1]['summary'],'不变')
            self.assertEqual(self.request('/api/units/edit','POST',body)[0],409)

    def test_visual_review_missing_assets_and_batch_save(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            path = Path(folder)/'素材/道具.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'props':[{'id':'p','name':'铜牌'}]}),encoding='utf-8')
            code, _, payload = self.request('/api/assets/visual-review?project=test')
            self.assertEqual(code,200,payload)
            result=json.loads(payload)
            self.assertEqual(result['other_assets'][0]['id'],'@prop:p')
            body=json.dumps({'project':'test','revision':result['revision'],'items':[{'id':'@prop:p','patch':{'visual_description':'方铜牌'}}]}).encode()
            code,_,payload=self.request('/api/assets/visual-review','POST',body)
            self.assertEqual(code,200,payload)
            self.assertTrue(json.loads(payload)['validations']['@prop:p']['ready'])
            self.assertEqual(self.request('/api/assets/visual-review','POST',body)[0],409)

    def test_style_options_does_not_load_full_script_or_production_checks(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server, 'tools_mod', side_effect=AssertionError('风格选择不能运行制作检查')):
            path = Path(folder)/'剧本/style.json'
            path.parent.mkdir()
            path.write_text('{"image":"auto"}', encoding='utf-8')
            code, _, payload = self.request('/api/skills/style?project=test')
            self.assertEqual(code, 200, payload)
            value = json.loads(payload)
            self.assertEqual(value['style']['image'], 'auto')
            self.assertTrue(value['skills'])
            self.assertNotIn('script', value)

    def test_asset_reconcile_previews_then_saves_reviewed_selection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            base = Path(folder)
            for name, data in {'剧本/分集.json': {'episodes':[{'id':'E1','key_asset_refs':['@prop:p']}]},
                               '素材/道具.json': {'props':[{'id':'p','name':'令旗','image_prompt':'蓝布竹杆令旗'}]}}.items():
                path = base/name
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(data), encoding='utf-8')
            code, _, payload = self.request('/api/assets/reconcile?project=test')
            self.assertEqual(code, 200, payload)
            result = json.loads(payload)
            self.assertNotIn('_changes', result)
            self.assertEqual(result['items'][0]['id'], '@prop:p')
            body = json.dumps({'project':'test','revision':result['revision'],'selected':['@prop:p']}).encode()
            code, _, payload = self.request('/api/assets/reconcile', 'POST', body)
            self.assertEqual(code, 200, payload)
            self.assertEqual(json.loads((base/'素材/道具.json').read_text('utf-8'))['props'][0]['visual_description'], '蓝布竹杆令旗')
            self.assertEqual(self.request('/api/assets/reconcile', 'POST', body)[0], 409)

    def test_character_batch_review_submits_selected_changes_atomically(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            path=Path(folder) / '素材/人物.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'characters':[{'id':'a'},{'id':'b'}]}),encoding='utf-8')
            _,_,payload=self.request('/api/characters?project=test')
            revision=json.loads(payload)['revision']
            body=json.dumps({'project':'test','expected_revision':revision,'items':[
                {'character_id':'a','patch':{'appearance':{'face':'窄脸'}}},
                {'character_id':'b','patch':{'appearance':{'hair':'短卷发'}}}]}).encode()
            code,_,payload=self.request('/api/characters/save','POST',body)
            self.assertEqual(code,200,payload)
            self.assertEqual(json.loads(payload)['saved'],['a','b'])
            code,_,_=self.request('/api/characters/save','POST',body)
            self.assertEqual(code,409)

    def test_storyboard_diff_reads_content_and_revision_from_same_document(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            path=Path(folder) / '分镜/剧本_E1.json'
            path.parent.mkdir()
            path.write_text('{"shots":[{"id":"S1","prompt":"现行提示词"}]}',encoding='utf-8')
            code,_,payload=self.request('/api/storyboard/revision?project=test&name='+quote('剧本_E1.json')+'&include=board')
            self.assertEqual(code,200,payload)
            result=json.loads(payload)
            self.assertEqual(result['board']['shots'][0]['prompt'],'现行提示词')
            self.assertEqual(result['revision'],server.project_store.current_revision(path))

    def test_storyboard_review_returns_saved_content_and_preserves_unselected_shot(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            path=Path(folder) / '分镜/board.json'
            path.parent.mkdir()
            shots=[{'id':'S1','dur':4,'action':'原动作一'},{'id':'S2','dur':4,'action':'原动作二'}]
            path.write_text(json.dumps({'shots':shots}),encoding='utf-8')
            shots[1]['action']='选中并确认的动作二'
            body=json.dumps({'project':'test','name':'board.json','shots':shots,'revision':server.project_store.current_revision(path)}).encode()
            code,_,payload=self.request('/api/storyboard/save','POST',body)
            self.assertEqual(code,200,payload)
            result=json.loads(payload)
            self.assertEqual(result['board']['shots'][0]['action'],'原动作一')
            self.assertEqual(result['board']['shots'][1]['action'],'选中并确认的动作二')
            self.assertEqual(result['board'],json.loads(path.read_text(encoding='utf-8')))

    def test_visual_asset_preview_reads_current_character_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            base = Path(folder) / '素材'
            base.mkdir()
            (base / '人物.json').write_text(json.dumps({'characters': [{'id': 'a',
                'sheet_prompt': '旧红衣', 'appearance': {'outfit': '新灰衣', 'hair': '短发'}}]}), encoding='utf-8')
            code, _, payload = self.request('/api/asset/prompt_layers?project=test&kind=character&id=a')
            self.assertEqual(code, 200, payload)
            layers = json.loads(payload)['layers']
            self.assertIn('新灰衣', layers['subject'])
            self.assertIn('新灰衣', layers['final'])
            self.assertNotIn('旧红衣', layers['final'])

    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def test_existing_imported_episode_can_expand_without_idea(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server.H, 'spawn_job', return_value=8) as spawn:
            base = Path(folder) / '剧本'
            base.mkdir()
            (base / '分集.json').write_text(json.dumps({'episodes': [{'id': 'E1', 'text': '导入的正文'}]}), encoding='utf-8')
            body = json.dumps({'project': 'test', 'episode': 'E1'}).encode()
            self.assertEqual(self.request('/api/script/expand', 'POST', body)[0], 200)
            self.assertIn('--episode', spawn.call_args.args[1])
            self.assertNotIn('--idea', spawn.call_args.args[1])

    def test_missing_episode_rejected_before_job_is_created(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server.H, 'spawn_job') as spawn:
            self.assertEqual(self.request('/api/script/expand', 'POST', json.dumps({'project': 'test', 'episode': 'E9'}).encode())[0], 400)
            spawn.assert_not_called()

    def test_invalid_episode_book_rejected_before_job_is_created(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server.H, 'spawn_job') as spawn:
            base = Path(folder) / '剧本'
            base.mkdir()
            for book in ([], {'episodes': 'E1'}, {'episodes': [None]}):
                with self.subTest(book=book):
                    (base / '分集.json').write_text(json.dumps(book), encoding='utf-8')
                    self.assertEqual(self.request('/api/script/expand', 'POST', b'{"project":"test","episode":"E1"}')[0], 400)
            spawn.assert_not_called()

    def test_locked_actor_performance_keeps_stale_and_invalid_status(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            base = Path(folder) / '分镜'
            base.mkdir()
            (base / 'board.json').write_text('{}', encoding='utf-8')
            actor = MagicMock()
            actor.list_candidates.return_value = []
            actor.hydrate_actor_cards.return_value = {}
            actor.main_actor_records.return_value = {}
            actor.actor_ids_for_shot.return_value = []
            actor.continuity_evidence.return_value = None
            compiler = MagicMock()
            with patch.object(server, 'tools_mod', side_effect=lambda name: actor if name == 'actor_pipeline.py' else compiler):
                for performance, warning, expected in [('ready', '', 'locked'), ('ready', '表演来源过期', 'stale'), ('invalid', '', 'invalid')]:
                    with self.subTest(expected=expected):
                        actor._read_board.return_value = ({'shots': [{'id': 'S1', 'performance_locked': True, 'performance': {'status': performance}}]}, 1)
                        compiler.compile_shot.return_value = {'warnings': [warning] if warning else []}
                        code, _, body = self.request('/api/acting/context?project=test&storyboard=board.json')
                        self.assertEqual(code, 200, body)
                        shot = json.loads(body)['shots'][0]
                        self.assertTrue(shot['performance_locked'])
                        self.assertEqual(shot['performance_status'], expected)

    def test_units_job_receives_idea_without_a_second_outline_job(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server.H, 'spawn_job', return_value=8) as spawn:
            body = json.dumps({'project': 'test', 'idea': '一个完整的故事构想', 'eps': 2}).encode()
            self.assertEqual(self.request('/api/units/build', 'POST', body)[0], 200)
            command = spawn.call_args.args[1]
            self.assertIn('units', command)
            self.assertIn('一个完整的故事构想', command)
            self.assertEqual(spawn.call_count, 1)

    def test_completion_conflict_rejected_without_creating_job(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server.H, 'spawn_job') as spawn:
            base = Path(folder) / '剧本'
            base.mkdir()
            (base / '构想.txt').write_text('一个完整的故事构想', encoding='utf-8')
            (base / '分集.json').write_text(json.dumps({'units_version': 1, 'episodes': [{'id': 'E1'}]}), encoding='utf-8')
            (base / 'brief.json').write_text('{"total_episodes":15}', encoding='utf-8')
            for endpoint in ('/api/units/build', '/api/script/expand'):
                with self.subTest(endpoint=endpoint):
                    code, _, payload = self.request(endpoint, 'POST', b'{"project":"test","stage":"complete"}')
                    self.assertEqual(code, 409, payload)
                    response = json.loads(payload)
                    self.assertEqual(response['episode_count'], 1)
                    self.assertEqual(response['target_episodes'], 15)
                    self.assertEqual(response['errors'][0]['code'], 'SPEC_EP_COUNT')
            spawn.assert_not_called()

    def test_replan_uses_async_candidate_job_even_when_current_planning_is_locked(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server.H, 'spawn_job', return_value=8) as spawn:
            base = Path(folder) / '剧本'
            base.mkdir()
            (base / '分集.json').write_text('{"units_version":1,"episodes":[{"id":"E1"}]}', encoding='utf-8')
            (base / '大纲.json').write_text('{"anchor_rev":2,"premise":"旧主线"}', encoding='utf-8')
            body = json.dumps({'project': 'test', 'stage': 'replan', 'eps': 15, 'idea': '重新规划的完整故事', 'planning_source': 'idea'}).encode()
            code, _, payload = self.request('/api/units/build', 'POST', body)
            self.assertEqual(code, 200, payload)
            command = spawn.call_args.args[1]
            self.assertIn('replan', command)
            self.assertIn('--planning-source', command)
            self.assertEqual(command[command.index('--eps') + 1], '15')
            self.assertEqual(spawn.call_count, 1)

    def test_story_revision_forwards_mode_and_instructions_and_rejects_undefined_rewrite(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), \
             patch.object(server.H, 'spawn_job', return_value=8) as spawn:
            base = Path(folder) / '剧本'
            base.mkdir()
            (base / '分集.json').write_text('{"episodes":[{"id":"E1"}]}', encoding='utf-8')
            request = {'project': 'test', 'stage': 'replan', 'eps': 1, 'revision_mode': 'rewrite'}
            code, _, payload = self.request('/api/units/build', 'POST', json.dumps(request).encode())
            self.assertEqual(code, 400, payload)
            spawn.assert_not_called()
            request['revision_instructions'] = '保留主角，把第一集节奏压紧'
            code, _, payload = self.request('/api/units/build', 'POST', json.dumps(request).encode())
            self.assertEqual(code, 200, payload)
            command = spawn.call_args.args[1]
            self.assertEqual(command[command.index('--revision-mode') + 1], 'rewrite')
            self.assertEqual(command[command.index('--revision-instructions') + 1], request['revision_instructions'])

    def test_resume_failed_planning_uses_original_candidate_and_rejects_stale_baseline(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), \
             patch.object(server.H, 'spawn_job', return_value=8) as spawn:
            base = Path(folder) / '剧本'
            base.mkdir()
            (base / '分集.json').write_text('{"mode":"generated","episodes":[{"id":"E1","text":"旧正文"}]}', encoding='utf-8')
            (base / '构想.txt').write_text('已有角色寻找证据', encoding='utf-8')
            planning = server.tools_mod('story_planning_versions.py')
            failed = planning.generate(folder, target_episodes=3,
                runner=lambda *_: {'ok': False, 'incomplete': ['模型失败']})
            request = {'project': 'test', 'stage': 'replan', 'planning_version': failed['version_id']}
            code, _, payload = self.request('/api/units/build', 'POST', json.dumps(request).encode())
            self.assertEqual(code, 200, payload)
            command = spawn.call_args.args[1]
            self.assertEqual(command[command.index('--planning-version') + 1], failed['version_id'])
            self.assertNotIn('--idea', command)
            spawn.reset_mock()
            (base / '构想.txt').write_text('当前构想已经人工修改', encoding='utf-8')
            code, _, payload = self.request('/api/units/build', 'POST', json.dumps(request).encode())
            self.assertEqual(code, 409, payload)
            spawn.assert_not_called()

    def test_working_candidate_requires_adoption_and_legacy_restore_is_disabled(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            base = Path(folder) / '剧本'
            base.mkdir()
            (base / '分集.json').write_text('{"mode":"generated","episodes":[{"id":"E1","text":"旧正文"}]}', encoding='utf-8')
            (base / '构想.txt').write_text('一个完整的故事构想', encoding='utf-8')
            planning = server.tools_mod('story_planning_versions.py')
            history_id = 'history-' + 'a' * 16
            history_dir = base / '.versions/规划' / history_id
            for name, content in planning._collect(folder).items():
                target = history_dir / '内容' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            (history_dir / 'version.json').write_text(json.dumps({'id': history_id, 'status': 'history',
                'label': '旧存档', 'created_at': '2026-09-26T12:00:00'}), encoding='utf-8')
            def runner(workspace, target):
                (workspace / '剧本/分集.json').write_text(json.dumps({'units_version': 1, 'episodes': [
                    {'id': f'E{i}', 'arc_id': 'A', 'summary': '新剧情', 'beats': ['寻找']} for i in range(1, target + 1)]}), encoding='utf-8')
                (workspace / '剧本/大纲.json').write_text(json.dumps({'arcs': [{'id': 'A', 'ep_from': 'E1', 'ep_to': f'E{target}'}]}), encoding='utf-8')
                return {'ok': True}
            result = planning.generate(folder, target_episodes=2, revision_mode='rewrite',
                instructions='在旧故事基础上调整分集', runner=runner)
            self.assertTrue(result['ok'])
            code, _, payload = self.request('/api/units?project=test')
            self.assertEqual(code, 200, payload)
            current = json.loads(payload)
            # 候选就绪即停：生成不自动发布——正式项目仍是旧 1 集，候选待人工采用
            self.assertEqual(len(current['episodes']), 1, '生成只进候选区，采用前不得改写当前规划')
            self.assertEqual(current['planning_versions'], [])
            code, _, payload = self.request('/api/units/candidate?project=test')
            self.assertEqual(code, 200, payload)
            candidate = json.loads(payload)
            self.assertEqual(candidate['candidate']['id'], result['version_id'])
            self.assertTrue(candidate['candidate']['can_adopt'])
            before = planning.current_revision(folder)
            for action in ('adopt-plan', 'restore-plan'):
                body = json.dumps({'project':'test','kind':action,'id':history_id,'revision':before}).encode()
                self.assertEqual(self.request('/api/units/edit', 'POST', body)[0], 400)
                self.assertEqual(planning.current_revision(folder), before)
            # 采用新候选：经 /api/units/edit(kind=adopt-plan) 人工落盘
            body = json.dumps({'project':'test','kind':'adopt-plan','id':result['version_id'],
                               'revision':before}).encode()
            code, _, payload = self.request('/api/units/edit', 'POST', body)
            self.assertEqual(code, 200, payload)
            code, _, payload = self.request('/api/units?project=test')
            current = json.loads(payload)
            self.assertEqual(len(current['episodes']), 2, '采用后候选规划落回正式项目')
            code, _, payload = self.request('/api/script/episode/history?project=test')
            self.assertEqual(code, 200, payload)
            self.assertTrue(any('旧正文' in v['text'] for v in json.loads(payload)['versions']))

    def test_units_exposes_scene_and_prop_visual_descriptions_for_missing_field_editor(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            assets = Path(folder) / '素材'
            assets.mkdir()
            (assets / '场景.json').write_text(json.dumps({'scenes': [
                {'id': 'room', 'name': '房间', 'visual_description': '青石墙与朝南窗'}]}), encoding='utf-8')
            (assets / '道具.json').write_text(json.dumps({'props': [
                {'id': 'ring', 'name': '铜环', 'usage_boundary': '只能开旧锁',
                 'visual_description': '暗褐铜环，三道刻痕'}]}), encoding='utf-8')
            code, _, payload = self.request('/api/units?project=test')
            self.assertEqual(code, 200, payload)
            result = json.loads(payload)
            self.assertEqual(result['scene_limits'][0]['visual_description'], '青石墙与朝南窗')
            self.assertEqual(result['prop_boundaries'][0]['visual_description'], '暗褐铜环，三道刻痕')

    def test_episode_save_conflict_and_history_contract(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            base = Path(folder) / '剧本'
            base.mkdir()
            (base / '分集.json').write_text(json.dumps({'mode':'generated','episodes':[{'id':'E1','text':'甲：旧稿'}]}), encoding='utf-8')
            revision = server.project_store.current_revision(base / '分集.json')
            body = json.dumps({'project':'test','episode':'E1','text':'甲：新稿','revision':revision}).encode()
            code, _, payload = self.request('/api/script/episode/save','POST',body)
            self.assertEqual(code,200,payload)
            self.assertTrue(json.loads(payload)['changed'])
            self.assertEqual(self.request('/api/script/episode/save','POST',body)[0],409)
            code, _, payload = self.request('/api/script/episode/history?project=test&episode=E1')
            self.assertEqual(code,200,payload)
            self.assertIn('甲：旧稿',[r['text'] for r in json.loads(payload)['versions']])

    def test_character_workspace_roundtrip_and_revision_conflict(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            path = Path(folder) / '素材/人物.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'characters':[{'id':'hero','name':'主角','states':[{'id':'s1'}]}]}),encoding='utf-8')
            code, _, raw = self.request('/api/characters?project=test')
            self.assertEqual(code,200,raw)
            data=json.loads(raw)
            body=json.dumps({'project':'test','character_id':'hero','patch':{'biography':'人物小传'},'expected_revision':data['revision']}).encode()
            code, _, raw = self.request('/api/characters/save','POST',body)
            self.assertEqual(code,200,raw)
            self.assertEqual(json.loads(raw)['character']['states'],[{'id':'s1'}])
            self.assertEqual(self.request('/api/characters/save','POST',body)[0],409)

    def test_script_draft_job_and_adoption_contract(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server.H,'spawn_job',return_value=90001):
            path=Path(folder) / '剧本/分集.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'mode':'generated','episodes':[{'id':'E1','text':'原稿','summary':'概要'}]}),encoding='utf-8')
            (path.parent / '构想.txt').write_text('完整故事构想',encoding='utf-8')
            body=json.dumps({'project':'test','episodes':['E1']}).encode()
            code,_,raw=self.request('/api/script/drafts/generate','POST',body)
            self.assertEqual(code,200,raw)
            created=json.loads(raw)
            self.assertEqual(created['id'],90001)
            self.assertEqual(self.request('/api/script/drafts/generate','POST',body)[0],400)
            drafts=server.tools_mod('script_drafts.py')
            drafts.run(folder,created['batch'],generate=lambda *args:'新稿')
            code,_,raw=self.request('/api/script/drafts?project=test')
            self.assertEqual(code,200,raw)
            self.assertEqual(json.loads(raw)['batches'][0]['items'][0]['after'],'新稿')
            code,_,raw=self.request('/api/script/drafts/adopt','POST',json.dumps({'project':'test','batch':created['batch'],'episodes':['E1']}).encode())
            self.assertEqual(code,200,raw)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['episodes'][0]['text'],'新稿')

    def test_failed_draft_repair_is_a_single_async_job_with_saved_original(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'proj_dir',return_value=folder), patch.object(server.H,'spawn_job',return_value=90003) as spawn:
            path=Path(folder)/'剧本/分集.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'mode':'generated','episodes':[{'id':'E1','text':'原稿','summary':'概要'}]}),encoding='utf-8')
            (path.parent/'构想.txt').write_text('完整故事构想',encoding='utf-8')
            drafts=server.tools_mod('script_drafts.py')
            batch=drafts.create(folder,['E1'])
            drafts.run(folder,batch['id'],generate=lambda *args:'已生成原稿')
            def failed(doc):
                doc['status']='partial'
                doc['items'][0].update(status='failed',failure_kind='validation',error='未登记说话人：陌生人')
            drafts._update(drafts._path(folder,batch['id']),failed)
            body=json.dumps({'project':'test','batch':batch['id'],'episodes':['E1'],'instructions':'按设定修正'}).encode()
            code,_,raw=self.request('/api/script/drafts/repair','POST',body)
            self.assertEqual(code,200,raw)
            self.assertTrue(json.loads(raw)['job'])
            self.assertIn('--repair',spawn.call_args.args[1])
            self.assertEqual(spawn.call_count,1)
            self.assertEqual(self.request('/api/script/drafts/repair','POST',body)[0],400)
            self.assertEqual(spawn.call_count,1)
            saved=json.loads(drafts._path(folder,batch['id']).read_text(encoding='utf-8'))
            self.assertEqual(saved['items'][0]['after'],'已生成原稿')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['episodes'][0]['text'],'原稿')

    def test_episode_read_revision_matches_the_returned_screenplay(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            base = Path(folder) / '剧本'
            base.mkdir()
            path = base / '分集.json'
            path.write_text(json.dumps({'mode': 'generated', 'episodes': [{'id': 'E1', 'text': '甲：读取时的旧稿'}]}), encoding='utf-8')
            original_revision = server.project_store.current_revision(path)
            def changed_during_response(*args):
                path.write_text(json.dumps({'mode': 'generated', 'episodes': [{'id': 'E1', 'text': '甲：并发保存的新稿'}]}), encoding='utf-8')
                return {}
            with patch.object(server, 'skill_lib_call', side_effect=changed_during_response):
                code, _, payload = self.request('/api/script/data?project=test')
            self.assertEqual(code, 200, payload)
            result = json.loads(payload)
            self.assertEqual(result['episodes'][0]['text'], '甲：读取时的旧稿')
            self.assertEqual(result['script_revision'], original_revision)
            body = json.dumps({'project': 'test', 'episode': 'E1', 'text': '甲：旧页面编辑稿', 'revision': result['script_revision']}).encode()
            self.assertEqual(self.request('/api/script/episode/save', 'POST', body)[0], 409)

    def request(self, path, method='GET', body=b'', headers=None):
        fields = {'Host': 'localhost', 'Connection': 'close', 'Content-Length': str(len(body)), **(headers or {})}
        request = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n'
        with socket.create_connection(self.http.server_address, timeout=4) as sock:
            sock.sendall(request.encode() + body)
            chunks = []
            while True:
                part = sock.recv(65536)
                if not part: break
                chunks.append(part)
        raw = b''.join(chunks)
        self.assertEqual(raw.count(b'HTTP/1.0 '), 1, raw[:500])
        head, payload = raw.split(b'\r\n\r\n', 1)
        return int(head.split()[1]), head, payload

    def test_environment_install_validation_and_busy(self):
        self.assertEqual(self.request('/api/env/install', 'POST', b'{"groups":["arbitrary"]}')[0], 400)
        with patch.object(server.H, 'JOBS', {1: {"status": "running"}}):
            self.assertEqual(self.request('/api/env/install', 'POST', b'{"groups":["base"]}')[0], 409)
        with patch.object(server.H, 'JOBS', {}), patch.object(server.H, 'spawn_job', return_value=77) as spawn:
            code, _, body = self.request('/api/env/install', 'POST', b'{"groups":["base"]}')
            self.assertEqual(code, 202)
            self.assertEqual(json.loads(body)['id'], 77)
            self.assertEqual(spawn.call_args.args[0], 'environment_install')

    def test_missing_frontend_reports_deployment_error(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'WEBDIST', folder):
            code, _, body = self.request('/')
            self.assertEqual(code, 503)
            self.assertIn(b'npm run build', body)
            (Path(folder) / 'index.html').write_text('new-ui', encoding='utf-8')
            self.assertEqual(self.request('/')[2], b'new-ui')

    def test_missing_media_sends_exactly_one_404(self):
        with patch.object(server, 'safe_video', return_value=None):
            self.assertEqual(self.request('/media?p=missing.png')[0], 404)

    def test_reference_upload_preserves_binary_and_lists_project_media(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            raw = b'RIFF\x00\xffaudio'
            code, _, payload = self.request('/api/reference-media/upload?project=test&kind=audio&name=test.wav', 'POST', raw)
            self.assertEqual(code, 200, payload)
            row = json.loads(payload)
            self.assertEqual((Path(folder)/row['path']).read_bytes(), raw)
            code, _, payload = self.request('/api/reference-media?project=test&kind=audio')
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(payload)['items'][0]['path'], row['path'])
            self.assertEqual(self.request('/api/reference-media/upload?project=test&kind=audio&name=test.exe','POST',raw)[0],400)

    def test_signed_media_range_and_invalid_signature(self):
        import media_gateway
        from urllib.parse import urlsplit
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'VIDEO',folder), patch.object(media_gateway,'CONFIG',Path(folder)/'gateway.json'):
            project = Path(folder)/'projects'/'demo'; project.mkdir(parents=True)
            (project/'clip.mp4').write_bytes(b'0123456789')
            media_gateway.save({'base_url':'https://example.com'})
            url = urlsplit(media_gateway.signed_url(project,'clip.mp4'))
            path = url.path + '?' + url.query
            code, headers, body = self.request(path,headers={'Range':'bytes=2-5'})
            self.assertEqual((code,body),(206,b'2345'))
            self.assertIn(b'Content-Range: bytes 2-5/10',headers)
            self.assertEqual(self.request(path,method='HEAD')[2],b'')
            self.assertEqual(self.request(path.replace('signature=','signature=x'))[0],403)

    def test_api_miss_never_returns_spa_or_static_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'index.html').write_text('SPA')
            (root/'api').mkdir(); (root/'api'/'missing').write_text('wrong file')
            with patch.object(server, 'WEBDIST', folder), patch.object(server, 'ROOT', folder):
                self.assertEqual(self.request('/api/missing', headers={'Accept': 'text/html'})[0], 404)
                self.assertEqual(self.request('/env', headers={'Accept': 'text/html'})[2], b'SPA')
                self.assertEqual(self.request('/missing', method='POST')[0], 404)

    def test_media_range_and_sensitive_file_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder)/'clip.mp4'; media.write_bytes(b'0123456789')
            with patch.object(server, 'safe_video', return_value=str(media)):
                code, headers, body = self.request('/media?p=clip.mp4', headers={'Range': 'bytes=2-5'})
                self.assertEqual((code, body), (206, b'2345'))
                self.assertIn(b'Content-Range: bytes 2-5/10', headers)
            secret = Path(folder)/'providers.json'; secret.write_text('{}')
            with patch.object(server, 'safe_video', return_value=str(secret)):
                self.assertEqual(self.request('/media?p=providers.json')[0], 403)

    def test_session_credentials_never_leave_http(self):
        """auth.json 持会话签名 secret 与口令 scrypt 哈希，三个文件出口一律 403。"""
        with tempfile.TemporaryDirectory() as folder:
            cred = Path(folder)/'auth.json'
            cred.write_text(json.dumps({'salt': 'aa', 'password_hash': 'bb', 'secret': 'cc'}))
            with patch.object(server, 'safe_video', return_value=str(cred)):
                self.assertEqual(self.request('/media?p=workbench/auth.json')[0], 403)
                self.assertEqual(self.request('/api/file?p=workbench/auth.json')[0], 403)
            with patch.object(server, 'ROOT', folder), patch.object(server, 'WEBDIST', tempfile.mkdtemp()):
                # 09-25 撤掉 ROOT 静态兜底后，这条从 403 变成 404（连"这文件存在"都不再说）；
                # 两种都算守住，_deny_file 本身的 403 语义仍由上面 /media 与 /api/file 两条覆盖。
                code, _, body = self.request('/auth.json')
                self.assertIn(code, (403, 404), 'workbench/ 树内的凭证文件不得按 URL 被读走')
                self.assertNotIn(b'password_hash', body)
            self.assertTrue(server._deny_file(str(cred.parent/'auth.json.bak')))

    def test_anonymous_asset_allowlist_cannot_escape_dist(self):
        """免认证只准拿前端产物：/assets/../x 会被静态兜底按 workbench/ 解析读走后端源码。"""
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'assets').mkdir()
            (Path(folder)/'assets'/'app-1a2b3c.js').write_text('chunk')
            with patch.object(server, 'WEBDIST', folder):
                self.assertTrue(server._anon_asset_ok('/assets/app-1a2b3c.js'))
                for probe in ('/assets/../server.py', '/assets/%2e%2e/server.py',
                              '/assets/..%2fserver.py', '/media?p=x', '/workbench/server.py'):
                    with self.subTest(probe=probe):
                        self.assertFalse(server._anon_asset_ok(probe))

    def test_create_batch_requires_explicit_shot_selection(self):
        """空镜头选择曾一路透传给 create_batch 的「未选=全板」兜底 = 整板付费生成。"""
        vendor = {"id": "v1", "enabled": True, "label": "V1", "models": {"image": "m"}}
        def body(ids):
            return json.dumps({"project": "p", "board": "b", "type": "image",
                               "vendor_id": "v1", **({} if ids is None else {"shot_ids": ids})}).encode()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(server, 'proj_dir', return_value=folder), \
             patch.object(server, 'load_vendors', return_value=[vendor]), \
             patch.object(server.H, 'spawn_job', return_value=9) as spawn:
            for ids in (None, [], [""], ["  "]):
                with self.subTest(ids=ids):
                    code, _, payload = self.request('/api/create/batch', 'POST', body(ids),
                                                    {'Content-Type': 'application/json'})
                    self.assertEqual(code, 400)
                    self.assertIn('至少选择一个镜头'.encode(), payload)
            self.assertEqual(spawn.call_count, 0)
            code, _, payload = self.request('/api/create/batch', 'POST', body(["S2"]),
                                            {'Content-Type': 'application/json'})
            self.assertEqual(code, 200)
            cmd = spawn.call_args.args[1]
            self.assertEqual(cmd[cmd.index('--shot-id')+1], 'S2')

    def test_versions_batch_returns_map_and_blocks_traversal(self):
        """② 素材页一次要 248 个版本清单；批量端点必须一次回全，且越界路径不得带出任何真实路径。"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'projects' / 'P' / '素材' / '人物'
            (target / '.versions').mkdir(parents=True)
            (target / 'a.png').write_bytes(b'current')
            (target / '.versions' / 'a.20260920_101010.png').write_bytes(b'old')
            secret = root.parent / 'outside-should-never-leak.txt'
            secret.write_text('secret', encoding='utf-8')
            rel = 'projects/P/素材/人物/a.png'
            body = json.dumps({'paths': [rel, rel, '../outside-should-never-leak.txt', '']}).encode()
            with patch.object(server, 'VIDEO', _os.path.realpath(str(root))):
                code, _, payload = self.request('/api/versions/batch', 'POST', body,
                                                {'Content-Type': 'application/json'})
                self.assertEqual(code, 200)
                results = json.loads(payload)['results']
                self.assertEqual(sorted(results), ['../outside-should-never-leak.txt', rel],
                                 '重复与空路径应各自折叠成一条')
                ts = [v['ts'] for v in results[rel]]
                self.assertEqual(len(ts), 2, '最新 + 一条历史都应在（含 current 标记）')
                self.assertTrue(any(v.get('current') for v in results[rel]))
                self.assertEqual(results['../outside-should-never-leak.txt'], [],
                                 '越界路径只能回空，不能泄露根外内容')
                self.assertNotIn(str(secret).replace('\\', '/'), payload.decode('utf-8', 'replace'))
                self.assertNotIn('should-never-leak.png', payload.decode('utf-8', 'replace'),
                                 '越界条目不得被解析成根外真实文件回给客户端')
            code, _, payload = self.request('/api/versions/batch', 'POST', b'{"paths":[]}',
                                            {'Content-Type': 'application/json'})
            self.assertEqual(code, 400)

    def test_json_parse_error_returns_400(self):
        self.assertEqual(self.request('/api/project/new', 'POST', b'{', {'Content-Type': 'application/json'})[0], 400)

    def test_json_shape_guard_before_handlers(self):
        for endpoint in ('/api/project/new','/api/create/chatgpt/queue','/api/create/chatgpt/run/create','/api/storyboard/save'):
            for body in (b'[]', b'null', b'1', b'"text"', b'{'):
                with self.subTest(endpoint=endpoint, body=body):
                    self.assertEqual(self.request(endpoint,'POST',body,{'Content-Type':'application/json'})[0],400)

    def test_storyboard_routes_reject_escape_before_spawn(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); project=root/'project'; (project/'分镜').mkdir(parents=True)
            outside=root/'outside.json'; outside.write_text('{}')
            with patch.object(server,'proj_dir',return_value=str(project)), patch.object(server.H,'spawn_job') as spawn:
                for endpoint in ('/api/strategy/build','/api/creation/package','/api/creation/assemble'):
                    for path in (str(outside),'../../outside.json'):
                        code,_,_=self.request(endpoint,'POST',json.dumps({'project':'test','storyboard':path}).encode())
                        self.assertEqual(code,400)
                spawn.assert_not_called()

    def test_script_brief_roundtrip(self):
        # 制作规格（E05）：GET 缺文件返回完整默认；POST 校验并落 剧本/brief.json
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            code, _, payload = self.request('/api/script/brief?project=test')
            self.assertEqual(code, 200)
            body = json.loads(payload)
            self.assertEqual(body['brief']['episode_minutes'], 3)
            self.assertEqual(body['brief']['aspect_ratio'], '16:9')
            self.assertNotIn('resolution', body['brief'])   # resolution/fps 已下线
            self.assertNotIn('fps', body['brief'])
            self.assertFalse(body['exists'])
            code, _, payload = self.request('/api/script/brief', 'POST',
                                            json.dumps({'project': 'test', 'patch': {'episode_minutes': 7}}).encode())
            self.assertEqual(code, 200, payload)
            self.assertEqual(json.loads(payload)['brief']['episode_minutes'], 7)
            self.assertTrue((Path(folder) / '剧本' / 'brief.json').is_file())
            code, _, payload = self.request('/api/script/brief', 'POST',
                                            json.dumps({'project': 'test', 'patch': {'fps': 23}}).encode())
            self.assertEqual(code, 400)
            self.assertIn('fps', json.loads(payload)['err'])
            code, _, _ = self.request('/api/script/brief', 'POST', json.dumps({'project': 'test'}).encode())
            self.assertEqual(code, 400)   # 缺 patch 对象

    def test_error_payload_redacts_bearer_and_url_credentials(self):
        secret='Bearer abc.def-ghi_jkl https://user:password@example.test/x?api_key=secret-value'
        with patch.object(server,'knowledge_save',side_effect=RuntimeError(secret)):
            code,_,payload=self.request('/api/knowledge/card','POST',b'{}')
        self.assertEqual(code,500)
        for value in (b'abc.def-ghi_jkl',b'password',b'secret-value'):
            self.assertNotIn(value,payload)

    def test_acting_prepare_submits_job_without_llm_on_http_thread(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'分镜').mkdir(); (root/'分镜'/'E1.json').write_text('{}')
            module=SimpleNamespace(_read_board=lambda p: ({},'revision'))
            with patch.object(server,'proj_dir',return_value=folder), patch.object(server,'tools_mod',return_value=module), patch.object(server.H,'spawn_job',return_value=123) as spawn, patch.object(server,'load_vendors',return_value=[{'id':'test','enabled':True,'models':{'text':'m'}}]):
                code,_,data=self.request('/api/acting/prepare','POST',json.dumps({'project':'p','storyboard':'E1.json','vendor_id':'test','context':{},'shot_ids':['S1']}).encode())
                self.assertEqual(code,200,data)
                self.assertEqual(json.loads(data)['id'],123)
                cmd=spawn.call_args.args[1]
                self.assertEqual(Path(cmd[1]).name,'acting_prepare_job.py')
                request=json.loads(Path(cmd[2]).read_text(encoding='utf-8'))
                self.assertEqual(request['revision'],'revision')
                self.assertNotIn('api_key',request)

    def test_white_from_analysis_passes_mode(self):
        # E09：--mode 可选 body 参数透传（schematic/faithful），非法/缺省不透传（工具内默认 schematic）
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(server, 'proj_dir', return_value=folder), \
             patch.object(server.H, 'spawn_job', return_value=88) as spawn:
            code, _, body = self.request('/api/white/from_analysis', 'POST',
                                         b'{"project":"p","mode":"faithful"}')
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)['id'], 88)
            cmd = spawn.call_args.args[1]
            self.assertEqual(cmd[cmd.index('--mode') + 1], 'faithful')
            code, _, _ = self.request('/api/white/from_analysis', 'POST', b'{"project":"p"}')
            self.assertEqual(code, 200)
            self.assertNotIn('--mode', spawn.call_args.args[1])
            code, _, _ = self.request('/api/white/from_analysis', 'POST',
                                      b'{"project":"p","mode":"bogus"}')
            self.assertEqual(code, 200)
            self.assertNotIn('--mode', spawn.call_args.args[1])

    def test_client_errors_have_distinct_statuses(self):
        with patch.object(server,'knowledge_save',return_value=None):
            self.assertEqual(self.request('/api/knowledge/card','POST',b'{}')[0],400)
        with patch.object(server,'knowledge_delete',return_value=False):
            self.assertEqual(self.request('/api/knowledge/card/delete','POST',b'{}')[0],404)
        self.assertEqual(self.request('/api/create/chatgpt/queue','POST',b'{}')[0],400)

    def test_disabled_vendor_can_test_without_enabling(self):
        cfg={'id':'doubao-api','enabled':False,'api_key':'test','base_url':'https://ark.cn-beijing.volces.com/api/v3','models':{'image':'test-model'}}
        with patch.object(server,'load_vendors',return_value=[cfg]):
            body=json.dumps({'id':'doubao-api','kind':'image'}).encode()
            code,_,data=self.request('/api/env/test','POST',body,{'Content-Type':'application/json'})
            self.assertEqual(code,200)
            self.assertTrue(json.loads(data)['ok'])
            self.assertFalse(cfg['enabled'])

    def test_audio_request_saves_binding_and_returns_background_job(self):
        cfg={'id':'minimax','enabled':True,'models':{'speech':'speech-2.8-hd','music':'music-2.6'}}
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'load_vendors',return_value=[cfg]), patch.object(server,'proj_dir',return_value=folder), patch.object(server.H,'spawn_job',return_value=123) as spawn:
            root=Path(folder); (root/'素材').mkdir()
            (root/'素材'/'人物.json').write_text(json.dumps({'characters':[{'id':'hero','name':'主角'}]}),encoding='utf-8')
            body={'project':'test','type':'speech','vendor_id':'minimax','prompt':'你好','character_id':'hero','voice_id':'voice-1'}
            code,_,data=self.request('/api/create/run','POST',json.dumps(body).encode(),{'Content-Type':'application/json'})
            self.assertEqual(code,200,data)
            self.assertEqual(json.loads(data)['id'],123)
            item=json.loads((root/'创作'/'creation.json').read_text(encoding='utf-8'))['items'][0]
            self.assertEqual((item['character_id'],item['voice_id'],item['model']),('hero','voice-1','speech-2.8-hd'))
            self.assertIn('--voice-id',spawn.call_args.args[1])
            body['character_id']='missing'
            self.assertEqual(self.request('/api/create/run','POST',json.dumps(body).encode(),{'Content-Type':'application/json'})[0],400)
            self.assertEqual(spawn.call_count,1)

    def test_incompatible_plan_video_never_starts_single_or_batch_job(self):
        cfg={'id':'doubao','enabled':True,'api_key':'test',
             'base_url':'https://ark.cn-beijing.volces.com/api/plan/v3',
             'models':{'video':'doubao-seedance-1.5-pro'},
             'endpoints':{'video':'/contents/generations/tasks'}}
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'load_vendors',return_value=[cfg]), patch.object(server,'proj_dir',return_value=folder), patch.object(server.H,'spawn_job') as spawn:
            for endpoint in ('/api/create/run','/api/create/batch'):
                body=json.dumps({'project':'test','board':'test.json','type':'video','vendor_id':'doubao','prompt':'test'}).encode()
                code,_,data=self.request(endpoint,'POST',body,{'Content-Type':'application/json'})
                self.assertEqual(code,400)
                self.assertIn('Agent Plan',data.decode('utf-8'))
            spawn.assert_not_called()
            self.assertFalse((Path(folder)/'创作'/'creation.json').exists())
            body=json.dumps({'id':'doubao','kind':'video'}).encode()
            self.assertFalse(json.loads(self.request('/api/env/test','POST',body,{'Content-Type':'application/json'})[2])['ok'])

    def test_unhandled_exception_returns_one_500(self):
        with patch.object(server, 'scan_sources', side_effect=RuntimeError('test failure')):
            self.assertEqual(self.request('/api/sources')[0], 500)

    def test_matched_handler_return_value_never_triggers_fallback(self):
        def handler(h, ctx):
            h._send(200, 'text/plain', b'handled')
            return False
        with patch.object(server, 'ROUTES', {('GET', '/__test__'): handler}):
            self.assertEqual(self.request('/__test__')[2], b'handled')

    def test_silent_handler_is_500_not_404(self):
        with patch.object(server, 'ROUTES', {('GET', '/__test__'): lambda h, ctx: None}):
            self.assertEqual(self.request('/__test__')[0], 500)

    def test_second_response_is_blocked(self):
        def handler(h, ctx):
            h._send(200, 'text/plain', b'first')
            h._send(404, 'text/plain', b'second')
        with patch.object(server, 'ROUTES', {('GET', '/__test__'): handler}):
            self.assertEqual(self.request('/__test__')[2], b'first')

    def test_stream_error_closes_connection_without_second_response(self):
        def handler(h, ctx):
            h.send_response(200); h.send_header('Content-Length', '10'); h.end_headers()
            h.wfile.write(b'partial')
            raise OSError('simulated broken stream')
        with patch.object(server, 'ROUTES', {('GET', '/__test__'): handler}):
            self.assertEqual(self.request('/__test__')[2], b'partial')

    def test_response_guard_resets_on_keepalive(self):
        with patch.object(server.H, 'protocol_version', 'HTTP/1.1'), patch.object(server, 'scan_sources', return_value=[]):
            conn = http.client.HTTPConnection(*self.http.server_address, timeout=4)
            try:
                conn.request('GET', '/api/sources')
                first = conn.getresponse(); self.assertEqual(first.status, 200); first.read()
                sock = conn.sock
                conn.request('GET', '/api/sources')
                second = conn.getresponse(); self.assertEqual(second.status, 200); second.read()
                self.assertIs(conn.sock, sock)
            finally: conn.close()

    def test_continue_response_does_not_block_final_response(self):
        with patch.object(server.H, 'protocol_version', 'HTTP/1.1'):
            conn = http.client.HTTPConnection(*self.http.server_address, timeout=4)
            try:
                conn.request('POST', '/api/project/new', body=b'{', headers={'Expect': '100-continue'})
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
                response.read()
            finally: conn.close()

    def test_negative_content_length_is_400(self):
        self.assertEqual(self.request('/api/project/new', 'POST', headers={'Content-Length': '-1'})[0], 400)

    def test_static_asset_cache_header_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'assets'; path.mkdir(); (path/'test.js').write_text('test')
            with patch.object(server, 'WEBDIST', folder):
                code, headers, body = self.request('/assets/test.js')
                self.assertEqual((code, body), (200, b'test'))
                self.assertIn(b'Cache-Control: no-cache', headers)   # 开发期工具正确性优先：hash 资源也走协商缓存（防 SPA 懒加载旧 chunk）

    def test_multipart_bytes_arrive_unchanged(self):
        boundary = 'route-boundary'
        blob = b'\x00\xffreference-image'
        parts = [b'Content-Disposition: form-data; name="project"\r\n\r\ntest',
                 b'Content-Disposition: form-data; name="file"; filename="manifest.json"\r\n\r\n{}',
                 b'Content-Disposition: form-data; name="file"; filename="image.png"\r\n\r\n' + blob]
        body = b''.join(b'--'+boundary.encode()+b'\r\n'+p+b'\r\n' for p in parts) + b'--'+boundary.encode()+b'--\r\n'
        with patch.object(server, 'proj_dir', return_value='/test'), patch.object(server.chatgpt_import, 'import_package', return_value={'ok': True}) as importer:
            self.assertEqual(self.request('/api/create/chatgpt/import', 'POST', body, {'Content-Type': f'multipart/form-data; boundary={boundary}'})[0], 200)
            self.assertEqual(importer.call_args.args, ('/test', {}, {'image.png': blob}))

    def test_project_delete_moves_to_recycle_bin(self):
        # 删除项目=整目录移入 projects/.回收站/<项目>_<时间戳>/（不物理删除）；回收站与已删项目都不再进项目扫描
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'VIDEO', folder):
            pj = Path(folder)/'projects'; proj = pj/'zzz_test_del'
            proj.mkdir(parents=True)
            (proj/'项目.json').write_text('{"type":"拆片"}', encoding='utf-8')
            keep = pj/'zzz_keep'; keep.mkdir()
            code, _, payload = self.request('/api/project/delete', 'POST', json.dumps({'project': 'zzz_test_del'}).encode())
            self.assertEqual(code, 200, payload)
            row = json.loads(payload)
            self.assertTrue(row['ok'])
            self.assertIn('.回收站', row['recycled'])
            self.assertFalse(proj.exists())
            recycled = list((pj/'.回收站').iterdir())
            self.assertEqual(len(recycled), 1)
            self.assertTrue(recycled[0].name.startswith('zzz_test_del_'))
            self.assertTrue((recycled[0]/'项目.json').is_file())
            self.assertTrue(keep.is_dir())
            names = [p['name'] for p in server.scan_projects()]
            self.assertEqual(names, ['zzz_keep'])
            # 再删一次 → 404
            code, _, payload = self.request('/api/project/delete', 'POST', json.dumps({'project': 'zzz_test_del'}).encode())
            self.assertEqual(code, 404, payload)

    def test_project_delete_rejects_traversal_and_missing(self):
        # 路径穿越/分隔符/隐藏名/空名 → 400；不存在的项目 → 404；目录原样保留
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'VIDEO', folder):
            proj = Path(folder)/'projects'/'zzz_keep'; proj.mkdir(parents=True)
            outside = Path(folder)/'outside'; outside.mkdir()
            for bad in ('../outside', 'a/b', 'a\\b', '.', '..', '.回收站', ''):
                with self.subTest(bad=bad):
                    code = self.request('/api/project/delete', 'POST', json.dumps({'project': bad}).encode())[0]
                    self.assertEqual(code, 400)
            self.assertEqual(self.request('/api/project/delete', 'POST', json.dumps({'project': '不存在项目'}).encode())[0], 404)
            self.assertTrue(proj.is_dir())
            self.assertTrue(outside.is_dir())
            self.assertFalse((Path(folder)/'projects'/'.回收站').exists())


    def test_production_redo_segment_route_validation(self):
        # 局部修补路由：项目不存在 → 400；缺稳定 nonce → 入队前 400 拦截
        code, _, _ = self.request('/api/production/redo_segment', 'POST', json.dumps({'project': '不存在'}).encode())
        self.assertEqual(code, 400)
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            code, _, payload = self.request('/api/production/redo_segment', 'POST',
                                            json.dumps({'project': 'p', 'board': 'E1.json', 'target': 'v-1', 't0': 1, 't1': 2}).encode())
            self.assertEqual(code, 400)
            self.assertIn('nonce', json.loads(payload)['err'])

    def test_production_redo_segment_forces_action_and_spawns_job(self):
        # 路由强制 action=redo_segment/scope=V/type=video 后走 job 体系入队
        from types import SimpleNamespace
        from unittest.mock import Mock
        module = SimpleNamespace(enqueue=Mock(return_value={'ok': True, 'id': 55, 'item_id': 'prod-x'}))
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder), patch.object(server, 'tools_mod', return_value=module):
            code, _, data = self.request('/api/production/redo_segment', 'POST',
                                         json.dumps({'project': 'p', 'board': 'E1.json', 'target': 'v-1', 't0': 6, 't1': 8, 'nonce': 'redo-nonce-12345'}).encode())
            self.assertEqual(code, 200, data)
            self.assertEqual(json.loads(data)['id'], 55)
            body = module.enqueue.call_args.args[1]
            self.assertEqual((body['action'], body['scope'], body['type']), ('redo_segment', 'V', 'video'))
            self.assertEqual((body['t0'], body['t1'], body['target']), (6, 8, 'v-1'))


    def test_asset_http_uses_shared_transaction_and_preserves_reference_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            assets = Path(folder) / '素材'
            assets.mkdir()
            (assets / '人物.json').write_text(json.dumps({'characters': [{'id': 'hero', 'name': '主角',
                'states': [{'id': 'rain', 'label': '雨中'}]}]}), encoding='utf-8')
            body = {'project': 'test', 'kind': 'prop', 'id': 'bow', 'name': '弩',
                    'parent_ref': '@character:hero', 'derived_from': '@character:hero#rain'}
            code, _, payload = self.request('/api/assets/create', 'POST', json.dumps(body).encode())
            self.assertEqual(code, 200, payload)
            self.assertEqual(json.loads(payload)['asset']['derived_from'], '@character:hero#rain')
            self.assertEqual(self.request('/api/assets/create', 'POST', json.dumps(body).encode())[0], 409)
            before = (assets / '道具.json').read_bytes()
            invalid = {'project': 'test', 'updates': [{'ref': '@prop:bow', 'parent_ref': None}, None]}
            self.assertEqual(self.request('/api/assets/relations', 'POST', json.dumps(invalid).encode())[0], 400)
            self.assertEqual((assets / '道具.json').read_bytes(), before)

    def test_style_http_patch_keeps_other_dimensions(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'proj_dir', return_value=folder):
            path = Path(folder) / '剧本/style.json'
            path.parent.mkdir()
            path.write_text('{"script":"three-act"}', encoding='utf-8')
            with patch.object(server.chatgpt_queue, 'refresh_queued_asset_jobs', return_value=0):
                code, _, raw = self.request('/api/skills/style', 'POST',
                    json.dumps({'project': 'test', 'patch': {'image_visual': 'ink-wash'}}).encode())
            self.assertEqual(code, 200, raw)
            self.assertEqual(json.loads(raw)['style'], {'script': 'three-act', 'image_visual': 'ink-wash'})


class RegistryTests(unittest.TestCase):
    def test_duplicate_routes_rejected(self):
        self.assertTrue(hasattr(server, 'route'), '缺少路由注册机制')
        class Duplicate:
            @server.route('GET', '/same')
            def first(self, ctx): pass
            @server.route('GET', '/same')
            def second(self, ctx): pass
        with self.assertRaises(ValueError): server.build_route_table(Duplicate)

    def test_route_inventory_matches_frozen_contract(self):
        self.assertTrue(hasattr(server, 'ROUTES'), '缺少路由表')
        expected = json.loads(Path(__file__).with_name('http_route_contract.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted([list(key) for key in server.ROUTES]), expected)


if __name__ == '__main__': unittest.main()
