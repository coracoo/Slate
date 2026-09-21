# -*- coding: utf-8 -*-
"""视频参数与协议回归：完全离线，不提交付费任务。"""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from video_profiles import capabilities, settings, validate_media, public_url
from llm_openai import VendorClient, VendorError


def cfg(vid,model):
    return dict(id=vid,enabled=True,base_url='https://example.test/v1',models={'video':model})


class VideoProfileTests(unittest.TestCase):
    def test_seedance_variant_resolutions(self):
        cases = {
            'doubao-seedance-2-0-260128': ['720p','480p','1080p','4k'],
            'doubao-seedance-2-0-mini': ['720p','480p'],
            'doubao-seedance-2-0-mini-260815': ['720p','480p'],
            'doubao-seedance-2.0-fast': ['720p','480p'],
            'doubao-seedance-2-5-260628': ['720p','480p','1080p'],
        }
        for vendor in ('doubao','doubao-api'):
            for model, resolutions in cases.items():
                with self.subTest(vendor=vendor, model=model):
                    c = cfg(vendor, model)
                    self.assertEqual(capabilities(c)['resolutions'], resolutions)
                    for resolution in resolutions:
                        self.assertEqual(settings(c, {'resolution':resolution})['resolution'], resolution)
                    for resolution in set(['480p','720p','1080p','4k'])-set(resolutions):
                        with self.assertRaises(ValueError): settings(c, {'resolution':resolution})
        for model in ('seedance-2.0-flash','seedance-2.5-fast','seedance-2.5-mini'):
            self.assertFalse(capabilities(cfg('doubao-api',model))['known'])

    def test_exact_variants_and_unknown(self):
        self.assertEqual(capabilities(cfg('agnes','agnes-video-2.5-flash'))['max_refs'],5)
        self.assertEqual(capabilities(cfg('agnes','agnes-video-2.5'))['max_refs'],8)
        self.assertFalse(capabilities(cfg('minimax','MiniMax-H3-Imaginary'))['known'])
        self.assertFalse(capabilities(cfg('openai-compat','MiniMax-H3'))['known'])
        self.assertEqual(capabilities(cfg('doubao-api','doubao-seedance-2-5-260628'))['max_duration'],30)
        self.assertEqual(capabilities(cfg('aliyun','wan3.0-video-prime'))['max_refs'],10)

    def test_ranges_and_mode_guard(self):
        c=cfg('agnes','agnes-video-2.5-flash')
        for options in ({'resolution':'2K'},{'duration':30},{'duration':5.5},{'seed':'1'},{'arbitrary':1},{'ratio':'adaptive'}):
            with self.assertRaises(ValueError): settings(c,options)
        p=capabilities(c)
        with self.assertRaises(ValueError): validate_media(p,'reference',list(range(6)))
        with self.assertRaises(ValueError): validate_media(p,'first_last',['a','b','c'],'a','b')
        with self.assertRaises(ValueError): validate_media(p,'text',['a'])
        with self.assertRaises(ValueError): validate_media(p,'reference',['a'],video=['v'])

    def test_minimax_frames_not_mixed_and_adaptive(self):
        c=VendorClient.from_config(cfg('minimax','MiniMax-H3'))
        with patch.object(c,'_post',return_value={'task_id':'t'}) as post, patch.object(c,'_get',return_value={'task':{'status':'succeeded','content':{'url':'https://example.test/o.mp4'}}}):
            c.generate_video('运动',image_refs=['https://example.test/a.png','https://example.test/b.png'],first_frame='https://example.test/a.png',last_frame='https://example.test/b.png',poll_interval=0)
        self.assertEqual(post.call_args.args[0],'https://example.test/v1/v2/video_generation')
        payload=post.call_args.args[1]
        self.assertEqual(payload['ratio'],'adaptive')
        self.assertEqual([x.get('role') for x in payload['content'][1:]],['first_frame','last_frame'])
        self.assertNotIn('mode',payload)

    def test_wan_maps_native_fields_and_async_header(self):
        c=VendorClient.from_config(cfg('aliyun','wan3.0-video-prime'))
        with patch.object(c,'_post',return_value={'output':{'task_id':'t'}}) as post, patch.object(c,'_get',return_value={'output':{'task_status':'SUCCEEDED','video_url':'https://example.test/o.mp4'}}):
            c.generate_video('运动',image_refs=['https://example.test/a.png'],extra={'generate_audio':False,'audio_refs':['https://example.test/a.mp3']},poll_interval=0)
        payload=post.call_args.args[1]
        self.assertFalse(payload['parameters']['audio'])
        self.assertNotIn('generate_audio',payload['parameters'])
        self.assertEqual(payload['input']['media'][1]['type'],'reference_audio')
        self.assertEqual(c._headers()['X-DashScope-Async'],'enable')

    def test_agnes_queries_video_id_and_model_not_task_id(self):
        c=VendorClient.from_config(cfg('agnes','agnes-video-2.5-flash'))
        with patch.object(c,'_post',return_value={'task_id':'wrong','video_id':'right'}) as post, patch.object(c,'_get',return_value={'status':'completed','metadata':{'url':'https://example.test/o.mp4'}}) as get:
            self.assertEqual(c.generate_video('运动',image_refs=['https://example.test/a.png'],poll_interval=0),'https://example.test/o.mp4')
        self.assertIn('video_id=right&model_name=agnes-video-2.5-flash',get.call_args.args[0])
        payload=post.call_args.args[1]
        self.assertEqual(payload['seconds'],'5'); self.assertEqual(payload['size'],'720P')
        self.assertNotIn('content',payload); self.assertNotIn('duration',payload)

    def test_agnes_rejects_local_input_before_network(self):
        c=VendorClient.from_config(cfg('agnes','agnes-video-2.5'))
        with patch.object(c,'_post') as post:
            with self.assertRaises(ValueError): c.generate_video('运动',image_refs=['J:/local.png'])
            post.assert_not_called()
        for url in ('http://localhost/x','http://192.168.0.1/x','data:image/png;base64,a'):
            with self.assertRaises(ValueError): public_url(url)

    def test_no_retry_on_missing_task_identifier(self):
        c=VendorClient.from_config(cfg('agnes','agnes-video-2.5'))
        with patch.object(c,'_post',return_value={'task_id':'not-video-id'}) as post:
            with self.assertRaises(VendorError): c.generate_video('运动',poll_interval=0)
            self.assertEqual(post.call_count,1)

    def test_text_requires_no_keyframes_and_first_last_compiles_roles(self):
        from production_requests import compile_request
        from production_studio import save_shots, read_board
        from production_media import digest
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp); (project/'分镜').mkdir()
            path=project/'分镜/剧本_E1.json'
            board={'shots':[dict(id='S1',dur=5,prompt_video='运动',prompt_image='图')], 'actors':{}}
            path.write_text(json.dumps(board),encoding='utf-8')
            body=dict(board='剧本_E1.json',scope='S',target='S1',type='video',video_options={'mode':'text'})
            packet=compile_request(project,body,cfg('minimax','MiniMax-H3'))
            self.assertEqual(packet['refs'],[])
            self.assertEqual(packet['video_options']['mode'],'text')
            image=project/'k.png'; image.write_bytes(b'frame')
            board['shots'][0]['keyframe']={'path':'k.png','sha256':digest(image)}
            path.write_text(json.dumps(board),encoding='utf-8')
            body['video_options']={'mode':'first_last'}
            packet=compile_request(project,body,cfg('minimax','MiniMax-H3'))
            self.assertEqual([r['frame_role'] for r in packet['refs']],['first_frame','last_frame'])
            self.assertEqual(packet['video_options']['ratio'],'adaptive')


if __name__=='__main__': unittest.main()
