import test from 'node:test';
import assert from 'node:assert/strict';

import { freezeQueuedJobIds, toChatGPTRunView } from '../src/utils/chatgptRun.ts';

test('只把启动时选中的 queued job 固定进运行范围', () => {
  const jobs = [
    { id: 'a', status: 'queued' },
    { id: 'b', status: 'done' },
    { id: 'c', status: 'queued' },
    { id: 'd', status: 'queued' },
  ];
  assert.deepEqual(freezeQueuedJobIds(jobs, ['c', 'b', 'a', 'c']), ['c', 'a']);
  assert.deepEqual(freezeQueuedJobIds(jobs, ['d']), ['d']);
});

test('waiting_dependencies 和 needs_review 显示可操作原因', () => {
  const waiting = toChatGPTRunView({
    run_id: 'r1', status: 'waiting_dependencies', pause_reason: '缺少父资产参考图：@character:hero',
    job_ids: ['a'], batches: [['a']], cursor: 0, counts: { total: 1, imported: 0, remaining: 1 }, revision: 2,
  });
  const review = toChatGPTRunView({
    run_id: 'r2', status: 'needs_review', pause_reason: '结果与另一任务完全相同',
    job_ids: ['a'], batches: [['a']], cursor: 0, counts: { total: 1, imported: 0, remaining: 1 }, revision: 3,
  });
  assert.match(waiting.actionableReason, /父资产/);
  assert.match(review.actionableReason, /完全相同/);
  assert.equal(review.tone, 'warning');
});

test('完成数以 imported 计数，不以 generated 计数', () => {
  const view = toChatGPTRunView({
    run_id: 'r1', status: 'staged', pause_reason: '', job_ids: ['a', 'b'], batches: [['a', 'b']], cursor: 0,
    counts: { total: 2, imported: 0, remaining: 2 }, revision: 7,
    current_attempt: { attempt_id: 'x', job_id: 'a', phase: 'staged' },
  });
  assert.equal(view.completed, 0);
  assert.equal(view.progressText, '0/2 已导入');
  assert.equal(view.percent, 0);
});

test('二十个任务按每批十项显示当前批次', () => {
  const ids = Array.from({ length: 20 }, (_, index) => `job-${index + 1}`);
  const view = toChatGPTRunView({
    run_id: 'r1', status: 'ready', pause_reason: '', job_ids: ids,
    batches: [ids.slice(0, 10), ids.slice(10)], cursor: 12,
    counts: { total: 20, imported: 12, remaining: 8 }, revision: 20,
  });
  assert.equal(view.batchText, '第 2/2 批');
  assert.equal(view.percent, 60);
});
