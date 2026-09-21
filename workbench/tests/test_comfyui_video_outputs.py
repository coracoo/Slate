# -*- coding: utf-8 -*-
"""视频真实 history 契约回归，不提交生成任务。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from comfyui_client import ComfyUIClient

class VideoOutputTests(unittest.TestCase):
    def test_save_video_under_images_and_pending_history(self):
        client = ComfyUIClient('http://localhost:8188')
        history = {'task': {'status': {'completed': True, 'status_str': 'success'},
                           'outputs': {'14': {'images': [{'filename': 'H3_00001_.mp4', 'subfolder':'VideoWorkbench','type':'output'}], 'animated':[True]}}}}
        with tempfile.TemporaryDirectory() as folder, patch.object(client, '_json', side_effect=[{'prompt_id':'task'}, {'task':{'status':{'completed':False},'outputs':{}}}, history]) as request, patch.object(client, '_bytes', return_value=b'video'):
            out=Path(folder)/'vid_1.mp4'
            client.run_video_workflow({},str(out),poll_interval=0)
            self.assertEqual(out.read_bytes(),b'video')
            self.assertEqual(sum(c.args[0]=='POST' for c in request.call_args_list),1)

if __name__=='__main__': unittest.main()
