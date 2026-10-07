# -*- coding: utf-8 -*-
"""RH 原生协议契约：真实 HTTP 边界、参考素材和异步任务不丢失。"""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from llm_openai import VendorClient, VendorError
from provider_catalog import normalize_vendors
from reference_limits import reference_limit
from video_profiles import capabilities, settings


class RunningHubTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.calls.append((self.path, dict(self.headers), raw))
                value = owner.responses.pop(0) if owner.responses else {'code': 0}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            do_PUT = do_POST

            def do_GET(self):
                owner.calls.append((self.path, dict(self.headers), b''))
                self.send_response(200)
                self.end_headers()
                if self.path.startswith('/api/webapp/apiCallDemo'):
                    self.wfile.write(b'{"code":0,"data":{"curl":"example"}}')
                elif self.path == '/v1/models':
                    self.wfile.write(b'{"data":[{"id":"openai/example"}]}')
                else:
                    self.wfile.write(b'generated-media')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.cfg = dict(id='runninghub', base_url=self.base, api_key='test-key', enabled=True,
                        models=dict(image='seedream-v5-pro/text-to-image',
                                    image_edit='seedream-v5-pro/image-to-image',
                                    video='minimax/hailuo-h3/multimodal-to-video',
                                    speech='rhart-audio/text-to-audio/speech-2.8-hd',
                                    music='minimax/music-2.6/text-to-instrumental', text='openai/example'),
                        extra=dict(llm_base_url=self.base + '/v1'))
        self.client = VendorClient.from_config(self.cfg)
        self.bill = patch.object(self.client, '_bill')
        self.bill.start()
        self.addCleanup(self.bill.stop)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def json_body(self, i=0):
        return json.loads(self.calls[i][2])

    def success(self, suffix='png'):
        return dict(taskId='rh-task', status='SUCCESS', results=[
            dict(url=self.base + '/result.' + suffix, outputType=suffix)])

    def test_disabled_default_added_without_overwriting_users(self):
        rows = normalize_vendors([])
        rh = next((v for v in rows if v['id'] == 'runninghub'), None)
        self.assertIsNotNone(rh, '默认目录缺少 RH')
        self.assertFalse(rh['enabled'])
        self.assertEqual(rh['api_key'], '')
        old = dict(rh, api_key='saved-key', enabled=True)
        self.assertEqual(next(v for v in normalize_vendors([old]) if v['id'] == 'runninghub')['api_key'], 'saved-key')

    def test_caps_follow_each_official_schema(self):
        self.assertEqual(reference_limit('runninghub', self.cfg['models']['video'], 'video'), 9)
        self.assertEqual(reference_limit('runninghub', self.cfg['models']['image_edit'], 'image_edit'), 10)
        self.assertEqual(reference_limit('runninghub', 'rhart-image-g-2-official/image-to-image', 'image_edit'), 10)
        p = capabilities(self.cfg)
        self.assertTrue(p['known'])
        self.assertEqual((p['max_refs'], p['max_video'], p['max_audio']), (9, 3, 3))
        self.assertEqual(p['min_duration'], 5)
        self.assertNotIn('first_frame', p['modes'])
        self.assertFalse(capabilities(dict(self.cfg, models={'video': 'unknown'}))['known'])
        with self.assertRaises(ValueError):
            settings(self.cfg, {'duration': 4})

    def test_reference_limit_uses_configured_edit_slot(self):
        cfg = dict(self.cfg, models={**self.cfg['models'], 'image':'text-model-without-edit-pair'})
        self.assertEqual(reference_limit('runninghub', cfg['models']['image'], 'image', cfg), 10,
                         '制作入口按改图槽执行带引用的请求，上限必须使用同一槽')
        self.assertEqual(reference_limit('runninghub', 'unknown', 'video', cfg), 0)

    def test_h3_payload_query_post_and_download(self):
        self.responses = [dict(taskId='rh-task', status='RUNNING'),
                          dict(taskId='rh-task', status='QUEUED'), self.success('mp4')]
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / 'video.mp4')
            refs = ['https://example.test/r.png'] * 9
            result = self.client.generate_video('参考素材演绎自然动作', refs, out_path=out,
                extra={'duration': 5, 'resolution': '768P', 'ratio': '16:9',
                       'video_refs': ['https://example.test/v.mp4'], 'audio_refs': ['https://example.test/a.mp3']},
                poll_interval=0)
            self.assertEqual(result, out)
            self.assertEqual(Path(out).read_bytes(), b'generated-media')
        payload = self.json_body()
        self.assertEqual(self.calls[0][0], '/openapi/v2/minimax/hailuo-h3/multimodal-to-video')
        self.assertEqual(payload['duration'], '5')
        self.assertEqual(len(payload['imageUrls']), 9)
        self.assertEqual(payload['videoUrls'], ['https://example.test/v.mp4'])
        self.assertNotIn('content', payload)
        self.assertNotIn('model', payload)
        self.assertEqual(self.calls[1][0], '/openapi/v2/query')
        self.assertEqual(self.json_body(1), {'taskId': 'rh-task'})
        self.assertEqual(self.client.last_request['task_id'], 'rh-task')

    def test_upload_local_image_then_edit_and_respect_ratio(self):
        self.responses = [dict(code=200, data={'download_url': 'https://example.test/uploaded.png'}), self.success()]
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / '人物.png'
            ref.write_bytes(b'local-pixels')
            out = Path(tmp) / 'out.png'
            self.client.generate_image('保留角色的外观只改变姿势', str(out), image_refs=[str(ref)],
                                       extra={'ratio': '16:9', 'size': '2848x1600'}, negative_prompt='文字水印')
            self.assertEqual(out.read_bytes(), b'generated-media')
        self.assertEqual(self.calls[0][0], '/openapi/v2/media/upload/binary')
        self.assertIn(b'local-pixels', self.calls[0][2])
        self.assertEqual(self.calls[0][1]['Authorization'], 'Bearer test-key')
        self.assertEqual(self.calls[1][0], '/openapi/v2/seedream-v5-pro/image-to-image')
        payload = self.json_body(1)
        self.assertEqual(payload['imageUrls'], ['https://example.test/uploaded.png'])
        self.assertEqual((payload['width'], payload['height']), (2848, 1600))
        self.assertNotIn('resolution', payload, 'resolution 会覆盖 width/height，破坏指定画幅')
        self.assertIn('文字水印', payload['prompt'])

    def test_reject_missing_refs_unknown_fields_and_over_limit_before_submit(self):
        for kwargs in (dict(mode='edit'), dict(image_refs=['https://example.test/x'] * 11),
                       dict(extra={'madeUpParameter': 'x'})):
            with self.subTest(kwargs=kwargs), self.assertRaises(VendorError):
                self.client.generate_image('生成测试画面', 'unused.png', **kwargs)
        self.assertEqual(self.calls, [])

    def test_no_regeneration_when_poll_fails_or_result_type_is_wrong(self):
        self.responses = [dict(taskId='rh-task', status='RUNNING'),
                          dict(taskId='rh-task', status='FAILED', errorMessage='provider HTTP 500 failure')]
        with self.assertRaisesRegex(VendorError, 'rh-task'):
            self.client.generate_image('生成测试画面', 'unused.png', extra={'poll_interval': 0})
        self.assertEqual(sum(path.endswith('text-to-image') for path, _, _ in self.calls), 1)
        self.assertEqual(self.client.last_request['task_id'], 'rh-task')
        self.responses = [self.success('mp4')]
        with self.assertRaisesRegex(VendorError, '图片'):
            self.client.generate_image('生成测试画面', 'unused.png')

    def test_catalog_filters_media_and_llm_uses_independent_host(self):
        from runninghub_catalog import model_options
        options = model_options('image_edit')
        self.assertIn('seedream-v5-pro/image-to-image', [x['id'] for x in options])
        self.assertNotIn(self.cfg['models']['video'], [x['id'] for x in options])
        self.assertFalse(any('[Deprecated]' in x['id'] for x in options))
        self.assertEqual(self.client.list_models(kind='text'), ['openai/example'])
        self.responses = [{'choices': [{'message': {'content': 'ok'}}]}]
        self.assertEqual(self.client.chat([{'role': 'user', 'content': '你好'}]), 'ok')
        self.assertEqual(self.calls[-1][0], '/v1/chat/completions')

    def test_video_slot_options_have_usable_defaults(self):
        from runninghub_catalog import model_options
        for row in model_options('video'):
            with self.subTest(model=row['id']):
                defaults=settings({'id':'runninghub','models':{'video':row['id']}})
                self.assertGreater(defaults['duration'],0)

    def test_tts_music_and_raw_workflow(self):
        self.responses = [self.success('mp3'), self.success('mp3')]
        with tempfile.TemporaryDirectory() as tmp:
            self.client.generate_speech('你好', str(Path(tmp) / 'speech.mp3'), extra={'voice': 'Wise_Woman'})
            self.client.generate_music('舒缓的钢琴曲', str(Path(tmp) / 'music.mp3'))
        self.assertEqual(self.json_body()['voice_id'], 'Wise_Woman')
        self.assertEqual(self.json_body()['text'], '你好')
        self.assertFalse(self.json_body()['enable_base64_output'])
        self.assertNotIn('voice', self.json_body())
        from runninghub_client import RunningHubClient
        adapter = RunningHubClient(self.client)
        self.responses = [{'code': 0, 'data': {'taskId': 'workflow-task', 'taskStatus': 'RUNNING'}}]
        result = adapter.call('/task/openapi/create', {'workflowId': 'mine', 'nodeInfoList': []})
        self.assertEqual(result['data']['taskId'], 'workflow-task')
        self.assertEqual(self.json_body(-1)['apiKey'], 'test-key')

    def test_video_families_use_their_own_field_names_and_types(self):
        cases = [
            ('minimax/hailuo-h3/image-to-video', {'firstFrameUrl':'https://example.test/first.png', 'lastFrameUrl':'https://example.test/last.png'}, {'resolution':'768P'}, True),
            ('bytedance/seedance-2.0-global/image-to-video', {'firstFrameUrl':'https://example.test/first.png', 'lastFrameUrl':'https://example.test/last.png'}, {'resolution':'720p'}, True),
            ('vidu/reference-to-video-q2', {'imageUrls':['https://example.test/first.png']}, {'resolution':'720p'}, False),
        ]
        for model, media, opts, frames in cases:
            with self.subTest(model=model):
                self.responses = [self.success('mp4')]
                self.client.generate_video('人物走向教室门口的自然动作', model=model,
                    image_refs=['https://example.test/first.png'] if not frames else [],
                    first_frame='https://example.test/first.png' if frames else None,
                    last_frame='https://example.test/last.png' if frames else None,
                    extra={'duration':5,**opts}, poll_interval=0)
                body = self.json_body(-1)
                for key, value in media.items(): self.assertEqual(body[key], value)
                self.assertEqual(self.calls[-1][0], '/openapi/v2/' + model)

    def test_json_config_survives_flat_provider_storage(self):
        self.client.cfg['extra']['api_parameters'] = '{"video":{"aigc_watermark":false},"speech":{"emotion":"neutral"}}'
        self.responses = [self.success('mp4')]
        self.client.generate_video('人物自然行走', extra={'duration':5, 'ratio':'16:9'})
        self.assertIs(self.json_body()['aigc_watermark'], False)

    def test_default_video_ratio_is_usable_without_explicit_settings(self):
        self.responses = [self.success('mp4')]
        self.client.generate_video('人物自然行走')
        self.assertEqual(self.json_body()['ratio'], '16:9')

    def test_unknown_output_and_missing_task_do_not_resubmit(self):
        self.responses = [{'status':'RUNNING'}]
        with self.assertRaisesRegex(VendorError, 'taskId'):
            self.client.generate_image('生成自然教室画面', 'unused.png')
        self.assertEqual(len(self.calls), 1)

    def test_configured_image_workflow_uses_its_slot_node_mapping(self):
        self.client.models['image'] = 'workflow:owned-app'
        self.client.cfg['extra']['api_parameters'] = {'image': {
            'prompt_node': {'nodeId': '6', 'fieldName': 'text'}}}
        self.responses = [self.success()]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'result.png'
            self.client.generate_image('生成自然教室画面', str(out))
            self.assertEqual(out.read_bytes(), b'generated-media')
        self.assertEqual(self.calls[0][0], '/openapi/v2/run/ai-app/owned-app')
        self.assertEqual(self.json_body()['nodeInfoList'], [
            {'nodeId': '6', 'fieldName': 'text', 'fieldValue': '生成自然教室画面'}])

    def test_configured_edit_workflow_keeps_the_reference_and_prompt(self):
        self.client.models['image_edit'] = 'workflow:edit-app'
        self.client.cfg['extra']['api_parameters'] = {'image_edit': {
            'prompt_node': {'nodeId': '6', 'fieldName': 'text'},
            'image_nodes': [{'nodeId': '1', 'fieldName': 'image'}]}}
        self.responses = [self.success()]
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'result.png'
            self.client.generate_image('保留人物服装', str(out), image_refs=['https://example.test/ref.png'])
        self.assertEqual(self.calls[0][0], '/openapi/v2/run/ai-app/edit-app')
        self.assertEqual(self.json_body()['nodeInfoList'], [
            {'nodeId': '6', 'fieldName': 'text', 'fieldValue': '保留人物服装'},
            {'nodeId': '1', 'fieldName': 'image', 'fieldValue': 'https://example.test/ref.png'}])

    def test_workflow_reference_limit_comes_from_configured_image_nodes(self):
        cfg = dict(self.cfg, models={**self.cfg['models'], 'image_edit': 'workflow:edit-app'},
                   extra={'api_parameters': json.dumps({'image_edit': {
                       'image_nodes': [{'nodeId': '1', 'fieldName': 'image'}, {'nodeId': '2', 'fieldName': 'image'}]}})})
        self.assertEqual(reference_limit('runninghub', cfg['models']['image'], 'image', cfg), 2)
        self.assertEqual(reference_limit('runninghub', 'workflow:edit-app', 'image_edit', cfg), 2)

    def test_single_image_workflow_slot_can_receive_declared_reference_nodes(self):
        from creation_media import image_route
        self.client.models.pop('image_edit')
        self.client.models['image'] = 'workflow:reference-app'
        self.client.cfg['extra']['api_parameters'] = {'image': {
            'prompt_node': {'nodeId': '6', 'fieldName': 'text'}, 'image_nodes': [{'nodeId': '1', 'fieldName': 'image'}]}}
        slot, mode, model = image_route(self.client.cfg, True)
        self.assertEqual(slot, 'image')
        self.assertEqual(reference_limit('runninghub', model, 'image_edit', self.client.cfg), 1)
        self.responses = [self.success()]
        with tempfile.TemporaryDirectory() as folder:
            self.client.generate_image('保持衣着', str(Path(folder) / 'result.png'), model=model,
                                       mode=mode, image_refs=['https://example.test/ref.png'])
        self.assertEqual(self.calls[0][0], '/openapi/v2/run/ai-app/reference-app')
        self.assertEqual(self.json_body()['nodeInfoList'][1]['fieldValue'], 'https://example.test/ref.png')

    def test_workflow_missing_or_insufficient_node_mapping_rejects_before_submit(self):
        for parameters, refs in (({}, []),
                ({'prompt_node': {'nodeId': '6', 'fieldName': 'text'}, 'image_nodes': [{'nodeId': '1', 'fieldName': 'image'}]},
                 ['https://example.test/a.png', 'https://example.test/b.png']),
                ({'prompt_node': {'nodeId': '', 'fieldName': 'text'}}, []),
                ({'node_info_list': [{'nodeId': '6', 'fieldName': 'text', 'fieldValue': '旧内容'}]}, [])):
            with self.subTest(parameters=parameters):
                self.client.models['image'] = self.client.models['image_edit'] = 'workflow:owned-app'
                self.client.cfg['extra']['api_parameters'] = {'image': parameters, 'image_edit': parameters}
                with self.assertRaisesRegex(VendorError, '节点|映射'):
                    self.client.generate_image('当前请求的画面', 'unused.png', image_refs=refs)
        self.assertEqual(self.calls, [], '任何素材上传或工作流提交前必须完整校验映射')

    def test_workflow_keeps_negative_in_mapped_prompt(self):
        self.client.models['image'] = 'workflow:owned-app'
        self.client.cfg['extra']['api_parameters'] = {'image': {'prompt_node': {'nodeId': '6', 'fieldName': 'text'}}}
        self.responses = [self.success()]
        with tempfile.TemporaryDirectory() as folder:
            self.client.generate_image('当前画面', str(Path(folder) / 'image.png'), negative_prompt='字幕水印')
        self.assertIn('字幕水印', self.json_body()['nodeInfoList'][0]['fieldValue'])

    def test_workflow_request_parameters_require_explicit_node_mappings_before_upload(self):
        self.client.models['image_edit'] = 'workflow:owned-app'
        base = {'prompt_node': {'nodeId': '6', 'fieldName': 'text'}, 'image_nodes': [{'nodeId': '1', 'fieldName': 'image'}]}
        for extra, mappings in (({'ratio': '9:16'}, {}),
                ({'ratio': '9:16', 'size': '1600x2848'}, {'ratio': {'nodeId': '2', 'fieldName': 'aspect'}}),
                ({'custom_strength': 0.5}, {})):
            with self.subTest(extra=extra):
                self.client.cfg['extra']['api_parameters'] = {'image_edit': {**base, 'parameter_nodes': mappings}}
                missing = next(key for key in extra if key not in mappings)
                with tempfile.TemporaryDirectory() as folder:
                    ref = Path(folder) / 'ref.png'
                    ref.write_bytes(b'local-reference')
                    with self.assertRaisesRegex(VendorError, missing):
                        self.client.generate_image('当前画面', 'unused.png', image_refs=[str(ref)], extra=extra)
        self.assertEqual(self.calls, [], '缺参数映射不得先上传或提交工作流')

    def test_workflow_explicit_parameter_nodes_receive_exact_request_values(self):
        self.client.models['image'] = 'workflow:owned-app'
        self.client.cfg['extra']['api_parameters'] = {'image': {
            'prompt_node': {'nodeId': '6', 'fieldName': 'text'}, 'parameter_nodes': {
                'ratio': {'nodeId': '2', 'fieldName': 'aspect'},
                'size': {'nodeId': '3', 'fieldName': 'size'},
                'custom_strength': {'nodeId': '4', 'fieldName': 'strength'}}}}
        self.responses = [self.success()]
        with tempfile.TemporaryDirectory() as folder:
            self.client.generate_image('当前画面', str(Path(folder) / 'image.png'),
                extra={'ratio': '9:16', 'size': '1600x2848', 'custom_strength': 0.5})
        values = {(row['nodeId'], row['fieldName']): row['fieldValue'] for row in self.json_body()['nodeInfoList']}
        self.assertEqual(values[('2', 'aspect')], '9:16')
        self.assertEqual(values[('3', 'size')], '1600x2848')
        self.assertEqual(values[('4', 'strength')], 0.5)

    def test_workflow_requires_explicit_field_names_before_upload(self):
        self.client.models['image_edit'] = 'workflow:owned-app'
        for location in ('prompt_node', 'image_nodes', 'parameter_nodes'):
            for field_name in (None, '', '   '):
                with self.subTest(location=location, field_name=field_name):
                    parameters = {
                        'prompt_node': {'nodeId': '6', 'fieldName': 'text'},
                        'image_nodes': [{'nodeId': '1', 'fieldName': 'image'}],
                        'parameter_nodes': {'ratio': {'nodeId': '2', 'fieldName': 'aspect'}}}
                    target = (parameters['image_nodes'][0] if location == 'image_nodes' else
                              parameters['parameter_nodes']['ratio'] if location == 'parameter_nodes' else
                              parameters['prompt_node'])
                    if field_name is None:
                        target.pop('fieldName')
                    else:
                        target['fieldName'] = field_name
                    self.client.cfg['extra']['api_parameters'] = {'image_edit': parameters}
                    if location == 'image_nodes':
                        self.assertEqual(reference_limit('runninghub', 'workflow:owned-app', 'image_edit', self.client.cfg), 0)
                    with tempfile.TemporaryDirectory() as folder:
                        ref = Path(folder) / 'ref.png'
                        ref.write_bytes(b'local-reference')
                        with self.assertRaisesRegex(VendorError, 'fieldName'):
                            self.client.generate_image('当前画面', 'unused.png', image_refs=[str(ref)], extra={'ratio': '9:16'})
        self.assertEqual(self.calls, [], '未显式填写远端字段名时不得上传或提交')

    def test_workflow_video_reports_missing_production_contract(self):
        cfg = dict(self.cfg, models={'video': 'workflow:owned-app'})
        self.assertFalse(capabilities(cfg)['known'])
        with self.assertRaisesRegex(ValueError, '节点与能力契约'):
            settings(cfg)

    def test_invalid_refs_and_wrong_schema_are_rejected_before_upload(self):
        from runninghub_client import RunningHubClient
        adapter = RunningHubClient(self.client)
        with self.assertRaisesRegex(VendorError, '类型'):
            adapter.call('minimax/hailuo-h3/multimodal-to-video', {'prompt':'hello','duration':5,'resolution':'768P'})
        self.assertEqual(self.calls, [])

    def test_workflow_upload_returns_loadimage_filename(self):
        from runninghub_client import RunningHubClient
        self.responses = [{'code':0,'data':{'fileName':'api/workflow-input.png','fileType':'input'}}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'素材.png'
            path.write_bytes(b'workflow-reference')
            name = RunningHubClient(self.client).upload(str(path), workflow=True)
        self.assertEqual(name,'api/workflow-input.png')
        endpoint, headers, raw = self.calls[0]
        self.assertEqual(endpoint, '/task/openapi/upload')
        self.assertIn(b'name="apiKey"',raw)
        self.assertIn(b'test-key',raw)
        self.assertIn(b'name="fileType"',raw)
        self.assertIn(b'input',raw)
        self.assertIn(b'workflow-reference',raw)

    def test_legacy_workflow_wait_uses_status_and_outputs(self):
        from runninghub_client import RunningHubClient
        adapter = RunningHubClient(self.client)
        self.responses = [{'code':0,'data':{'taskId':'legacy-task','taskStatus':'RUNNING'}},
            {'code':0,'data':'SUCCESS'}, {'code':0,'data':[{'fileUrl':self.base+'/legacy.png','fileType':'png'}]}]
        submitted = adapter.call('/task/openapi/create',{'workflowId':'owned-workflow',
            'nodeInfoList':[{'nodeId':'6','fieldName':'text','fieldValue':'生成自然场景'}]})
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'result.png'
            self.assertEqual(adapter.wait(submitted,interval=0,expected='image',out_path=out),str(out))
            self.assertEqual(out.read_bytes(),b'generated-media')
        self.assertEqual([r[0] for r in self.calls[:3]],['/task/openapi/create','/task/openapi/status','/task/openapi/outputs'])
        self.assertEqual(self.client.last_request['query_protocol'],'workflow')

    def test_lora_upload_put_is_signed_without_bearer(self):
        import hashlib
        from runninghub_client import RunningHubClient
        self.responses = [{'code':0,'data':{'fileName':'api-lora/test.safetensors','url':self.base+'/signed-lora'}},{}]
        raw=b'lora-weight-bytes'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'风格.safetensors'
            path.write_bytes(raw)
            name=RunningHubClient(self.client).upload_lora(path)
        self.assertEqual(name,'api-lora/test.safetensors')
        self.assertEqual(self.json_body()['md5Hex'],hashlib.md5(raw).hexdigest())
        self.assertEqual(self.calls[1][2],raw)
        self.assertNotIn('Authorization',self.calls[1][1])

    def test_app_example_get_injects_key_and_redacts_response(self):
        from runninghub_client import RunningHubClient
        from urllib.parse import parse_qs,urlsplit
        adapter=RunningHubClient(self.client)
        adapter.call('/api/webapp/apiCallDemo', {'webappId':'owned-app'})
        self.assertEqual(parse_qs(urlsplit(self.calls[0][0]).query),{'webappId':['owned-app'],'apiKey':['test-key']})
        public=adapter.public_result({'data':{'curl':'Authorization: Bearer test-key'},'url':'https://example.test/signed'})
        self.assertNotIn('test-key',json.dumps(public))
        self.assertEqual(public['url'],'https://example.test/signed')

    def test_speech_flow_uses_rh_voice_not_openai_default(self):
        import voice_assets
        board = {'shots':[{'id':'S1','lines':[{'speaker':'hero','line':'你好'}]}]}
        body = {'action':'speech','character_id':'hero','board':'E1.json'}
        cfg = dict(self.cfg, extra={'voice_id':'Wise_Woman'})
        with patch.object(voice_assets,'resolve',side_effect=ValueError('尚未绑定')), patch.object(voice_assets,'read_board',return_value=(board,'')):
            packet = voice_assets.prepare('unused-project', body, cfg)
            self.assertEqual(packet['voice']['voice_id'], 'Wise_Woman')
            with self.assertRaisesRegex(ValueError, '音色'):
                voice_assets.prepare('unused-project', body, dict(cfg,extra={}))



if __name__ == '__main__':
    unittest.main()
