# -*- coding: utf-8 -*-
"""静态兜底收紧后的边界回归（业务审计「待你定 14」，用户 09-25 定「先盘点再收紧」）。

原来 `_static_or_not_found()` 在 WEBDIST 落空后会退回 ROOT（= workbench/ 本身），
于是登录后按 URL 就能读走 233 个 .py 源码与 157 个可反编译的 .pyc。
收紧的前提是"没人依赖它"（盘点脚本 survey_static_fallback.py 已证实），
本文件锁住两件事：**源码类路径必须 404**，**前端产物与 SPA 深链不能被打断**。
"""
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SLATE_NO_AUTH', '1')   # HTTP 边界测试不做口令认证

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system' / 'tools'))
sys.path.insert(0, str(ROOT / 'workbench' / 'tools'))

import workbench.server as server   # noqa: E402


class StaticFallbackClosed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def get(self, path, accept=None):
        fields = {'Host': 'localhost', 'Connection': 'close'}
        if accept:
            fields['Accept'] = accept
        head = f'GET {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n'
        with __import__('socket').create_connection(self.http.server_address, timeout=6) as sock:
            sock.sendall(head.encode())
            chunks = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
        raw = b''.join(chunks)
        head_bytes, payload = raw.split(b'\r\n\r\n', 1)
        return int(head_bytes.split()[1]), head_bytes, payload

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        # 假 workbench/：有源码、有 pyc、有 docs；假 WEBDIST：只有前端产物
        (root / 'tools').mkdir(parents=True)
        (root / 'tools' / '__pycache__').mkdir(parents=True)
        (root / 'server.py').write_text('SECRET_SENTINEL = "不得外发"\n', encoding='utf-8')
        (root / 'README.md').write_text('workbench 说明', encoding='utf-8')
        (root / 'tools' / '__pycache__' / 'x.cpython-312.pyc').write_bytes(b'\x00\x01fake-bytecode')
        dist = root / 'web' / 'dist'
        (dist / 'assets').mkdir(parents=True)
        (dist / 'index.html').write_text('<html>SPA</html>', encoding='utf-8')
        (dist / 'assets' / 'index-abc123.js').write_text('console.log(1)', encoding='utf-8')
        self.dist = dist
        self._p1 = patch.object(server, 'ROOT', str(root))
        self._p2 = patch.object(server, 'WEBDIST', str(dist))
        self._p1.start(); self._p2.start()
        self.addCleanup(lambda: (self._p1.stop(), self._p2.stop(), self._tmp.cleanup()))

    def test_webdist_assets_still_served(self):
        code, head, body = self.get('/assets/index-abc123.js')
        self.assertEqual(code, 200)
        self.assertIn(b'console.log', body)
        self.assertIn(b'immutable', head, '带 hash 的分片仍要长缓存，否则每次进页重下')

    def test_spa_deeplink_still_returns_index(self):
        code, _, body = self.get('/studio/shots', accept='text/html,application/xhtml+xml')
        self.assertEqual(code, 200)
        self.assertIn(b'SPA', body, '前端路由深链必须继续回落 index.html，不然刷新就白屏')

    def test_workbench_sources_are_no_longer_readable_by_url(self):
        for path in ('/server.py', '/README.md', '/tools/__pycache__/x.cpython-312.pyc',
                     '/tools/../server.py', '/assets/../../server.py', '/assets/../../../server.py'):
            with self.subTest(path=path):
                code, _, body = self.get(path)
                self.assertEqual(code, 404, f'{path} 还能被 URL 拿走，ROOT 兜底没关上')
                self.assertNotIn('不得外发', body.decode('utf-8', 'replace'))

    def test_unknown_api_path_still_404_not_index(self):
        """SPA 回落只对 HTML 请求开：不能让 /api/拼错 也拿到 index.html 而被当成 200。"""
        code, _, _ = self.get('/api/nope/nope', accept='text/html')
        self.assertEqual(code, 404)


if __name__ == '__main__':
    unittest.main()
