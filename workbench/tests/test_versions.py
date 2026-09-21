# -*- coding: utf-8 -*-
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="versions_", dir=str(ROOT)))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_content_snapshot_is_not_duplicated(self):
        import versions
        path = self.tmp / "hero.png"
        path.write_bytes(b"same")
        versions.snapshot(path)
        path.write_bytes(b"new")
        versions.snapshot(path)
        before = list((self.tmp / ".versions").glob("*"))
        versions.snapshot(path)
        after = list((self.tmp / ".versions").glob("*"))
        self.assertEqual(len(after), len(before))
        self.assertEqual(len(versions.list_versions(path)), 2)

    def test_restore_does_not_create_duplicate_current_version(self):
        import versions
        path = self.tmp / "scene.png"
        path.write_bytes(b"a")
        versions.snapshot(path)
        path.write_bytes(b"b")
        versions.snapshot(path)
        path.write_bytes(b"c")
        versions.snapshot(path)
        old = next(v for v in versions.list_versions(path)
                   if not v["current"] and Path(v["path"]).read_bytes() == b"a")
        versions.restore(path, old["ts"])
        self.assertEqual(path.read_bytes(), b"a")
        self.assertEqual(len(versions.list_versions(path)), 3)


if __name__ == "__main__":
    unittest.main()
