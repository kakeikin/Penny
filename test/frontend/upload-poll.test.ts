// frontend/upload-poll.js is a browser script; it also exports itself for these tests.
const UploadPoll = require('../../frontend/upload-poll.js');

const KEY = 'uploads/abc-mar.pdf';

// Replays one response per poll and advances a fake clock by each sleep.
function harness(responses: any[]) {
  let t = 0;
  let calls = 0;
  const fetchPending = async () => {
    const r = responses[Math.min(calls++, responses.length - 1)];
    if (r instanceof Error) throw r;
    return r;
  };
  const opts = { intervalMs: 3000, timeoutMs: 12000, sleep: async (ms: number) => { t += ms; }, now: () => t };
  return { fetchPending, opts, calls: () => calls };
}

const entry = (id: string, fileKey = KEY) => ({ entryId: id, fileKey });

test('waits until this file has entries and the count holds for two more polls', async () => {
  const h = harness([[], [entry('a')], [entry('a'), entry('b')], [entry('a'), entry('b')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('parsed');
  expect(res.entries.map((e: any) => e.entryId)).toEqual(['a', 'b']);
  expect(h.calls()).toBe(5);    // [a,b] seen at polls 3, 4 and 5
});

test('a pause between entry writes does not end the wait early', async () => {
  const h = harness([[entry('a')], [entry('a')], [entry('a'), entry('b')], [entry('a'), entry('b')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, { ...h.opts, stablePolls: 2 });
  expect(res.entries).toHaveLength(2);
});

test('stops when cancelled (the user left the page)', async () => {
  const h = harness([[]]);
  let left = false;
  const opts = { ...h.opts, sleep: async () => { left = true; }, cancelled: () => left };
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, opts);
  expect(res).toEqual({ status: 'cancelled', entries: [] });
  expect(h.calls()).toBe(1);
});

test('ignores pending entries from other files', async () => {
  const h = harness([[entry('x', 'uploads/other.pdf')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res).toEqual({ status: 'timeout', entries: [] });
});

test('times out when nothing appears (e.g. duplicate file skipped)', async () => {
  const h = harness([[]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('timeout');
  expect(h.calls()).toBe(5);    // t = 0, 3, 6, 9, 12 s
});

test('a transient API error is retried on the next poll', async () => {
  const h = harness([new Error('503'), [entry('a')], [entry('a')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('parsed');
});

test('entries still arriving at the deadline are returned, not reported as a timeout', async () => {
  const h = harness([[], [], [entry('a')], [entry('a'), entry('b')], [entry('a'), entry('b'), entry('c')]]);
  const res = await UploadPoll.waitForParsedEntries(h.fetchPending, KEY, h.opts);
  expect(res.status).toBe('parsed');
  expect(res.entries).toHaveLength(3);
});
