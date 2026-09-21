# -*- coding: utf-8 -*-
"""更新服务纯逻辑回归（F06/F08/F11）：needs_build 判定、backup 分支清理、互斥锁。"""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import update_service


class NeedsBuildTests(unittest.TestCase):
    """F06：dist 不入库，前端源码/依赖清单变动必须提示重建。"""

    def test_web_src_change_needs_build(self):
        self.assertTrue(update_service._needs_build(['workbench/web/src/App.vue']))
        self.assertTrue(update_service._needs_build(['workbench/web/package.json']))
        self.assertTrue(update_service._needs_build(['workbench/web/package-lock.json']))

    def test_backend_only_change_no_build(self):
        self.assertFalse(update_service._needs_build(['workbench/server.py', 'README.md']))
        self.assertFalse(update_service._needs_build([]))
        self.assertFalse(update_service._needs_build(['workbench/web/README.md']))  # 非 src/ 不触发


class PruneBackupsTests(unittest.TestCase):
    """F08：30 天前的 backup-* 锚点分支被清理，新分支保留；解析失败跳过。"""

    def test_prune_old_backup_branches(self):
        calls = []
        now = time.time()
        old = time.strftime('backup-%Y%m%d_%H%M%S', time.localtime(now - 40 * 86400))
        new = time.strftime('backup-%Y%m%d_%H%M%S', time.localtime(now - 86400))

        def fake_git(*args, **kw):
            calls.append(args)
            if args[:2] == ('branch', '--list'):
                return f'{old}\n* {new}\nbackup-not-a-date\nmain'
            return ''

        orig = update_service._git
        update_service._git = fake_git
        try:
            update_service._prune_backups(now=now)
        finally:
            update_service._git = orig
        deleted = [a[2] for a in calls if a[:2] == ('branch', '-D')]
        self.assertEqual(deleted, [old])   # 只删 40 天前的；新分支与坏名不动


class OpLockTests(unittest.TestCase):
    """F11：互斥锁被占用时 apply/rollback 立即拒绝。"""

    def test_lock_held_rejects(self):
        update_service._OP.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, '进行中'):
                update_service.apply()
            with self.assertRaisesRegex(RuntimeError, '进行中'):
                update_service.rollback()
        finally:
            update_service._OP.release()


if __name__ == "__main__":
    unittest.main()
