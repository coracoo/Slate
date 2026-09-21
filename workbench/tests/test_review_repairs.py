# -*- coding: utf-8 -*-
"""审计修复回归：副本一致、在场过滤、生成器覆写保留历史。"""
import json
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'workbench/tools'))
from shot_presence import actor_positions


class ReviewRepairTests(unittest.TestCase):
    def test_published_scripts_match_source(self):
        for name in ('blender_previs.py','lapian_docs.py','extract_shots.py'):
            self.assertEqual((ROOT/'previs_system/tools'/name).read_bytes(),(ROOT/'.codex/skills/video-previs/scripts'/name).read_bytes(),name)

    def test_presence_offstage_spawn_names_and_narrator(self):
        actors={'a':{'name':'甲','pos':[0,0]},'b':{'name':'乙','pos':[1,1]},'narrator':{'pos':[0,0]}}
        self.assertEqual(actor_positions({'action':'甲转头'},actors),{'a':[0,0]})
        self.assertEqual(actor_positions({'staging':{'a':[25,25],'b':[2,3]}},actors),{'b':[2,3]})
        self.assertEqual(actor_positions({},actors,{'layout':{'spawn':{'@character:乙':[4,5]}}}),{'b':[4,5]})

    def test_strategy_overwrite_preserves_previous_html(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'分镜').mkdir(); board=root/'分镜'/'E1.json'; output=root/'map.html'
            board.write_text(json.dumps({'shots':[{'id':'S1','dur':3}], 'actors':{}}),encoding='utf-8')
            output.write_text('previous',encoding='utf-8')
            subprocess.run([sys.executable,str(ROOT/'workbench/tools/strategy_map.py'),str(board),'--out',str(output)],check=True,capture_output=True)
            copies=list((root/'.versions').glob('*.html'))
            self.assertEqual(len(copies),1)
            self.assertEqual(copies[0].read_text(encoding='utf-8'),'previous')

    def test_env_overwrite_snapshots_before_writing(self):
        import gen_scene_env as module
        with tempfile.TemporaryDirectory() as folder:
            board=Path(folder)/'board.json'
            original=json.dumps({'shots':[{'action':'房间里有桌子'}]})
            board.write_text(original,encoding='utf-8')
            client=MagicMock(); client.chat.return_value=json.dumps({'room':{'boxes':[[0,0,0,1,1,1]]}})
            with patch.object(sys,'argv',['gen_scene_env',str(board)]), patch.object(module,'pick_vendor',return_value='test'), patch.object(module,'VendorClient',return_value=client):
                module.main()
            self.assertIn('env',json.loads(board.read_text(encoding='utf-8')))
            self.assertEqual(next((Path(folder)/'.versions').glob('*.json')).read_text(encoding='utf-8'),original)

    def test_prepare_job_marks_invalid_candidate_failed(self):
        import acting_prepare_job as module
        with tempfile.TemporaryDirectory() as folder:
            request=Path(folder)/'request.json'; request.write_text(json.dumps({'board_path':'board','revision':'r'}))
            with patch.object(module,'_read_board',return_value=({},'r')), patch.object(module,'prepare_context',return_value={'path':'candidate','status':'invalid','errors':[{'message':'Bearer secret.token'}]}):
                with self.assertRaisesRegex(ValueError,'演员准备失败') as error: module.execute(request)
                self.assertNotIn('secret.token',str(error.exception))

    def test_analysis_bridge_preserves_existing_board(self):
        import analysis_to_storyboard as module
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'拉片'/'v1').mkdir(parents=True); (root/'分镜').mkdir()
            (root/'拉片'/'v1'/'analysis.json').write_text(json.dumps({'shots':[{'id':'S1'}]}))
            output=root/'分镜'/'v1.json'; output.write_text('{"old":true}')
            with patch.object(sys,'argv',['analysis_to_storyboard',folder,'--analysis','v1']), patch.object(module,'load_lines',return_value=[{'speaker':'a'}]), patch.object(module,'build_actors',return_value=({'a':{'name':'甲'}},{})), patch.object(module,'convert',return_value=[{'id':'S1'}]):
                module.main()
            self.assertEqual(next((root/'分镜'/'.versions').glob('*.json')).read_text(),'{"old":true}')


if __name__=='__main__': unittest.main()
