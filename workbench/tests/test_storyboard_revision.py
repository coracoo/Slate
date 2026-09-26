# -*- coding: utf-8 -*-
"""③ 汇总表格 ↔ ⑦ 创作生成 并发写的乐观锁回归。

背景（业务链路审计 §六.18）：⑦ 与 ⑤ 早就带 revision 回写，唯独 ③ 的整组回写
`POST /api/storyboard/save` 从不带基线——server 侧 `save_shots(..., revision)`
形参一直存在但前端永远传 None，于是 ③ 会把 ⑦ 期间改好的提示词整片盖掉。
本文件锁住三件事：① 有只读基线端点；② 回写必须带基线、过期即 409；
③ 成功回写要回新 revision，让前端连续保存不必整页重载。
"""
import json
import os as _os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

_os.environ.setdefault('SLATE_NO_AUTH', '1')   # HTTP 边界测试不做口令认证

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system' / 'tools'))
sys.path.insert(0, str(ROOT / 'workbench' / 'tools'))

import workbench.server as server                      # noqa: E402
from project_store import RevisionConflict, read_json  # noqa: E402


BOARD = {'shots': [{'id': 'S1', 'dur': 3, 'action': '甲推门进来', 'content': '甲进门'},
                   {'id': 'S2', 'dur': 4, 'action': '乙抬头', 'content': '乙抬头'}],
         'actors': {}, 'scene': {'room': {}}}


class StoreLevel(unittest.TestCase):
    def test_current_revision_matches_read_json_and_moves_on_write(self):
        import project_store
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'board.json'
            path.write_text(json.dumps(BOARD, ensure_ascii=False), encoding='utf-8')
            _, rev = read_json(str(path))
            self.assertEqual(project_store.current_revision(str(path)), rev,
                             '基线端点与写入判据必须是同一个算法，否则 409 会假阳性')
            with self.assertRaises(FileNotFoundError):
                project_store.current_revision(str(Path(tmp) / 'nope.json'))
            path.write_text(json.dumps(dict(BOARD, note='⑦ 又改了一版'), ensure_ascii=False), encoding='utf-8')
            self.assertNotEqual(project_store.current_revision(str(path)), rev,
                                '外部改过文件后旧基线必须失效，否则覆盖还是无声')

    def test_save_shots_rejects_stale_revision_and_returns_new_one(self):
        import production_studio as studio
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / 'P'
            (proj / '分镜').mkdir(parents=True)
            board = proj / '分镜' / '剧本_E1.json'
            board.write_text(json.dumps(BOARD, ensure_ascii=False), encoding='utf-8')
            _, base = studio.project_store.read_json(str(board))

            # 别的入口（⑦）先落了一次盘：③ 再拿旧基线写就必须炸，而不是悄悄盖
            studio.save_shots(str(proj), '剧本_E1.json',
                              [dict(s, prompt='⑦ 写好的提示词') for s in BOARD['shots']], None)
            _, after_other = studio.project_store.read_json(str(board))
            self.assertNotEqual(after_other, base)
            with self.assertRaises(RevisionConflict):
                studio.save_shots(str(proj), '剧本_E1.json', BOARD['shots'], base)
            self.assertIn('⑦ 写好的提示词',
                          json.loads(board.read_text(encoding='utf-8'))['shots'][0].get('prompt') or '',
                          '被拒的保存不能已经把盘改了')

            saved, new_rev = studio.save_shots(str(proj), '剧本_E1.json',
                                               [dict(s, content='改成甲坐下') for s in BOARD['shots']], after_other)
            self.assertEqual(new_rev, studio.project_store.current_revision(str(board)),
                             '回写要把新基线还给调用方，连续保存才不必整页重载')
            self.assertEqual(saved['shots'][0]['content'], '改成甲坐下')


class HttpBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def request(self, path, method='GET', body=b''):
        fields = {'Host': 'localhost', 'Connection': 'close', 'Content-Length': str(len(body)),
                  'Content-Type': 'application/json'}
        head = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n'
        with socket.create_connection(self.http.server_address, timeout=6) as sock:
            sock.sendall(head.encode() + body)
            chunks = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
        raw = b''.join(chunks)
        head_bytes, payload = raw.split(b'\r\n\r\n', 1)
        return int(head_bytes.split()[1]), payload

    def _project(self, root):
        proj = root / 'projects' / 'P' / '分镜'
        proj.mkdir(parents=True)
        (root / 'projects' / 'P' / '项目.json').write_text('{"type":"制作"}', encoding='utf-8')
        (proj / '剧本_E1.json').write_text(json.dumps(BOARD, ensure_ascii=False), encoding='utf-8')
        return root / 'projects' / 'P'

    def test_storyboard_save_uses_the_single_duration_band(self):
        """③ 表格保存原来放行 1~60s（比管线还宽），09-25 定版后与权威档 1.5~15s 对齐。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            board_file = str(root / 'projects' / 'P' / '分镜' / '剧本_E1.json')
            with patch.object(server, 'VIDEO', _os.path.realpath(str(root))):
                _, payload = self.request('/api/storyboard/revision?project=P&name=' + quote('剧本_E1.json'))
                rev = json.loads(payload)['revision']
                post = lambda dur: self.request('/api/storyboard/save', 'POST', json.dumps({
                    'project': 'P', 'name': '剧本_E1.json', 'revision': rev,
                    'shots': [{'id': 'S1', 'dur': dur, 'action': '甲推门进来', 'content': '甲进门'},
                              {'id': 'S2', 'dur': 4, 'action': '乙抬头', 'content': '乙抬头'}]}).encode())
                code, payload = post(20)
                self.assertEqual(code, 400, '超过单镜硬顶的手填值必须拒（旧口径 1~60 会放它过去）')
                self.assertIn('1.5~15', payload.decode('utf-8'), '报错要给出权威档，别让人猜')
                code, _ = post(1.2)
                self.assertEqual(code, 400, '低于权威档下界同样拒——原来 1.0 能过，比管线还松')
                code, payload = post(15)
                self.assertEqual(code, 200, payload.decode('utf-8'))

    def test_revision_endpoint_and_save_round_trip_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._project(root)
            with patch.object(server, 'VIDEO', _os.path.realpath(str(root))):
                code, payload = self.request('/api/storyboard/revision?project=P&name=' + quote('剧本_E1.json'))
                self.assertEqual(code, 200)
                base = json.loads(payload)['revision']
                import project_store
                self.assertEqual(base, project_store.current_revision(
                    str(root / 'projects' / 'P' / '分镜' / '剧本_E1.json')))

                # 越界与不存在都必须 404，且不带出真实路径
                code, _ = self.request('/api/storyboard/revision?project=P&name=..%2F..%2Fsecrets.json')
                self.assertEqual(code, 404)
                code, _ = self.request('/api/storyboard/revision?project=NOPE&name=剧本_E1.json')
                self.assertEqual(code, 404)

                shots = [{'id': s['id'], 'dur': s['dur'], 'action': s['action'],
                          'content': '③ 改的'} for s in BOARD['shots']]
                post = lambda payload: self.request('/api/storyboard/save', 'POST', json.dumps(payload).encode())
                board_file = str(root / 'projects' / 'P' / '分镜' / '剧本_E1.json')
                import production_studio as studio

                # 不带基线 = 400（③ 必须显式声明自己看的是哪一版）
                code, payload = post({'project': 'P', 'name': '剧本_E1.json', 'shots': shots})
                self.assertEqual(code, 400, '缺 revision 的整组回写必须当场拒绝，而不是默认最后写入者赢')
                self.assertIn('revision', payload.decode('utf-8'))

                # ⑦ 抢先落盘（改 prompt + 自己的 content）：③ 手上的基线作废 → 409，盘上仍是 ⑦ 的版本
                studio.save_shots(str(root / 'projects' / 'P'), '剧本_E1.json',
                                  [{'id': s['id'], 'dur': s['dur'], 'action': s['action'],
                                    'content': '⑦ 改的', 'prompt': '⑦ 的提示词'} for s in BOARD['shots']], None)
                code, _ = post({'project': 'P', 'name': '剧本_E1.json', 'shots': shots, 'revision': base})
                self.assertEqual(code, 409, '陈旧基线必须冲突（base 已被 ⑦ 的写入作废）')
                self.assertNotIn('③ 改的', Path(board_file).read_text(encoding='utf-8'),
                                 '被拒的写不得已经把盘改了')
                self.assertIn('⑦ 改的', Path(board_file).read_text(encoding='utf-8'))

                _, fresh = studio.project_store.read_json(board_file)
                code, payload = post({'project': 'P', 'name': '剧本_E1.json', 'shots': shots, 'revision': fresh})
                self.assertEqual(code, 200, payload.decode('utf-8'))
                self.assertEqual(json.loads(payload)['revision'], studio.project_store.current_revision(board_file),
                                 '回写要把新基线还给前端，连续保存才不必整页重载')
                text = Path(board_file).read_text(encoding='utf-8')
                self.assertIn('⑦ 的提示词', text, '③ 只改 content，不该抹掉 ⑦ 写的 prompt')
                self.assertIn('③ 改的', text)


if __name__ == '__main__':
    unittest.main()
