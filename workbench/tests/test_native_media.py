# -*- coding: utf-8 -*-
"""官方协议契约测试；不调用付费接口。"""
import sys, unittest, tempfile, base64
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from llm_openai import VendorClient, VendorError
from provider_catalog import DEFAULT_VENDORS

def client(vid):
    import copy
    cfg=copy.deepcopy(next(v for v in DEFAULT_VENDORS if v['id']==vid))
    cfg.update(enabled=True,api_key='test-secret')
    if vid=='aliyun': cfg['base_url']='https://workspace.cn-beijing.maas.aliyuncs.com/api/v1'
    return VendorClient.from_config(cfg)

class NativeTests(unittest.TestCase):
    def test_minimax_h3_poll_protocol(self):
        c=client('minimax')
        with patch.object(c,'_post',return_value={'task_id':'42'}) as post, patch.object(c,'_get',return_value={'task':{'status':'succeeded','content':{'url':'https://cdn.example/out.mp4'}}}) as get:
            url=c.generate_video('移动',image_refs=['https://cdn.example/ref.png'],poll_interval=0)
        self.assertEqual(url,'https://cdn.example/out.mp4')
        self.assertTrue(post.call_args.args[0].endswith('/v2/video_generation'))
        self.assertEqual(post.call_args.args[1]['content'][1]['role'],'reference_image')
        self.assertEqual(post.call_args.args[1]['content'][1]['image_url'],{'url':'https://cdn.example/ref.png'})
        self.assertTrue(get.call_args.args[0].endswith('/v2/query/video_generation/42'))

    def test_wan_media_and_poll(self):
        c=client('aliyun')
        with patch.object(c,'_post',return_value={'output':{'task_id':'w1'}}) as post, patch.object(c,'_get',return_value={'output':{'task_status':'SUCCEEDED','video_url':'https://cdn.example/wan.mp4'}}):
            c.generate_video('图1在运动',image_refs=['https://cdn.example/r.png'],poll_interval=0)
        self.assertEqual(post.call_args.args[1]['input']['media'][0]['type'],'reference_image')
        self.assertEqual(post.call_args.args[1]['parameters']['resolution'],'720P')
        self.assertNotIn('size',post.call_args.args[1]['parameters'])
        self.assertEqual(c._headers()['X-DashScope-Async'],'enable')

    def test_speech_and_music_hex(self):
        c=client('minimax');c.cfg['extra']['voice_id']='test-voice'
        with tempfile.TemporaryDirectory() as d,patch.object(c,'_post',return_value={'base_resp':{'status_code':0},'data':{'audio':'494433'}}) as post:
            dest=Path(d)/'speech.mp3';c.generate_speech('你好',str(dest))
            self.assertEqual(dest.read_bytes(),b'ID3')
            self.assertEqual(post.call_args.args[1]['voice_setting']['voice_id'],'test-voice')
            c.generate_music('柔和背景',str(dest))
            self.assertTrue(post.call_args.args[1]['is_instrumental'])

    def test_kling_new_api_and_poll(self):
        c=client('kling')
        with patch.object(c,'_post',return_value={'code':0,'data':{'id':'k1'}}) as post, patch.object(c,'_get',return_value={'code':0,'data':[{'id':'k1','status':'succeeded','outputs':[{'type':'video','url':'https://cdn.example/k.mp4'}]}]}) as get:
            self.assertEqual(c.generate_video('向前走',image_refs=['https://cdn.example/ref.png'],poll_interval=0),'https://cdn.example/k.mp4')
        self.assertTrue(post.call_args.args[0].endswith('/image-to-video/kling-3.0-turbo'))
        self.assertEqual(post.call_args.args[1]['contents'][1]['type'],'first_frame')
        self.assertTrue(get.call_args.args[0].endswith('/tasks?task_ids=k1'))

    def test_business_error_not_success(self):
        c=client('minimax')
        with patch.object(c,'_post',return_value={'base_resp':{'status_code':1008,'status_msg':'balance'}}):
            with self.assertRaises(VendorError): c.generate_music('背景','unused.mp3')

    def test_qwen_translates_workbench_size_and_downloads_native_result(self):
        c=client('qwen')
        with patch.object(c,'_post',return_value={'output':{'choices':[{'message':{'content':[{'image':'https://cdn.example/q.png'}]}}]}}) as post, patch('urllib.request.urlretrieve') as download:
            c.generate_image('教室','out.png',extra={'size':'2k','ratio':'16:9'})
        self.assertEqual(post.call_args.args[1]['parameters']['size'],'1664*928')
        self.assertEqual(post.call_args.args[0],'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation')
        download.assert_called_once_with('https://cdn.example/q.png','out.png')

    def test_gemini_real_pixels_and_text(self):
        c=client('gemini')
        with patch.object(c,'_post',return_value={'candidates':[{'content':{'parts':[{'text':'完成'}]}}]}) as post:
            self.assertEqual(c.chat([{'role':'user','content':'你好'}]),'完成')
            self.assertIn(':generateContent',post.call_args.args[0])
        with tempfile.TemporaryDirectory() as d,patch.object(c,'_post',return_value={'steps':[{'type':'model_output','content':[{'type':'image','data':base64.b64encode(b'image').decode(),'mime_type':'image/png'}]}]}) as post:
            dest=Path(d)/'out.png';ref=Path(d)/'ref.png';ref.write_bytes(b'pixels')
            c.generate_image('照此绘制',str(dest),image_refs=[str(ref)])
            self.assertEqual(dest.read_bytes(),b'image')
            self.assertEqual(post.call_args.args[1]['input'][0]['type'],'image')

if __name__=='__main__':unittest.main()
