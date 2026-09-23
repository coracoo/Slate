# -*- coding: utf-8 -*-
"""plan v1 四条路由的 HTTP 边界：generate 走 job、save 校验 400 明细、get/list。"""
import json
import http.client
import os
import socket
import tempfile
import threading
import unittest
import urllib.parse
os.environ.setdefault('SLATE_NO_AUTH', '1')
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import workbench.server as server

GOOD_PLAN = {"version": 1, "name": "军帐", "canvas": {"w": 12, "h": 9},
             "room": {"walls": [[0, 0], [12, 0], [12, 9], [0, 9]]},
             "props": [{"id": "p1", "label": "长案", "shape": "rect", "center": [6, 4.5], "size": [3, 1]}],
             "actors": [{"id": "c", "name": "主角", "pos": [6, 6]}]}
# 中文名进 query string 必须先 URL 编码（HTTP 请求行不允许原始多字节字符）
QN = urllib.parse.quote('军帐')


class PlanRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def request(self, path, method='GET', body=b''):
        fields = {'Host': 'localhost', 'Connection': 'close', 'Content-Length': str(len(body))}
        request = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n'
        with socket.create_connection(self.http.server_address, timeout=4) as sock:
            sock.sendall(request.encode() + body)
            chunks = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
        head, payload = b''.join(chunks).split(b'\r\n\r\n', 1)
        return int(head.split()[1]), payload

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = self._td.name

    def tearDown(self):
        self._td.cleanup()

    def test_get_404_and_save_roundtrip(self):
        with patch.object(server, 'proj_dir', return_value=self.proj):
            code, body = self.request('/api/plan?project=p&name=' + QN)
            self.assertEqual(code, 404)
            self.assertIn('平面图不存在', body.decode('utf-8'))

            code, body = self.request('/api/plan/save', 'POST', json.dumps(
                {'project': 'p', 'name': '军帐', 'plan': GOOD_PLAN}, ensure_ascii=False).encode())
            self.assertEqual(code, 200, body)
            self.assertTrue(json.loads(body)['ok'])
            saved = os.path.join(self.proj, '推演', '平面图_军帐.plan.json')
            self.assertTrue(os.path.isfile(saved))

            code, body = self.request('/api/plan?project=p&name=' + QN)
            data = json.loads(body)
            self.assertEqual(code, 200)
            self.assertEqual(data['plan']['name'], '军帐')

    def test_save_validate_error_400_with_detail(self):
        bad = json.loads(json.dumps(GOOD_PLAN))
        bad['actors'][0]['pos'] = [6, 99]
        with patch.object(server, 'proj_dir', return_value=self.proj):
            code, body = self.request('/api/plan/save', 'POST', json.dumps(
                {'project': 'p', 'name': '军帐', 'plan': bad}, ensure_ascii=False).encode())
        data = json.loads(body)
        self.assertEqual(code, 400)
        self.assertIn('校验未通过', data['err'])
        self.assertTrue(any('超出画布' in e['message'] for e in data['errors']))

    def test_save_reject_non_object(self):
        with patch.object(server, 'proj_dir', return_value=self.proj):
            code, body = self.request('/api/plan/save', 'POST', json.dumps(
                {'project': 'p', 'name': '军帐', 'plan': [1]}).encode())
            self.assertEqual(code, 400)
            self.assertIn('plan 必须是对象', body.decode('utf-8'))

    def test_list(self):
        with patch.object(server, 'proj_dir', return_value=self.proj):
            code, body = self.request('/api/plan/list?project=p')
            self.assertEqual(json.loads(body)['plans'], [])
            plan = dict(GOOD_PLAN, scene_ref='loc_tent')
            self.request('/api/plan/save', 'POST', json.dumps(
                {'project': 'p', 'name': '军帐', 'plan': plan}, ensure_ascii=False).encode())
            code, body = self.request('/api/plan/list?project=p')
            plans = json.loads(body)['plans']
            self.assertEqual([p['name'] for p in plans], ['军帐'])
            self.assertEqual(plans[0]['file'], '平面图_军帐.plan.json')
            self.assertEqual(plans[0]['scene_ref'], 'loc_tent')   # 场景绑定直出（前端按场景名显示）

    def test_list_enriched_counts_and_validate(self):
        # 列表直出计数与 validate 状态（页面免逐张二次请求）
        bad = json.loads(json.dumps(GOOD_PLAN))
        bad['cameras'] = [{'id': 'cam1', 'pos': [1, 1], 'look': [2, 2], 'fov': 999}]  # fov 越界
        os.makedirs(os.path.join(self.proj, '推演'), exist_ok=True)
        json.dump(bad, open(os.path.join(self.proj, '推演', '平面图_坏.plan.json'), 'w', encoding="utf-8"),
                  ensure_ascii=False)
        with patch.object(server, 'proj_dir', return_value=self.proj):
            code, body = self.request('/api/plan/list?project=p')
        row = json.loads(body)['plans'][0]
        self.assertEqual(row['counts'], {'props': 1, 'actors': 1, 'paths': 0, 'cameras': 1, 'zones': 0})
        self.assertFalse(row['validate']['ok'])
        self.assertEqual(row['validate']['errors'], 1)
        self.assertTrue(any('fov' in d for d in row['validate']['details']))

    def test_strategy_build_plan_mode(self):
        os.makedirs(os.path.join(self.proj, '推演'), exist_ok=True)
        json.dump(GOOD_PLAN, open(os.path.join(self.proj, '推演', '平面图_军帐.plan.json'), 'w', encoding="utf-8"),
                  ensure_ascii=False)
        with patch.object(server, 'proj_dir', return_value=self.proj), \
             patch.object(server.H, 'spawn_job', return_value=99) as spawn:
            code, body = self.request('/api/strategy/build', 'POST', json.dumps(
                {'project': 'p', 'plan': '军帐'}, ensure_ascii=False).encode())
            self.assertEqual(code, 200, body)
            cmd = spawn.call_args.args[1]
            self.assertTrue(any('strategy_map.py' in c for c in cmd))
            self.assertIn('--plan', cmd)
            # 分镜 + plan 叠加模式：分镜照旧校验
            code, _ = self.request('/api/strategy/build', 'POST', json.dumps(
                {'project': 'p', 'plan': '不存在'}, ensure_ascii=False).encode())
            self.assertEqual(code, 400)

    def test_generate_validation(self):
        with patch.object(server, 'proj_dir', return_value=self.proj):
            code, body = self.request('/api/plan/generate', 'POST', json.dumps(
                {'project': 'p', 'name': '军帐'}).encode())
            self.assertEqual(code, 400)
            self.assertIn('scene_desc', body.decode('utf-8'))
            code, body = self.request('/api/plan/generate', 'POST', json.dumps(
                {'project': 'p', 'scene_desc': 'x'}).encode())
            self.assertEqual(code, 400)
            self.assertIn('name', body.decode('utf-8'))
            # keyframe 越出项目目录
            code, body = self.request('/api/plan/generate', 'POST', json.dumps(
                {'project': 'p', 'name': '军帐', 'keyframe': '../../etc/x.png'}).encode())
            self.assertEqual(code, 400)

    def test_generate_spawns_job(self):
        with patch.object(server, 'proj_dir', return_value=self.proj), \
             patch.object(server.H, 'spawn_job', return_value=88) as spawn:
            code, body = self.request('/api/plan/generate', 'POST', json.dumps(
                {'project': 'p', 'name': '军帐', 'scene_desc': '军帐，长案居中'},
                ensure_ascii=False).encode())
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)['id'], 88)
            cmd = spawn.call_args.args[1]
            self.assertTrue(any('gen_plan.py' in c for c in cmd))
            self.assertIn('军帐，长案居中', cmd)

    def test_generate_all_scenes_and_single_scene(self):
        with patch.object(server, 'proj_dir', return_value=self.proj), \
             patch.object(server.H, 'spawn_job', return_value=88) as spawn:
            # 全部场景批量：--all-scenes --skip-existing，不需要 name/desc
            code, body = self.request('/api/plan/generate', 'POST', json.dumps(
                {'project': 'p', 'all_scenes': True}, ensure_ascii=False).encode())
            self.assertEqual(code, 200, body)
            cmd = spawn.call_args.args[1]
            self.assertIn('--all-scenes', cmd)
            self.assertIn('--skip-existing', cmd)
            # 单场景重生成：--scene + --extra-desc，name 不必填
            code, body = self.request('/api/plan/generate', 'POST', json.dumps(
                {'project': 'p', 'scene': 'loc_tent', 'extra_desc': '加一架屏风'},
                ensure_ascii=False).encode())
            self.assertEqual(code, 200, body)
            cmd = spawn.call_args.args[1]
            self.assertIn('--scene', cmd)
            self.assertIn('loc_tent', cmd)
            self.assertIn('--extra-desc', cmd)
            self.assertIn('加一架屏风', cmd)

    def test_missing_project_400(self):
        with patch.object(server, 'proj_dir', return_value=None):
            for path, method, payload in (
                    ('/api/plan?project=p&name=x', 'GET', b''),
                    ('/api/plan/list?project=p', 'GET', b''),
                    ('/api/plan/generate', 'POST', b'{"project":"p","name":"x","scene_desc":"y"}'),
                    ('/api/plan/save', 'POST', b'{"project":"p","name":"x","plan":{}}')):
                with self.subTest(path=path):
                    code, _ = self.request(path, method, payload)
                    self.assertEqual(code, 400)


if __name__ == '__main__':
    unittest.main()
