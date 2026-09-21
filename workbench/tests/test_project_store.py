# -*- coding: utf-8 -*-
"""project_store 与创作清单共享写入入口的回归测试。"""
import hashlib
import json
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "previs_system" / "tools"))
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


def _update_worker(path, key, ready):
    """独立进程并发更新同一 JSON，供 Windows spawn 使用。"""
    from project_store import update_json

    ready.wait(10)

    def mutate(doc):
        time.sleep(0.15)
        doc[key] = "done"

    update_json(path, mutate)


class ProjectStoreTests(unittest.TestCase):
    def test_revision_is_hash_of_canonical_json(self):
        from project_store import read_json

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.json"
            path.write_text('{"b": 2, "a": 1}\n', encoding="utf-8")
            data, revision = read_json(str(path))
            canonical = json.dumps(
                data, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            self.assertEqual(revision, hashlib.sha256(canonical).hexdigest())

    def test_expected_revision_conflict_does_not_overwrite_new_data(self):
        from project_store import RevisionConflict, read_json, update_json

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.json"
            path.write_text('{"note":"旧"}', encoding="utf-8")
            _, revision = read_json(str(path))
            update_json(str(path), lambda doc: doc.update(note="新"),
                        expected_revision=revision)
            with self.assertRaises(RevisionConflict):
                update_json(str(path), lambda doc: doc.update(note="过期"),
                            expected_revision=revision)
            self.assertEqual(read_json(str(path))[0]["note"], "新")

    def test_two_process_updates_are_serialized_and_both_survive(self):
        from project_store import read_json

        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "a.json")
            Path(path).write_text('{"a":"pending","b":"pending"}',
                                  encoding="utf-8")
            ctx = multiprocessing.get_context("spawn")
            ready = ctx.Event()
            workers = [ctx.Process(target=_update_worker,
                                    args=(path, key, ready))
                       for key in ("a", "b")]
            for worker in workers:
                worker.start()
            ready.set()
            for worker in workers:
                worker.join(15)
                self.assertFalse(worker.is_alive(), "并发测试进程未退出")
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(read_json(path)[0], {"a": "done", "b": "done"})

    def test_atomic_replace_uses_same_directory_and_leaves_no_temp_file(self):
        import project_store

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.json"
            path.write_text('{"state":"old"}', encoding="utf-8")
            calls = []
            replace = project_store.os.replace

            def observe(source, target):
                calls.append((source, target))
                replace(source, target)

            with mock.patch.object(project_store.os, "replace", observe):
                project_store.update_json(str(path),
                                          lambda doc: doc.update(state="new"))
            self.assertEqual(len(calls), 1)
            source, target = calls[0]
            self.assertEqual(os.path.dirname(source), os.path.dirname(target))
            self.assertEqual(os.path.abspath(target), os.path.abspath(path))
            self.assertEqual(list(Path(td).glob("*.tmp")), [])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"state": "new"})

    def test_replace_failure_preserves_original_bytes(self):
        import project_store

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.json"
            original = b'{"state":"old"}\n'
            path.write_bytes(original)
            with mock.patch.object(project_store.os, "replace",
                                   side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    project_store.update_json(str(path),
                                              lambda doc: doc.update(state="new"))
            self.assertEqual(path.read_bytes(), original)

    def test_snapshot_callback_sees_old_bytes_before_replace(self):
        import project_store

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.json"
            original = b'{"state":"old"}\n'
            path.write_bytes(original)
            seen = []

            def snapshot(old_path):
                seen.append(Path(old_path).read_bytes())

            project_store.update_json(str(path),
                                      lambda doc: doc.update(state="new"),
                                      snapshot=snapshot)
            self.assertEqual(seen, [original])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"state": "new"})

    def test_corrupt_json_raises_and_preserves_original_bytes(self):
        from project_store import InvalidDocument, read_json, update_json

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.json"
            original = b'{"state":'
            path.write_bytes(original)
            with self.assertRaises(InvalidDocument):
                read_json(str(path))
            with self.assertRaises(InvalidDocument):
                update_json(str(path), lambda doc: doc.update(state="new"))
            self.assertEqual(path.read_bytes(), original)

    def test_missing_file_requires_create_default(self):
        from project_store import update_json

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "a.json"
            with self.assertRaises(FileNotFoundError):
                update_json(str(path), lambda doc: doc.update(state="new"))
            data, _ = update_json(
                str(path), lambda doc: doc.update(state="new"),
                create_default={"items": []},
            )
            self.assertEqual(data, {"items": [], "state": "new"})

    def test_create_media_update_item_delegates_to_shared_store(self):
        import create_media
        import project_store

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "creation.json"
            path.write_text(json.dumps({"items": [{"id": "x", "status": "running"}]}),
                            encoding="utf-8")
            with mock.patch.object(create_media.project_store, "update_json",
                                   wraps=project_store.update_json) as update:
                self.assertTrue(create_media.update_item(str(path), "x",
                                                          status="done"))
            self.assertTrue(update.called)
            self.assertEqual(project_store.read_json(str(path))[0]["items"][0]["status"],
                             "done")
            original = path.read_bytes()
            self.assertFalse(create_media.update_item(str(path), "missing", status="done"))
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
