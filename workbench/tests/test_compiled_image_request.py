# -*- coding: utf-8 -*-
import os
import sys
import unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from llm_openai import VendorClient, VendorError


class CompiledImageRequestTests(unittest.TestCase):
    def test_compiled_negative_rejection_does_not_rewrite_positive_prompt(self):
        cli = VendorClient.__new__(VendorClient)
        cli.id = "test"
        cli.base = "https://example.invalid"
        cli.models = {"image": "test-model"}
        cli.endpoints = {"image": "/images/generations"}
        sent = []
        def fake_post(url, payload, timeout):
            sent.append(payload.copy())
            raise VendorError("HTTP 400: negative_prompt unsupported")
        cli._post = fake_post
        with self.assertRaises(VendorError):
            cli.generate_image("少女下坠", "unused.png", negative_prompt="文字", strict_negative=True)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["prompt"], "少女下坠")


if __name__ == "__main__":
    unittest.main()
