# -*- coding: utf-8 -*-
import os
import sys
import unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tools')))
from create_ref_mentions import select_mentioned_refs

class CreateRefMentionTests(unittest.TestCase):
    def test_only_mentioned_refs_are_forwarded_in_text_order(self):
        refs=[{'path':'a.png','ref_token':'@ref1'},{'path':'b.png','ref_token':'@ref2'}]
        self.assertEqual([r['path'] for r in select_mentioned_refs('先 @ref2 再 @ref1，@ref2',refs)],['b.png','a.png'])
    def test_unbound_mention_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'不存在'):
            select_mentioned_refs('画面 @ref9',[{'path':'a.png','ref_token':'@ref1'}])
    def test_unmentioned_refs_and_bare_path_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'输入 @'):
            select_mentioned_refs('画面',[{'path':'a.png','ref_token':'@ref1'}])
        with self.assertRaisesRegex(ValueError,'不能只传路径'):
            select_mentioned_refs('画面 @ref1',['a.png'])
if __name__=='__main__':unittest.main()
