# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
from comfyui_client import ComfyUIClient
from comfyui_h3 import build_h3_workflow
from llm_openai import VendorClient

class H3Tests(unittest.TestCase):
    def test_text_to_video_graph(self):
        g=build_h3_workflow('镜头动作', seconds=5, seed=7)
        self.assertEqual(g['5']['class_type'],'MiniMaxH3ImageToVideo')
        self.assertEqual(g['5']['inputs']['length'],124)
        self.assertEqual(g['13']['inputs']['audio'],['12',0])
        self.assertEqual(g['14']['class_type'],'SaveVideo')
    def test_references_are_not_forced_first_frame(self):
        g=build_h3_workflow('角色走动',['one.png','two.png'], seconds=5)
        self.assertEqual(g['5']['class_type'],'MiniMaxH3ReferenceToVideo')
        self.assertEqual(g['5']['inputs']['ref_images.ref_image_0'],['20',0])
        self.assertEqual(g['5']['inputs']['ref_images.ref_image_1'],['21',0])
        self.assertNotIn('first_frame',g['5']['inputs'])
    def test_video_result_recovery(self):
        c=ComfyUIClient('http://example.invalid')
        c._json=lambda method,path,payload=None,timeout=15: {'prompt_id':'p1'} if path=='/prompt' else {'p1':{'outputs':{'14':{'videos':[{'filename':'h3.mp4','subfolder':'VideoWorkbench/H3','type':'output'}]}}}}
        c._bytes=lambda path,timeout=120: b'video'
        with tempfile.TemporaryDirectory() as td:
            out=os.path.join(td,'out.mp4')
            c.run_video_workflow({},out,poll_interval=0,timeout=1)
            with open(out,'rb') as f:self.assertEqual(f.read(),b'video')
    def test_vendor_routes_to_comfyui(self):
        v=VendorClient.from_config({'id':'local-comfyui','enabled':True,'base_url':'http://example.invalid','models':{'video':'minimax_h3_fl2va_pruned_int8_convrot.safetensors'},'endpoints':{'video':'/prompt'}})
        with patch('comfyui_h3.generate_h3',return_value='out.mp4') as fn:
            self.assertEqual(v.generate_video('动作',image_refs=['a.png'],out_path='out.mp4'),'out.mp4')
            self.assertEqual(fn.call_args.args[2],['a.png'])
if __name__=='__main__':unittest.main()
