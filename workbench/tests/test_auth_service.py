# -*- coding: utf-8 -*-
"""账号体系函数级回归：setup/login/限速/会话签名/撤销/改密（N85）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import auth_service


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        old = auth_service.AUTH_PATH
        self.tmp = tempfile.TemporaryDirectory()
        auth_service.AUTH_PATH = os.path.join(self.tmp.name, "auth.json")
        auth_service._failures.clear()
        self.old = old

    def tearDown(self):
        auth_service.AUTH_PATH = self.old
        self.tmp.cleanup()

    def test_setup_then_login_and_session(self):
        self.assertFalse(auth_service.configured())
        token = auth_service.setup("secret-pass")
        self.assertTrue(auth_service.configured())
        with self.assertRaises(ValueError):
            auth_service.setup("another-pass")           # 已配置拒绝二次设置
        token2 = auth_service.login("secret-pass", "127.0.0.1")
        self.assertTrue(auth_service.verify(token2))
        self.assertTrue(auth_service.verify_not_revoked(token2))
        self.assertFalse(auth_service.verify("tampered.abc"))
        self.assertFalse(auth_service.verify(token2[:-2] + "zz"))

    def test_wrong_password_locks_after_limit(self):
        auth_service.setup("secret-pass")
        for _ in range(auth_service.LOCK_LIMIT - 1):
            with self.assertRaises(ValueError):
                auth_service.login("wrong", "10.0.0.9")
        with self.assertRaises(ValueError):
            auth_service.login("wrong", "10.0.0.9")      # 第 5 次失败→锁定
        with self.assertRaisesRegex(ValueError, "过频"):
            auth_service.login("secret-pass", "10.0.0.9")  # 正确口令也被锁
        auth_service._failures.clear()
        self.assertTrue(auth_service.login("secret-pass", "10.0.0.9"))

    def test_logout_revokes_session(self):
        auth_service.setup("secret-pass")
        token = auth_service.login("secret-pass", "")
        self.assertTrue(auth_service.verify_not_revoked(token))
        auth_service.logout(token)
        self.assertFalse(auth_service.verify_not_revoked(token))

    def test_change_password(self):
        auth_service.setup("old-pass-1")
        old_token = auth_service.login("old-pass-1", "")
        auth_service.change("old-pass-1", "new-pass-2")
        with self.assertRaises(ValueError):
            auth_service.login("old-pass-1", "")
        self.assertTrue(auth_service.login("new-pass-2", ""))
        # F03：改密轮换 secret，旧会话立即失效
        self.assertFalse(auth_service.verify(old_token))
        self.assertFalse(auth_service.verify_not_revoked(old_token))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            auth_service.setup("12345")


if __name__ == "__main__":
    unittest.main()
