# -*- coding: utf-8 -*-
"""豆包 Agent Plan 语音合成：仅允许 /api/v3/plan/tts/unidirectional。"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

PLAN_TTS_URL = 'https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional'
TTS_RESOURCE_ID = 'seed-tts-2.0'


def build_tts_request(text, speaker, api_key, *, audio_format='mp3'):
    if not str(text).strip() or not str(speaker).strip() or not str(api_key).strip():
        raise ValueError('TTS 需要文本、音色 speaker 和 Agent Plan API Key')
    if audio_format not in ('mp3', 'wav', 'pcm'):
        raise ValueError('TTS 格式只支持 mp3/wav/pcm')
    headers = {'Content-Type':'application/json', 'X-Api-Key':api_key,
               'X-Api-Resource-Id':TTS_RESOURCE_ID, 'X-Api-Request-Id':str(uuid.uuid4())}
    payload = {'user':{'uid':'video-workbench'},'req_params':{
        'text':text, 'speaker':speaker, 'sample_rate':24000,
        'audio_params':{'format':audio_format,'speech_rate':0,'loudness_rate':0}}}
    return headers,payload


def decode_tts_response(raw):
    """兼容 Plan HTTP 的 SSE 行或单条 JSON；拒绝空音频与错误状态。"""
    chunks=[]
    for line in raw.splitlines():
        line=line.strip()
        if line.startswith(b'data:'): line=line[5:].strip()
        if not line or not line.startswith(b'{'): continue
        try: data=json.loads(line)
        except (ValueError,UnicodeDecodeError): continue
        code=data.get('code',0)
        if str(code) not in ('0','20000000'):
            raise RuntimeError('TTS 服务错误: '+str(data.get('message') or code))
        value=data.get('data')
        if isinstance(value,str) and value:
            chunks.append(base64.b64decode(value))
        elif isinstance(value,dict) and value.get('audio'):
            chunks.append(base64.b64decode(value['audio']))
    if chunks: return b''.join(chunks)
    if raw.startswith((b'ID3',b'RIFF',b'\xff\xfb',b'\xff\xf3')): return raw
    raise RuntimeError('TTS 未返回可解析的音频')


def _bill_speech(cfg, text):
    """TTS 成功补账（kind=speech，units=字符数）；billing 缺失或异常静默。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import billing
        billing.bill(cfg, kind="speech", model=(cfg.get("models") or {}).get("speech") or TTS_RESOURCE_ID,
                     op="synthesize", ok=True, units={"chars": len(str(text or ""))})
    except Exception:
        pass


def synthesize(text, speaker, out_path, cfg, *, audio_format='mp3', timeout=120):
    if cfg.get('id') != 'doubao' or cfg.get('base_url','').rstrip('/') != 'https://ark.cn-beijing.volces.com/api/plan/v3':
        raise ValueError('TTS 只允许豆包 Agent Plan 配置')
    api_key = (cfg.get('extra') or {}).get('speech_api_key') or cfg.get('api_key') or ''
    headers,payload=build_tts_request(text,speaker,api_key,audio_format=audio_format)
    req=urllib.request.Request(PLAN_TTS_URL, data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
                               headers=headers,method='POST')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            audio=decode_tts_response(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'TTS HTTP {exc.code}: '+exc.read().decode('utf-8','replace')[:250]) from exc
    with open(out_path,'wb') as fh: fh.write(audio)
    _bill_speech(cfg, text)   # 计费补账：合成成功记 kind=speech 一条
    return out_path


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--text',required=True);ap.add_argument('--speaker',required=True)
    ap.add_argument('--out',required=True);ap.add_argument('--providers',default=os.path.join(os.path.dirname(__file__),'..','providers.json'))
    args=ap.parse_args()
    with open(args.providers,encoding='utf-8') as fh: cfg=next(v for v in json.load(fh)['vendors'] if v.get('id')=='doubao')
    ext=os.path.splitext(args.out)[1].lstrip('.').lower() or 'mp3'
    print(synthesize(args.text,args.speaker,args.out,cfg,audio_format=ext))
if __name__=='__main__':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
    main()
