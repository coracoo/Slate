# -*- coding: utf-8 -*-
"""串行、导入归属和中断不重绘验证；网页执行由替身代替。"""
import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_chatgpt_runs import ChatGPTRunTests, _png
import chatgpt_runs as runs
import chatgpt_queue
import image_use_runner as runner

class RunnerTests(ChatGPTRunTests):
    def test_two_jobs_individual_prompts_and_precise_import(self):
        second=self._second_job()
        created=runs.create_run(self.project,[self.first['id'],second['id']])
        seen=[]
        def generate(prompt,output,**kw):
            # 下一次调用必须在上一张完成导入之后。
            seen.append(prompt)
            state=runs.get_run(self.project,created['run_id'])
            self.assertEqual(state['cursor'],len(seen)-1)
            Path(output).write_bytes(_png(color=(len(seen)*60,20,40)))
        with patch.object(runner.runtime,'generate_image',side_effect=generate):
            runner._execute(self.project,created['run_id'],created['run_token'])
        state=runs.get_run(self.project,created['run_id'])
        self.assertEqual(state['status'],'done')
        self.assertEqual(state['counts']['imported'],2)
        self.assertEqual(len(seen),2)
        self.assertNotEqual(seen[0],seen[1])
        index=json.loads(Path(self.project,'素材','素材图.json').read_text(encoding='utf-8'))
        self.assertIn('hero',str(index)); self.assertIn('friend',str(index))

    def test_ambiguous_error_and_resume_never_regenerates(self):
        created=runs.create_run(self.project,[self.first['id']])
        with patch.object(runner.runtime,'generate_image',side_effect=RuntimeError('发送结果未知')) as generate:
            runner._execute(self.project,created['run_id'],created['run_token'])
            self.assertEqual(runs.get_run(self.project,created['run_id'])['status'],'paused')
            runs.set_run_control(self.project,created['run_id'],created['run_token'],'resume')
            runner._execute(self.project,created['run_id'],created['run_token'])
            self.assertEqual(generate.call_count,1)
        self.assertEqual(runs.get_run(self.project,created['run_id'])['counts']['imported'],0)

    def test_downloaded_output_recovers_without_calling_generator(self):
        created=runs.create_run(self.project,[self.first['id']])
        attempt=runs.claim_next(self.project,created['run_id'],created['run_token'])
        self._advance_to_generating(created,attempt)
        folder=Path(runs._run_dir(self.project,created['run_id']))/'executor'/attempt['attempt_id']
        folder.mkdir(parents=True);(folder/'result.png').write_bytes(_png())
        with patch.object(runner.runtime,'generate_image') as generate:
            runner._execute(self.project,created['run_id'],created['run_token'])
            generate.assert_not_called()
        self.assertEqual(runs.get_run(self.project,created['run_id'])['status'],'done')

    def test_duplicate_output_stops_before_second_import(self):
        created=runs.create_run(self.project,[self.first['id'],self._second_job()['id']])
        def generate(prompt,output,**kw):Path(output).write_bytes(_png())
        with patch.object(runner.runtime,'generate_image',side_effect=generate):
            runner._execute(self.project,created['run_id'],created['run_token'])
        state=runs.get_run(self.project,created['run_id'])
        self.assertEqual(state['status'],'needs_review')
        self.assertEqual(state['counts']['imported'],1)
