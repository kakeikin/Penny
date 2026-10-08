// Waits for ParseLambda to finish writing the entries of one uploaded file.
const UploadPoll = (() => {
  const realSleep = ms => new Promise(r => setTimeout(r, ms));

  // Resolves {status: 'parsed', entries} once the file's PENDING entries appear and their count
  // holds steady for `stablePolls` more polls (entries are written one by one), or
  // {status: 'timeout'}. A file uploaded before is skipped by ParseLambda as a duplicate, so it
  // always times out. `cancelled()` (e.g. the user left the page) stops polling early.
  async function waitForParsedEntries(fetchPending, fileKey,
      { intervalMs = 3000, timeoutMs = 120000, stablePolls = 2, sleep = realSleep, now = () => Date.now(),
        cancelled = () => false } = {}) {
    const deadline = now() + timeoutMs;
    let lastCount = 0;
    let stable = 0;
    for (;;) {
      if (cancelled()) return { status: 'cancelled', entries: [] };
      let pending = null;
      try { pending = await fetchPending(); } catch (e) { /* transient: try again next poll */ }
      const mine = (Array.isArray(pending) ? pending : []).filter(e => e.fileKey === fileKey);
      stable = mine.length && mine.length === lastCount ? stable + 1 : 0;
      if (stable >= stablePolls) return { status: 'parsed', entries: mine };
      lastCount = mine.length;
      if (now() >= deadline) {
        return mine.length ? { status: 'parsed', entries: mine } : { status: 'timeout', entries: [] };
      }
      await sleep(intervalMs);
    }
  }

  return { waitForParsedEntries };
})();

if (typeof module !== 'undefined') module.exports = UploadPoll;   // jest
