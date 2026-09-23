# -*- coding: utf-8 -*-
"""计费账本：record/读回、price_of 计价（含 "*" 回落与无价格 null）、summary 聚合、
VendorClient.chat 记账（mock _post，验证 ok/fail 各一条、usage 捕获）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import billing
from llm_openai import VendorClient, VendorError


class BillingBase(unittest.TestCase):
    """每个用例独立账本文件，避免污染真实 workbench/billing/ledger.jsonl。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ledger = str(Path(self._tmp.name) / 'billing' / 'ledger.jsonl')
        patcher = patch.object(billing, 'LEDGER', self.ledger)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def rows(self):
        path = Path(self.ledger)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


class RecordTests(BillingBase):
    def test_record_and_read_back(self):
        self.assertTrue(billing.record({'vendor': 'glm', 'kind': 'text', 'model': 'glm-5.2',
                                        'op': 'chat', 'ok': True, 'cost': 0.001, 'currency': 'CNY'}))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['vendor'], 'glm')
        self.assertIn('ts', rows[0])          # ts 缺省自动补当前时间
        # recent 读回且新在前
        billing.record({'vendor': 'kimi', 'kind': 'text', 'model': 'k', 'op': 'chat', 'ok': False, 'cost': 0})
        recent = billing.recent(10)
        self.assertEqual([r['vendor'] for r in recent], ['kimi', 'glm'])
        self.assertEqual(len(billing.recent(1)), 1)

    def test_record_never_raises(self):
        self.assertFalse(billing.record('not-a-dict'))
        # 账本路径指向一个已存在的目录：open 追加必然失败，验证异常静默
        with patch.object(billing, 'LEDGER', self._tmp.name):
            self.assertFalse(billing.record({'vendor': 'x'}))


class PriceOfTests(unittest.TestCase):
    def test_text_token_pricing(self):
        cfg = {'pricing': {'currency': 'CNY',
                           'text': {'glm-5.2': {'input': 2.0, 'output': 8.0}}}}
        usage = {'prompt_tokens': 1_000_000, 'completion_tokens': 500_000}
        cost, currency = billing.price_of(cfg, 'text', 'glm-5.2', usage, None)
        self.assertEqual((cost, currency), (6.0, 'CNY'))

    def test_wildcard_fallback(self):
        cfg = {'pricing': {'text': {'*': {'input': 1.0, 'output': 2.0}}}}
        cost, currency = billing.price_of(cfg, 'text', 'unknown-model',
                                          {'prompt_tokens': 1_000_000, 'completion_tokens': 0}, None)
        self.assertEqual((cost, currency), (1.0, 'CNY'))

    def test_no_pricing_returns_null(self):
        self.assertEqual(billing.price_of({}, 'text', 'm', {'prompt_tokens': 1}, None), (None, None))
        self.assertEqual(billing.price_of({'pricing': {'image': {'m': {'per_image': 1}}}},
                                          'text', 'm', {'prompt_tokens': 1}, None), (None, None))
        # 文本类有单价但无 usage 时费用为 0 元（token 为零）而非 null
        cost, _ = billing.price_of({'pricing': {'text': {'*': {'input': 1, 'output': 1}}}},
                                   'text', 'm', None, None)
        self.assertEqual(cost, 0.0)

    def test_media_units_pricing(self):
        cfg = {'pricing': {'currency': 'USD',
                           'image': {'seedream': {'per_image': 0.3}},
                           'video': {'seedance': {'per_second': 0.5}},
                           'speech': {'*': {'per_char': 0.001}},
                           'music': {'*': {'per_call': 1.5}}}}
        self.assertEqual(billing.price_of(cfg, 'image', 'seedream', None, {'images': 2}), (0.6, 'USD'))
        self.assertEqual(billing.price_of(cfg, 'video', 'seedance', None, {'seconds': 5}), (2.5, 'USD'))
        # 视频缺 seconds 时无法按秒计量 → null
        self.assertEqual(billing.price_of(cfg, 'video', 'seedance', None, None), (None, None))
        self.assertEqual(billing.price_of(cfg, 'speech', 'any', None, {'chars': 120}), (0.12, 'USD'))
        self.assertEqual(billing.price_of(cfg, 'music', 'any', None, None), (1.5, 'USD'))
        # image_edit 未单独定价时回落 image 表
        self.assertEqual(billing.price_of(cfg, 'image_edit', 'seedream', None, {'images': 1}), (0.3, 'USD'))


class SummaryTests(BillingBase):
    def test_summary_groups_totals_and_days(self):
        entries = [
            {'ts': '2026-09-01 10:00:00', 'vendor': 'glm', 'kind': 'text', 'ok': True, 'cost': 1.0, 'currency': 'CNY'},
            {'ts': '2026-09-01 11:00:00', 'vendor': 'glm', 'kind': 'text', 'ok': False, 'cost': 0},
            {'ts': '2026-09-02 09:00:00', 'vendor': 'glm', 'kind': 'image', 'ok': True, 'cost': 0.3, 'currency': 'CNY'},
            {'ts': '2026-08-15 09:00:00', 'vendor': 'kimi', 'kind': 'text', 'ok': True, 'cost': 2.0, 'currency': 'CNY'},
        ]
        for e in entries:
            billing.record(e)
        s = billing.summary('2026-09')
        self.assertEqual(s['total']['calls'], 3)
        self.assertEqual((s['total']['ok'], s['total']['fail']), (2, 1))
        self.assertEqual(s['total']['cost'], {'CNY': 1.3})
        groups = {(g['vendor'], g['kind']): g for g in s['groups']}
        self.assertEqual(groups[('glm', 'text')]['calls'], 2)
        self.assertEqual(groups[('glm', 'text')]['fail'], 1)
        self.assertEqual(groups[('glm', 'image')]['cost'], {'CNY': 0.3})
        self.assertEqual([d['date'] for d in s['days']], ['2026-09-01', '2026-09-02'])
        # 不传 month 聚合全部月份
        self.assertEqual(billing.summary()['total']['calls'], 4)
        # 空账本不报错
        with patch.object(billing, 'LEDGER', str(Path(self._tmp.name) / 'none.jsonl')):
            self.assertEqual(billing.summary()['total']['calls'], 0)


class VendorClientChatBillingTests(BillingBase):
    """mock _post：chat 成功/失败各记一条；usage 捕获到 last_usage 与账本。"""

    def client(self):
        return VendorClient.from_config({
            'id': 'glm', 'enabled': True, 'api_key': 'test-placeholder',
            'base_url': 'https://open.bigmodel.cn/api/paas/v4',
            'models': {'text': 'glm-5.2'},
            'pricing': {'currency': 'CNY', 'text': {'glm-5.2': {'input': 2.0, 'output': 8.0}}}})

    def test_chat_success_bills_with_usage(self):
        client = self.client()
        usage = {'prompt_tokens': 1_000_000, 'completion_tokens': 250_000, 'total_tokens': 1_250_000}
        with patch.object(client, '_post', return_value={
                'choices': [{'message': {'content': '你好'}}], 'usage': usage}):
            self.assertEqual(client.chat([{'role': 'user', 'content': 'hi'}]), '你好')
        self.assertEqual(client.last_usage, usage)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row['ok'])
        self.assertEqual((row['vendor'], row['kind'], row['model'], row['op']), ('glm', 'text', 'glm-5.2', 'chat'))
        self.assertEqual(row['usage'], usage)
        self.assertEqual(row['cost'], 4.0)          # 2.0×1M + 8.0×0.25M（每百万）
        self.assertEqual(row['currency'], 'CNY')

    def test_chat_failure_bills_zero_cost(self):
        client = self.client()
        with patch.object(client, '_post', side_effect=VendorError('HTTP 429: 限流')):
            with self.assertRaises(VendorError):
                client.chat([{'role': 'user', 'content': 'hi'}])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['ok'])
        self.assertEqual(rows[0]['cost'], 0)
        self.assertIn('429', rows[0]['error'])

    def test_chat_success_without_pricing_records_null_cost(self):
        client = self.client()
        client.cfg.pop('pricing')
        with patch.object(client, '_post', return_value={'choices': [{'message': {'content': 'ok'}}]}):
            client.chat([{'role': 'user', 'content': 'hi'}])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['ok'])
        self.assertIsNone(rows[0]['cost'])          # 无价格配置仍记录调用，cost=null
        self.assertIsNone(client.last_usage)        # 响应无 usage 时 last_usage=None


if __name__ == '__main__':
    unittest.main()
