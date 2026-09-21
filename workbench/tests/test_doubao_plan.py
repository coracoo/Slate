# -*- coding: utf-8 -*-
import importlib.util
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from io import BytesIO
from urllib.error import HTTPError

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from llm_openai import VendorClient, VendorError
from doubao_plan_tts import build_tts_request, decode_tts_response, PLAN_TTS_URL
from doubao_plan_asr import packet, unpack, result_rows, PLAN_ASR_URL


class DoubaoPlanTests(unittest.TestCase):
    def config(self):
        return {'id':'doubao','enabled':True,'base_url':'https://ark.cn-beijing.volces.com/api/plan/v3',
                'api_key':'test-placeholder','models':{'image':'doubao-seedream-5.0-pro','video':'doubao-seedance-1.5-pro'},
                'endpoints':{'image':'/images/generations','video':'/contents/generations/tasks'}}

    def test_rejects_ordinary_billing_path(self):
        cfg=self.config();cfg['base_url']='https://ark.cn-beijing.volces.com/api/v3'
        with self.assertRaises(VendorError): VendorClient.from_config(cfg)

    def test_explicit_ordinary_group_uses_ordinary_api(self):
        cfg=self.config();cfg.update(id='doubao-api',base_url='https://ark.cn-beijing.volces.com/api/v3',enabled=False)
        client=VendorClient.from_config(cfg,check_enabled=False)
        self.assertEqual(client._url('video'),cfg['base_url']+'/contents/generations/tasks')
        self.assertTrue(client._supports_image_refs('doubao-seedream-5.0-pro'))
        with patch.object(client,'_get',return_value={'data':[{'id':'test-model'}]}) as get:
            self.assertEqual(client.list_models(),['test-model'])
            get.assert_called_once_with(cfg['base_url']+'/models',15)
        with self.assertRaises(VendorError): VendorClient.from_config(cfg)

    def test_model_list_404_explains_manual_configuration(self):
        client=VendorClient.from_config(self.config())
        with patch.object(client,'_get',side_effect=VendorError('HTTP 404: Not Found')) as request:
            with self.assertRaisesRegex(VendorError,'模型列表.*手动填写'):
                client.list_models()
            request.assert_called_once_with(client.base+'/models',15)

    def test_media_plan_routes(self):
        client=VendorClient.from_config(self.config())
        self.assertEqual(client._url('image'),'https://ark.cn-beijing.volces.com/api/plan/v3/images/generations')
        self.assertEqual(client._url('video'),'https://ark.cn-beijing.volces.com/api/plan/v3/contents/generations/tasks')
        cfg=self.config();cfg['endpoints']['video']='/videos/generations'
        with self.assertRaises(VendorError): VendorClient.from_config(cfg)

    def test_full_plan_endpoint_is_normalized_without_mutating_config(self):
        cfg=self.config()
        for kind in ('image','video'):
            cfg['endpoints'][kind]=cfg['base_url']+cfg['endpoints'][kind]
        original=json.dumps(cfg)
        client=VendorClient.from_config(cfg)
        for kind in ('image','video'):
            self.assertEqual(client._url(kind),cfg['endpoints'][kind])
        self.assertEqual(json.dumps(cfg),original)
        cfg['endpoints']['video']='https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks'
        with self.assertRaises(VendorError): VendorClient.from_config(cfg)

    def test_known_incompatible_video_fails_before_upload_or_request(self):
        client=VendorClient.from_config(self.config())
        with patch.object(client, '_post') as post:
            with self.assertRaisesRegex(VendorError, 'Agent Plan.*1.5.*环境检查'):
                client.generate_video('赤鱬在山涧复游', image_refs=['missing.png'])
            post.assert_not_called()

    def test_unsupported_model_error_keeps_request_id_and_does_not_retry(self):
        client=VendorClient.from_config(self.config())
        body=json.dumps({'error':{'code':'UnsupportedModel','message':'model unavailable. Request id: request-test-123'}}).encode()
        error=HTTPError(client._url('video'),404,'Not Found',{},BytesIO(body))
        with patch('urllib.request.urlopen',side_effect=error) as send:
            with self.assertRaisesRegex(VendorError, 'UnsupportedModel.*环境检查.*request-test-123'):
                client._post(client._url('video'),{'model':'custom-video'},10)
            self.assertEqual(send.call_count,1)

    def test_tts_plan_resource_and_decode(self):
        headers,body=build_tts_request('测试','zh_female_test','test-placeholder')
        self.assertEqual(PLAN_TTS_URL,'https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional')
        self.assertEqual(headers['X-Api-Resource-Id'],'seed-tts-2.0')
        self.assertEqual(body['req_params']['text'],'测试')
        self.assertEqual(decode_tts_response(b'data: {"code":0,"data":"YWI="}\n'),b'ab')

    def test_asr_plan_protocol_and_rows(self):
        self.assertTrue(PLAN_ASR_URL.endswith('/api/v3/plan/sauc/bigmodel_nostream'))
        self.assertEqual(packet(1,b'{}')[1] >> 4,1)
        self.assertEqual(packet(2,b'abc',last=True)[1] & 2,2)
        raw=b'\x11\x90\x10\x00'+struct.pack('>I',len(b'{"result":{"text":"ok"}}'))+b'{"result":{"text":"ok"}}'
        self.assertEqual(unpack(raw)[1]['result']['text'],'ok')
        rows,_=result_rows({'result':{'utterances':[{'text':'你好','start_time':100,'end_time':500}]}},2)
        self.assertEqual(rows,[(2.1,2.5,'你好')])

if __name__=='__main__': unittest.main()
