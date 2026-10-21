import { test } from 'node:test';
import assert from 'node:assert/strict';
const { coalesceRefresh } = await import(process.env.REFRESH_MODULE);
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };

test('startup and notification bursts share one request', async () => {
  let calls = 0;
  const refresh = coalesceRefresh(async () => { calls++; }, 0);
  await Promise.all([refresh(), refresh(), refresh()]);
  assert.equal(calls, 1);
});

test('notifications arriving in flight get one trailing catch-up without overlap', async () => {
  const started = deferred(), finish = deferred();
  let calls = 0, active = 0, peak = 0;
  const refresh = coalesceRefresh(async () => {
    peak = Math.max(peak, ++active);
    if (++calls === 1) { started.resolve(); await finish.promise; }
    active--;
  }, 0);
  const first = refresh();
  await started.promise;
  const second = refresh(), third = refresh();
  finish.resolve();
  await Promise.all([first, second, third]);
  assert.equal(calls, 2);
  assert.equal(peak, 1);
});

test('disposing cancels queued refreshes and later calls', async () => {
  let calls = 0;
  const refresh = coalesceRefresh(async () => { calls++; }, 0);
  const pending = refresh();
  refresh.dispose();
  await pending;
  await refresh();
  assert.equal(calls, 0);
});

test('a failed refresh does not prevent retry', async () => {
  let calls = 0;
  const refresh = coalesceRefresh(async () => { if (++calls === 1) throw Error('offline'); }, 0);
  await assert.rejects(refresh(), /offline/);
  await refresh();
  assert.equal(calls, 2);
});
