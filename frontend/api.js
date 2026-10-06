// ── Demo / Owner session management ──────────────────────────────────────────
// Demo mode: URL contains ?demo=true  →  isolated sessionStorage session
// Owner mode: no ?demo param          →  sees real data (no sessionId filter)
const Session = (() => {
  const isDemo = new URLSearchParams(location.search).get('demo') === 'true';

  function getId() {
    if (!isDemo) return null;
    let id = sessionStorage.getItem('penny_demo_session');
    if (!id) {
      id = crypto.randomUUID();
      sessionStorage.setItem('penny_demo_session', id);
    }
    return id;
  }

  return { isDemo, getId };
})();

// ── API client ────────────────────────────────────────────────────────────────
const API = {
  async request(method, path, body) {
    const headers = { 'Content-Type': 'application/json' };
    const sid = Session.getId();
    if (sid) headers['X-Session-Id'] = sid;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(`${window.API_BASE}${path}`, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || 'Request failed');
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  },

  get:    (path)        => API.request('GET', path),
  post:   (path, body)  => API.request('POST', path, body),
  put:    (path, body)  => API.request('PUT', path, body),
  delete: (path)        => API.request('DELETE', path),

  async uploadFile(filename, contentType, file) {
    // Pass sessionId so ParseLambda can tag parsed entries correctly
    const { uploadUrl, key } = await API.post('/api/upload', {
      filename,
      contentType,
      sessionId: Session.getId(),   // null in owner mode → owner entries
    });
    await fetch(uploadUrl, { method: 'PUT', body: file, headers: { 'Content-Type': contentType } });
    return key;
  },
};
