# -*- coding: utf-8 -*-
"""首次分析、补填与断点保存的完成口径；全部输入为临时目录及离线响应。"""
import json
import sys
import tempfile
import unittest
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system/tools'))
sys.path.insert(0, str(ROOT / 'workbench/tools'))
import analyze_film as film
import project_store


def response():
    return {'shot_size': '全景', 'camera_move': '固定', 'angle': '平视', 'transition': '硬切',
            'lighting': '日光', 'action': '走路', 'story': '角色到达', 'prompt_cn': '全景，角色走来', 'dialogue': []}


class AnalysisCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.video = self.root / 'source.mp4'
        self.video.write_bytes(b'offline')
        self.directory = self.root / '拉片/test'
        self.segs = [(0.0, 2.0), (2.0, 4.0)]
        self.client = SimpleNamespace(id='offline', models={'vision': 'offline'})
        for target in ('socket.create_connection', 'socket.socket.connect'):
            guard = patch(target, side_effect=AssertionError('禁止网络'))
            guard.start()
            self.addCleanup(guard.stop)

    def frames(self, _video, segs, directory, _ff, _transition):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        frames = []
        for index in range(len(segs)):
            filename = f'S{index + 1}.jpg'
            (directory / filename).write_bytes(b'image')
            frames.append([filename])
        return frames

    def main(self, *, answers=None, available=True, extra=(), extract=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(sys, 'argv', ['analyze_film.py', str(self.video), '--out', str(self.directory), '--workers', '1', *extra]))
        stack.enter_context(patch.object(film, 'find_ffmpeg', return_value='offline-ffmpeg'))
        stack.enter_context(patch.object(film, 'detect_shots', return_value=(self.segs, 'extract_shots')))
        stack.enter_context(patch.object(film, 'extract_keyframes', side_effect=extract or self.frames))
        engine = (self.client, None, 'offline:offline', 'offline') if available else (None, None, 'none', '未配置凭据')
        stack.enter_context(patch.object(film, 'pick_engine', return_value=engine))
        stack.enter_context(patch.object(film, 'self_validate', return_value=([], [])))
        stack.enter_context(patch.object(film, 'write_markdown'))
        model = stack.enter_context(patch.object(film, 'ai_with_retry', side_effect=answers or [response(), response()]))
        return model

    def saved(self):
        return json.loads((self.directory / 'analysis.json').read_text(encoding='utf-8'))

    def partial(self, *, source=None, first_complete=True):
        self.directory.mkdir(parents=True, exist_ok=True)
        shots = []
        for index, (start, end) in enumerate(self.segs):
            shot = {'id': f'S{index + 1}', 't_in': start, 't_out': end, 'duration': end - start,
                    'keyframes': [f'S{index + 1}.jpg'], 'dialogue': [], 'story': '已保留剧情'}
            if index == 0 and first_complete: shot.update(response())
            shots.append(shot)
        analysis = {'name': 'test', 'version': 3, 'source': str(source or self.video),
                    'source_identity': film.source_identity(self.video),
                    'cut_detection': film.build_cut_detection('extract_shots', 13.0, 2.0, 0), 'shots': shots}
        (self.directory / 'analysis.json').write_text(json.dumps(analysis, ensure_ascii=False), encoding='utf-8')
        (self.directory / '_partial.flag').write_text('partial', encoding='utf-8')
        return analysis

    def test_first_partial_response_keeps_valid_fields_and_never_registers_done(self):
        model = self.main(answers=[{'story': '部分剧情'}, response()])
        with self.assertRaises(SystemExit) as exc: film.main()
        self.assertEqual(exc.exception.code, 1)
        saved = self.saved()
        self.assertEqual(saved['ai']['status'], 'partial')
        self.assertEqual(saved['shots'][0]['story'], '部分剧情')
        self.assertIn('S1', saved['ai']['missing_fields'])
        self.assertEqual(saved['shots'][1]['prompt_cn'], response()['prompt_cn'])
        self.assertTrue((self.directory / '_partial.flag').is_file())
        self.assertFalse((self.directory.parent / '_versions.json').exists())
        self.assertEqual(model.call_count, 2)

    def test_missing_transition_is_not_replaced_by_a_completion_default(self):
        answer = response()
        answer.pop('transition')
        self.main(answers=[answer, response()])
        with self.assertRaises(SystemExit): film.main()
        self.assertEqual(self.saved()['shots'][0]['transition'], '')
        self.assertIn('transition', self.saved()['ai']['missing_fields']['S1'])

    def test_target_without_keyframes_fails_instead_of_being_skipped(self):
        model = self.main(extract=lambda *_: [[], []])
        with self.assertRaises(SystemExit): film.main()
        self.assertEqual(model.call_count, 0)
        self.assertEqual(set(self.saved()['ai']['failures']), {'S1', 'S2'})
        self.assertFalse((self.directory.parent / '_versions.json').exists())

    def test_max_ai_counts_reused_shots_inside_first_n_scope(self):
        self.partial()
        model = self.main(extra=('--max-ai', '1'))
        film.main()
        saved = self.saved()
        self.assertEqual(model.call_count, 0)
        self.assertEqual(saved['ai']['status'], 'complete')
        self.assertEqual(saved['ai']['scope'], 'limited')
        self.assertEqual(saved['ai']['target_ids'], ['S1'])
        self.assertTrue(film.missing_ai_fields(saved['shots'][1]))
        self.assertFalse((self.directory / '_partial.flag').exists())

    def test_incomplete_checkpoint_shot_is_retried_and_keeps_existing_valid_fields(self):
        self.partial()
        answer = response()
        answer.pop('story')
        model = self.main(answers=[answer])
        with self.assertRaises(SystemExit): film.main()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.saved()['shots'][1]['story'], '已保留剧情')
        retried = self.main(answers=[response()])
        film.main()
        self.assertEqual(retried.call_count, 1)
        self.assertEqual(self.saved()['ai']['completed_ids'], ['S1', 'S2'])

    def test_reanalysis_empty_response_cannot_use_old_complete_fields_to_report_success(self):
        shot = {'id': 'S1', 't_in': 0, 't_out': 2, **response()}
        failures = {}
        count = film.run_ai_targets([shot], lambda _shot: {}, lambda: None, failures)
        self.assertEqual(count, 0)
        self.assertIn('S1', failures)
        self.assertEqual(shot['story'], response()['story'])

    def test_other_source_cannot_reuse_identical_cut_boundaries(self):
        self.partial(source=self.root / 'other.mp4')
        model = self.main()
        film.main()
        self.assertEqual(model.call_count, 2)

    def test_same_path_video_replacement_and_changed_cut_parameters_invalidate_reuse(self):
        previous = self.partial()
        identity = film.source_identity(self.video)
        self.assertEqual(len(film.matching_checkpoint_shots(previous, str(self.video), self.segs, previous['cut_detection'], identity)), 2)
        for changed in ({**identity, 'mtime_ns': identity['mtime_ns'] + 1}, {**identity, 'size': identity['size'] + 1}):
            self.assertEqual(film.matching_checkpoint_shots(previous, str(self.video), self.segs, previous['cut_detection'], changed), {})
        self.assertEqual(film.matching_checkpoint_shots(previous, str(self.video), self.segs,
                         {**previous['cut_detection'], 'thresh': 15.0}, identity), {})

    def test_no_credentials_and_no_ai_complete_structure_with_explicit_skipped_state(self):
        for available, extra in ((False, ()), (True, ('--no-ai',))):
            with self.subTest(available=available):
                self.directory = self.root / f'拉片/skip-{available}'
                model = self.main(available=available, extra=extra)
                film.main()
                saved = self.saved()
                self.assertEqual(model.call_count, 0)
                self.assertEqual(saved['ai']['status'], 'skipped')
                self.assertTrue(saved['ai']['skip_reason'])
                self.assertTrue(film.missing_ai_fields(saved['shots'][0]))
                self.assertFalse((self.directory / '_partial.flag').exists())

    def test_checkpoint_failure_stops_before_any_model_call(self):
        model = self.main()
        with patch.object(project_store, 'update_json', side_effect=OSError('磁盘已满')):
            with self.assertRaisesRegex(OSError, '磁盘已满'): film.main()
        self.assertEqual(model.call_count, 0)

    def test_checkpoint_failure_after_first_result_preserves_json_and_stops_next_shot(self):
        model = self.main()
        original = project_store.update_json
        calls = 0
        def write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('磁盘已满')
            return original(*args, **kwargs)
        with patch.object(project_store, 'update_json', side_effect=write):
            with self.assertRaisesRegex(OSError, '磁盘已满'): film.main()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(self.saved()['shots']), 2)
        self.assertEqual(self.saved()['ai']['status'], 'running')
        self.assertTrue((self.directory / '_partial.flag').is_file())

    def test_parallel_checkpoint_failure_cancels_pending_and_does_not_schedule_remaining(self):
        submitted, cancelled, snapshots = [], [], []
        class Executor:
            def __init__(self, **_kwargs): pass
            def submit(self, callback, shot):
                future = Future()
                submitted.append((shot, future))
                if len(submitted) == 1: future.set_result(callback(shot))
                return future
            def shutdown(self, **kwargs): cancelled.append(kwargs)
        shots = [{'id': f'S{index}', 't_in': 0, 't_out': 2} for index in range(4)]
        def checkpoint():
            snapshots.append(1)
            if len(snapshots) == 2: raise OSError('断点失败')
        with patch.object(film, 'ThreadPoolExecutor', Executor):
            with self.assertRaisesRegex(OSError, '断点失败'):
                film.run_ai_targets(shots, lambda _shot: response(), checkpoint, {}, workers=2, parallel=True)
        self.assertEqual(len(submitted), 2)
        self.assertTrue(submitted[1][1].cancelled())
        self.assertEqual(cancelled, [{'wait': True, 'cancel_futures': True}])

    def test_external_edit_rejects_checkpoint_and_preserves_other_writer(self):
        model = self.main()
        def result(*_args):
            project_store.update_json(self.directory / 'analysis.json', lambda data: data.update(note='他人修改'))
            return response()
        model.side_effect = result
        with self.assertRaises(project_store.RevisionConflict): film.main()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(self.saved()['note'], '他人修改')

    def test_fill_missing_fields_fails_with_partial_and_preserves_explicit_silence(self):
        self.partial(first_complete=False)
        self.frames(None, self.segs, self.directory, None, None)
        args = SimpleNamespace(fill=str(self.directory), vendor=None, shots='S1', only_empty=True,
                               max_ai=0, transition_frames=False, workers=1)
        with patch.object(film, 'pick_engine', return_value=(self.client, None, 'offline', 'offline')), \
             patch.object(film, 'ai_with_retry', return_value={'action': '走路', 'dialogue': [{'text': '不要添加'}]}), \
             patch.object(film, 'self_validate', return_value=([], [])), patch.object(film, 'write_markdown'):
            with self.assertRaises(SystemExit): film.fill_mode(args)
        saved = self.saved()
        self.assertEqual(saved['ai']['status'], 'partial')
        self.assertEqual(saved['ai']['target_ids'], ['S1'])
        self.assertEqual(saved['shots'][0]['story'], '已保留剧情')
        self.assertEqual(saved['shots'][0]['dialogue'], [])
        self.assertTrue((self.directory / '_partial.flag').is_file())

    def test_fill_without_credentials_records_skipped_but_does_not_report_success(self):
        before = self.partial()
        args = SimpleNamespace(fill=str(self.directory), vendor=None, shots='S1', only_empty=False,
                               max_ai=0, transition_frames=False, workers=1)
        with patch.object(film, 'pick_engine', return_value=(None, None, 'none', '无凭据')):
            with self.assertRaises(SystemExit): film.fill_mode(args)
        saved = self.saved()
        self.assertEqual(saved['shots'], before['shots'])
        self.assertEqual(saved['ai']['status'], 'skipped')
        self.assertEqual(saved['ai']['requested_ids'], ['S1'])
        self.assertTrue((self.directory / '_partial.flag').exists())

    def test_corrupt_version_registry_cannot_be_replaced_by_empty_list(self):
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        registry = self.directory.parent / '_versions.json'
        registry.write_text('损坏清单', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            film.register_version(str(self.directory.parent), 'test', {'status': 'done'})
        self.assertEqual(registry.read_text(encoding='utf-8'), '损坏清单')


if __name__ == '__main__':
    unittest.main()
