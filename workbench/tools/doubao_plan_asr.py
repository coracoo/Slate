# -*- coding: utf-8 -*-
"""豆包 Agent Plan 流式 ASR，直接上传 PCM，不经过旧版 AUC/TOS。"""
import gzip
import json
import os
import struct
import sys
import uuid
import wave

PLAN_ASR_URL = 'wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_nostream'
ASR_RESOURCE_ID = 'volc.seedasr.sauc.duration'


def packet(kind, payload, *, last=False):
    flags = 2 if last else 0
    compressed = gzip.compress(payload)
    return bytes((0x11, (kind << 4) | flags, 0x11 if kind == 1 else 0x01, 0)) + struct.pack('>I', len(compressed)) + compressed


def unpack(data):
    if len(data) < 8:
        raise RuntimeError('ASR 响应包过短')
    header_len = (data[0] & 15) * 4
    kind, flags, compression = data[1] >> 4, data[1] & 15, data[2] & 15
    offset = header_len + (4 if flags & 1 else 0)
    if kind == 0xF:
        offset += 4
    if len(data) < offset + 4:
        raise RuntimeError('ASR 响应包头不完整')
    length = struct.unpack('>I', data[offset:offset+4])[0]
    body = data[offset+4:offset+4+length]
    if compression == 1:
        body = gzip.decompress(body)
    return kind, json.loads(body.decode('utf-8')) if body else {}


def result_rows(result, offset=0.0):
    body = result.get('result') or result
    utterances = body.get('utterances') or []
    rows, side, speakers = [], [], set()
    for item in utterances:
        value = (item.get('text') or '').strip().replace(' ', '')
        start, end = item.get('start_time'), item.get('end_time')
        if not value or start is None or end is None:
            continue
        speaker = item.get('speaker') or (item.get('additions') or {}).get('speaker')
        t0, t1 = offset + start / 1000, offset + end / 1000
        if rows and rows[-1][2] == value:
            continue
        rows.append((t0, t1, value))
        side.append({'t_in': round(t0,2), 't_out':round(t1,2), 'text':value, 'speaker':speaker})
        if speaker is not None:
            speakers.add(str(speaker))
    return rows, {'engine':'doubao-seed-asr-2.0-plan','speakers':sorted(speakers),'utterances':side,'text':body.get('text','')}


def transcribe_wav(wav_path, api_key, *, offset=0.0, timeout=120):
    if not api_key:
        raise ValueError('豆包 Agent Plan ASR 缺少 API Key')
    vendor_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), '_vendor')
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)
    import websocket
    with wave.open(wav_path, 'rb') as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000,1,2):
            raise ValueError('ASR 输入必须是 16kHz 单声道 16bit WAV')
        headers = [f'X-Api-Key: {api_key}', f'X-Api-Resource-Id: {ASR_RESOURCE_ID}',
                   f'X-Api-Request-Id: {uuid.uuid4()}', 'X-Api-Sequence: -1']
        ws = websocket.create_connection(PLAN_ASR_URL, header=headers, timeout=timeout)
        try:
            request = {'user':{'uid':'video-workbench'},
                       'audio':{'format':'pcm','rate':16000,'bits':16,'channel':1},
                       'request':{'model_name':'bigmodel','enable_itn':True,'enable_punc':True,
                                  'show_utterances':True,'enable_speaker_info':True}}
            ws.send_binary(packet(1, json.dumps(request,ensure_ascii=False).encode('utf-8')))
            block = 3200  # 100ms，按原始 PCM 分块
            while True:
                pcm = wav.readframes(block)
                if not pcm:
                    ws.send_binary(packet(2, b'', last=True))
                    break
                last = wav.tell() >= wav.getnframes()
                ws.send_binary(packet(2, pcm, last=last))
                if last:
                    break
            final = None
            while True:
                data = ws.recv()
                if isinstance(data,str):
                    continue
                kind, body = unpack(data)
                if kind == 0xF or body.get('code') not in (None,0,20000000):
                    raise RuntimeError('ASR 服务错误: '+str(body.get('message') or body.get('code') or body)[:250])
                if kind == 0x9:
                    final = body
                    if body.get('result') and (body.get('is_final') or body.get('result',{}).get('utterances')):
                        break
            return result_rows(final or {}, offset)
        finally:
            ws.close()
