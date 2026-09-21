# -*- coding: utf-8 -*-
"""真实 HTTP 边界：响应次数、文件流、静态兜底和上传体不被分发器消费。"""
import json
import http.client
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import workbench.server as server


class HttpRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

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
                self.assertIn(b'Cache-Control: public, max-age=31536000, immutable', headers)

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
