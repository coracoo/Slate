# -*- coding: utf-8 -*-
"""旁白（narrator）不是角色的回归测试：
- validate_dialogue：narrator 作 speaker 放行；narrator 进 actors 报错
- creation_pipeline：is_narrator 别名归一
- dialogue_engine：narrator 显示名为【旁白】
"""
import os
import sys
import unittest
import tempfile
import json
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "workbench", "tools"))
sys.path.insert(0, os.path.join(ROOT, "previs_system", "tools"))

from validate_dialogue import validate_document, RESERVED_SPEAKERS
from creation_pipeline import is_narrator


def _board(speaker="narrator", actors=None):
    return {"actors": actors if actors is not None else {"a": {"name": "甲"}},
            "shots": [{"id": "S1", "dur": 4, "cam": "wide", "speaker": speaker,
                       "pos": [0, -3, 1.6], "look": [0, 0, 1],
                       "lines": [{"at": 0.5, "dur": 2.0, "speaker": speaker, "line": "旁白内容"}]}]}


class NarratorContractTests(unittest.TestCase):
    def test_prompt_keeps_narration_only_in_audio(self):
        from shot_prompt import build_shot_prompt, render_prompt_text
        shot = {'id':'S1', 'action':'赤鱬在山涧复游',
                'actor_refs':['@character:chiru','@character:narrator','旁白者'],
                'actors':[{'asset':'@character:chiru','target':'@character:narrator'}],
                'lines':[{'speaker':'narrator','line':'仙山恢复平静'}]}
        value = build_shot_prompt(shot, media_type='video')
        self.assertEqual([a['asset'] for a in value['actors']], ['@character:chiru'])
        self.assertNotIn('target', value['actors'][0])
        self.assertNotIn('@character:narrator', value['asset_refs'])
        self.assertEqual(value['dialogue'][0]['speaker'], 'narrator')
        text = render_prompt_text(value)
        self.assertIn('仙山恢复平静', text)
        self.assertNotIn('@character:narrator', text)

    def test_legacy_narrator_asset_never_becomes_reference_image(self):
        from prompt_assembler import assemble_shot_prompt, resolve_shot_refs
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            assets = project / '素材'
            (assets / '人物').mkdir(parents=True)
            records = [{'id':'chiru','name':'赤鱬'}, {'id':'narrator','name':'旁白'}]
            (assets / '人物.json').write_text(json.dumps({'characters':records}),encoding='utf-8')
            for record in records:
                (assets / '人物' / (record['id']+'.png')).write_bytes(b'legacy fixture')
            shot = {'id':'S1','action':'赤鱬在山涧复游','actor_refs':['@character:chiru','@character:narrator'],
                    'lines':[{'speaker':'narrator','line':'仙山恢复平静'}],
                    'prompt_json':{'asset_refs':['@character:narrator']}}
            board = {'actors':{r['id']:r for r in records}}
            out = assemble_shot_prompt(shot, {'dir':folder, 'board':board, 'media_type':'video'})
            self.assertNotIn('@character:narrator',out['asset_refs'])
            self.assertNotIn('@character:narrator',out['prompt_assembled'])
            self.assertTrue(all(c['id']!='narrator' for c in out['asset_context']['characters']))
            refs = resolve_shot_refs(shot,folder,actors=board['actors'],media_type='video')
            self.assertTrue(refs)
            self.assertTrue(all('narrator' not in r['path'] for r in refs))

    def test_legacy_named_narrator_and_baked_prompt_are_excluded(self):
        from prompt_assembler import assemble_shot_prompt, resolve_shot_refs
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'素材'/'人物'
            path.mkdir(parents=True)
            (path/'voiceover.png').write_bytes(b'fixture')
            (path.parent/'人物.json').write_text(json.dumps({'characters':[{'id':'voiceover','name':'旁白'}]}),encoding='utf-8')
            board={'actors':{'voiceover':{'name':'旁白'}}}
            shot={'id':'S1','action':'群山空镜','prompt':'群山空镜。资产引用：@character:narrator',
                  'actor_refs':['voiceover'],'speaker':'voiceover',
                  'prompt_json':{'asset_refs':['@character:voiceover']}}
            out=assemble_shot_prompt(shot,{'dir':folder,'board':board})
            self.assertEqual(out['prompt_json']['actors'],[])
            self.assertNotIn('@character:narrator',out['prompt_assembled'])
            self.assertEqual(resolve_shot_refs(shot,folder,actors=board['actors']),[])

    def test_narrator_speaker_passes_validation(self):
        r = validate_document(_board())
        self.assertEqual(r["errors"], [])

    def test_narrator_must_not_be_an_actor(self):
        r = validate_document(_board(actors={"narrator": {"name": "旁白"}}))
        self.assertTrue(any(e["code"] == "RESERVED_ACTOR" for e in r["errors"]))

    def test_unknown_speaker_still_rejected(self):
        r = validate_document(_board(speaker="ghost"))
        self.assertTrue(any(e["code"] == "ACTOR_REF" for e in r["errors"]))

    def test_alias_normalization(self):
        for alias in ("旁白", "旁白者", "叙述者", "画外音", "解说", "narrator", "Narrator", "VO"):
            self.assertTrue(is_narrator(alias), alias)
        for real in ("jiuweihu", "青妩", "黄帝", ""):
            self.assertFalse(is_narrator(real), real)

    def test_reserved_set_is_narrator_only(self):
        self.assertEqual(RESERVED_SPEAKERS, {"narrator"})


if __name__ == "__main__":
    unittest.main()
