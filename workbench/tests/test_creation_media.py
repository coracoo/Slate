# -*- coding: utf-8 -*-
"""媒体选择与归档契约，使用临时文件，不调用付费生成。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'previs_system' / 'tools'))
from creation_media import image_route, register_output, speech_binding


class CreationMediaTests(unittest.TestCase):
    def test_worker_uses_explicit_model_and_archives_speech(self):
        import create_media
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); outdir=root/'创作'/'job1'; outdir.mkdir(parents=True)
            manifest=root/'创作'/'creation.json'
            manifest.write_text(json.dumps({'items':[{'id':'job1','type':'speech','character_id':'hero','voice_id':'voice-1'}]}),encoding='utf-8')
            client=MagicMock()
            client.generate_speech.side_effect=lambda text,out,**kwargs: Path(out).write_bytes(b'fake audio for local test')
            argv=['create_media','--type','speech','--model','speech-2.8-hd','--voice-id','voice-1','--prompt','你好', '--vendor','minimax','--outdir',str(outdir),'--manifest',str(manifest),'--item-id','job1']
            with patch.object(sys,'argv',argv), patch.object(create_media.llm_openai,'VendorClient',return_value=client):
                create_media.main()
            client.model.assert_not_called()
            self.assertEqual(client.generate_speech.call_args.kwargs['model'],'speech-2.8-hd')
            result=json.loads(manifest.read_text(encoding='utf-8'))['items'][0]
            self.assertEqual(result['status'],'done')
            self.assertEqual(result['material_path'],'素材/语音/hero/job1.mp3')

    def test_image_route_uses_references_and_selected_model(self):
        cfg = {'models': {'image': 't2i', 'image_edit': 'i2i'}}
        self.assertEqual(image_route(cfg, False), ('image', 'generate', 't2i'))
        self.assertEqual(image_route(cfg, True), ('image_edit', 'edit', 'i2i'))
        self.assertEqual(image_route(cfg, True, 'image'), ('image', 'generate', 't2i'))
        self.assertEqual(image_route({'models': {'image': 'seedream'}}, True)[2], 'seedream')
        with self.assertRaises(ValueError): image_route(cfg, False, 'image_edit')
        with self.assertRaises(ValueError): image_route(cfg, True, 'video')

    def test_archive_is_idempotent_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); out = root/'创作'/'job1'/'vid_1.mp4'
            out.parent.mkdir(parents=True); out.write_bytes(b'video')
            item = {'id': 'job1', 'type': 'video', 'board': '剧本_E1.json', 'shot_id': 'S1'}
            first = register_output(root, item, out)
            self.assertEqual(first, register_output(root, item, out))
            self.assertEqual(first['source_video'], '拉片素材/创作视频/job1.mp4')
            self.assertTrue(out.exists())
            data = json.loads((root/'素材'/'生成媒体.json').read_text(encoding='utf-8'))
            self.assertEqual(len(data['items']), 1)
            self.assertEqual(data['items']['job1']['shot_id'], 'S1')

    def test_audio_uses_distinct_folders_and_actor_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'素材').mkdir()
            (root/'素材'/'人物.json').write_text(json.dumps({'characters':[{'id':'hero','name':'主角'}]}),encoding='utf-8')
            binding = speech_binding(root, 'hero', 'voice-123', 'minimax')
            self.assertEqual(binding['character_name'], '主角')
            with self.assertRaises(ValueError): speech_binding(root, 'missing', 'voice', 'minimax')
            with self.assertRaises(ValueError): speech_binding(root, 'hero', '', 'minimax')
            out = root/'audio.mp3'; out.write_bytes(b'audio')
            self.assertEqual(register_output(root, {'id':'speech1','type':'speech',**binding}, out)['material_path'], '素材/语音/hero/speech1.mp3')
            self.assertEqual(register_output(root, {'id':'music1','type':'music'}, out)['material_path'], '素材/音乐/music1.mp3')
            with self.assertRaises(ValueError): register_output(root, {'id':'../bad','type':'music'},out)
