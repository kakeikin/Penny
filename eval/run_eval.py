"""Run Penny's deterministic eval against the deployed stack (manual; needs AWS credentials).

    python eval/run_eval.py setup            # once: upload + confirm the synthetic corpus (~$0.10)
    python eval/run_eval.py run              # 20 questions x 2 models (~$1), writes results

Setup creates a private demo session (`eval-v1-<random>`, saved in eval/.state/, which is
gitignored) so nobody else can add entries to the eval ledger. `run` refuses to score unless
the booked ledger equals the gold transactions and every evidence item is indexed.

`run` imports the real AdvisorLambda code and calls its handler locally with the deployed
function's configuration, so both models run the same agent against the same data; only
ADVISOR_MODEL_ID changes. Latency is measured from this machine calling Bedrock directly.
docs/evaluation-results.md is only written by a full, error-free run.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone

import boto3
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics  # noqa: E402

STACK = 'FinanceStack'
REGION = 'us-east-1'
DATASET = os.path.join(ROOT, 'eval', 'dataset', 'v1')
STATE = os.path.join(ROOT, 'eval', '.state', 'session.json')
RESULTS_DIR = os.path.join(ROOT, 'eval', 'results')
REPORT = os.path.join(ROOT, 'docs', 'evaluation-results.md')
MODELS = {
    'haiku-4.5':  'us.anthropic.claude-haiku-4-5-20251001-v1:0',
    'sonnet-4.6': 'us.anthropic.claude-sonnet-4-6',      # the model ParseLambda already uses
}
CONTENT_TYPES = {'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg'}
POLL_S = 10


def load(name):
    with open(os.path.join(DATASET, name)) as f:
        return json.load(f)


def load_state(create=False) -> dict:
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    if not create:
        sys.exit('no eval session yet: run `python eval/run_eval.py setup` first')
    state = {'session': f'eval-v1-{secrets.token_hex(8)}', 'uploaded': {}}
    save_state(state)
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, 'w') as f:
        json.dump(state, f, indent=2)


def site_url() -> str:
    outputs = boto3.client('cloudformation', region_name=REGION).describe_stacks(StackName=STACK)['Stacks'][0]['Outputs']
    return next(o['OutputValue'] for o in outputs if o['OutputKey'] == 'SiteUrl').rstrip('/')


class Api:
    def __init__(self, site, session):
        self.site, self.session = site, session

    def call(self, method, path, body=None):
        r = requests.request(method, f'{self.site}{path}', json=body, timeout=30,
                             headers={'X-Session-Id': self.session})
        if not r.ok:
            raise RuntimeError(f'{method} {path} -> {r.status_code}: {r.text[:200]}')
        return r.json()

    def pending(self):          # PENDING and DUPLICATE_SUSPECT
        return self.call('GET', '/api/entries?status=PENDING')

    def confirmed(self):
        return self.call('GET', '/api/entries')


def file_of(entry) -> str:
    return entry.get('fileKey', '').rsplit('/', 1)[-1].split('-', 5)[-1]   # uploads/demo-sid/<uuid>-<name>


# ── setup ────────────────────────────────────────────────────────────────────

def setup(site):
    state = load_state(create=True)
    api = Api(site, state['session'])
    manifest = load('manifest.json')
    print(f'eval session: {state["session"]}')
    for f in manifest['files']:
        if f['file'] in state['uploaded']:          # never upload twice, even while still parsing
            print(f'{f["file"]}: already uploaded')
            continue
        ext = f['file'].rsplit('.', 1)[-1]
        up = api.call('POST', '/api/upload', {'filename': f['file'], 'contentType': CONTENT_TYPES[ext],
                                              'sessionId': state['session']})
        with open(os.path.join(DATASET, f['file']), 'rb') as fh:
            requests.put(up['uploadUrl'], data=fh.read(), headers={'Content-Type': CONTENT_TYPES[ext]},
                         timeout=60).raise_for_status()
        state['uploaded'][f['file']] = up['key']
        save_state(state)
        print(f'{f["file"]}: uploaded')

    # Parsing runs asynchronously; wait until every file has entries and the count stops changing.
    deadline, last = time.monotonic() + 600, None
    while True:
        entries = api.pending() + api.confirmed()
        counts = {f['file']: sum(1 for e in entries if file_of(e) == f['file']) for f in manifest['files']}
        if all(counts.values()) and counts == last:
            break
        if time.monotonic() > deadline:
            sys.exit(f'timed out waiting for parsing: {counts}. If a parse failed for good, delete eval/.state/ '
                 'and run setup again (a fresh session re-uploads everything).')
        last = counts
        time.sleep(POLL_S)
    wrong = {f['file']: (counts[f['file']], f['entries']) for f in manifest['files'] if counts[f['file']] != f['entries']}
    if wrong:
        sys.exit('parsed entry counts differ from the manifest (got, expected): '
                 f'{wrong}. Inspect the eval session before confirming anything.')
    pending = api.pending()
    suspects = [e['entryId'] for e in pending if e.get('status') != 'PENDING']
    if suspects:
        sys.exit(f'{len(suspects)} DUPLICATE_SUSPECT entries in the eval session; not confirming. Ids: {suspects}')
    for e in pending:
        try:
            api.call('PUT', f'/api/entries/{e["entryId"]}/confirm', {})   # never acknowledges unconverted FX
        except RuntimeError as err:
            sys.exit(f'confirm failed for {e["entryId"]}: {err}')
    print(f'confirmed {len(pending)} entries')

    # IndexLambda backfills chunkKey on evidence once the document's vectors exist.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        missing = unindexed(api.confirmed())
        if not missing:
            print('vectors indexed; setup complete')
            return
        time.sleep(POLL_S)
    sys.exit(f'{missing} evidence items still have no chunkKey (check IndexLambda logs / DLQ), then re-run setup')


def unindexed(entries) -> int:
    return sum(1 for e in entries for ev in e.get('evidence', []) if not ev.get('chunkKey'))


# ── run ──────────────────────────────────────────────────────────────────────

def load_advisor():
    """Import lambda/advisor/index.py with the deployed function's environment."""
    lam = boto3.client('lambda', region_name=REGION)
    name = next(fn['FunctionName'] for page in lam.get_paginator('list_functions').paginate()
                for fn in page['Functions'] if 'AdvisorLambda' in fn['FunctionName'])
    env = lam.get_function_configuration(FunctionName=name)['Environment']['Variables']
    os.environ.update(env)
    os.environ.setdefault('AWS_DEFAULT_REGION', REGION)
    sys.path.insert(0, os.path.join(ROOT, 'lambda', 'common'))
    spec = importlib.util.spec_from_file_location('advisor_index', os.path.join(ROOT, 'lambda', 'advisor', 'index.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, env


class _LambdaContext:
    aws_request_id = 'eval'

    @staticmethod
    def get_remaining_time_in_millis():
        return 30000


def _log_fields(captured: str) -> dict:
    """Ids-and-counts fields from the handler's own JSON log lines (no text or amounts in them)."""
    out = {}
    for line in captured.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('event') == 'advisor_answered':
            out.update({k: event.get(k) for k in ('rounds', 'toolErrors', 'stopReason', 'retrievalMs', 'latencyMs')})
        elif event.get('event') in ('advisor_failed', 'tool_failed'):
            out.setdefault('errors', []).append({k: event.get(k) for k in ('event', 'tool', 'errorType')})
    return out


def ask(adv, session, question):
    event = {'httpMethod': 'POST', 'headers': {'X-Session-Id': session}, 'body': json.dumps({'question': question})}
    buf = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stdout(buf):
        resp = adv.handler(event, _LambdaContext())
    latency = int((time.monotonic() - started) * 1000)
    body = json.loads(resp['body'])
    return resp['statusCode'], body, latency, _log_fields(buf.getvalue())


def retrieval(adv, session, queries):
    ranks = []
    for q in queries:
        if not q.get('goldDocs'):
            continue
        ctx = adv.Context(session, {}, deadline=time.monotonic() + 60)
        results = adv.search_documents({'query': q['question'], 'topK': 5}, ctx)
        ranks.append(metrics.rank_of(results, q['goldDocs']))
    return metrics.retrieval_metrics(ranks)


def run(site, model_names, limit):
    state = load_state()
    session = state['session']
    api = Api(site, session)
    gold, manifest = load('gold_transactions.json'), load('manifest.json')
    entries = api.confirmed()
    ledger = metrics.ledger_matches_gold(gold, entries)
    if not ledger['ok']:
        sys.exit(f'eval ledger does not match the gold transactions ({ledger}); fix the session before scoring')
    if unindexed(entries):
        sys.exit(f'{unindexed(entries)} evidence items are not indexed yet; re-run setup')
    queries = load('queries.json')
    full = limit is None and list(model_names) == list(MODELS)
    queries = queries[:limit]
    adv, env = load_advisor()
    from penny_common.chunking import CHUNKER_VERSION
    report = {'startedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'datasetVersion': manifest['datasetVersion'], 'manifest': manifest['files'],
              'embedDimensions': env.get('EMBED_DIMENSIONS'), 'vectorIndex': env.get('VECTOR_INDEX'),
              'chunkerVersion': CHUNKER_VERSION, 'limit': limit, 'modelsRun': list(model_names),
              'ledger': ledger, 'retrieval': retrieval(adv, session, queries),
              'evidence': metrics.evidence_metrics(gold, entries), 'models': {}}
    for label in model_names:
        adv.MODEL_ID = MODELS[label]
        rows, costs, latencies = [], [], []
        for i, q in enumerate(queries):
            status, body, latency, logs = ask(adv, session, q['question'])
            ok = status == 200
            if i == 0 and not ok:          # wrong model id / no model access: fail fast
                write_raw(report)            # keep (and pay for) any model already scored
                sys.exit(f'[{label}] first question failed with {status} {logs.get("errors")}; '
                         f'check that {MODELS[label]} is enabled in Bedrock')
            score = metrics.score_answer(q, body if ok else {}, ok=ok)
            cost = (body.get('usage') or {}).get('estCostUsd') if ok else None
            rows.append({**score, 'pass': metrics.passed(score), 'question': q['question'],
                         'answer': body.get('answer') if ok else None, 'statusCode': status,
                         'toolsUsed': body.get('toolsUsed'), 'evidenceStatus': body.get('evidenceStatus'),
                         'latencyMs': latency, 'estCostUsd': cost, **logs})
            costs.append(cost)
            if ok:
                latencies.append(latency)
            print(f'[{label}] {q["id"]} {"ok  " if rows[-1]["pass"] else "MISS"} {latency:>6} ms  '
                  f'${cost if cost is not None else "n/a"}' + ('' if ok else f'  HTTP {status}'))
        report['models'][label] = {'modelId': MODELS[label],
                                   'summary': metrics.summarize(rows, costs, latencies), 'questions': rows}
    raw_path = write_raw(report)
    errors = sum(m['summary']['errors'] for m in report['models'].values())
    if not full:
        print('partial run (--limit or --models): docs/evaluation-results.md left unchanged')
    elif errors:
        print(f'{errors} answers errored: docs/evaluation-results.md left unchanged; see the raw JSON')
    else:
        with open(REPORT, 'w') as f:
            f.write(render(report, os.path.relpath(raw_path, ROOT)))
        print(f'wrote {os.path.relpath(REPORT, ROOT)}')


def write_raw(report) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = report['startedAt'].replace(':', '').replace('+0000', 'Z')
    raw_path = os.path.join(RESULTS_DIR, f'run-{stamp}.json')
    with open(raw_path, 'w') as f:
        json.dump(report, f, indent=2)
    print(f'wrote {os.path.relpath(raw_path, ROOT)}')
    return raw_path


def _pct(r):
    if r is None or r.get('rate') is None:
        return '—'
    return f'{r["rate"] * 100:.0f}% ({r["passed"]}/{r["n"]})'


def _frac(rate, n):
    return '—' if rate is None else f'{rate * 100:.0f}% ({round(rate * n)}/{n})'


def _usd(v):
    return '—' if v is None else f'${v}'


def _secs(v):
    return '—' if v is None else f'{v / 1000:.1f} s'


def render(r, raw_path) -> str:
    models = list(r['models'])
    files = r['manifest']
    kinds = {k: sum(1 for f in files if f['kind'] == k) for k in ('statement', 'receipt')}
    n = r['models'][models[0]]['summary']['n']
    rows = [('Questions fully passed', 'passRate', _pct), ('Tool selection', 'toolSelection', _pct),
            ('Numeric exactness', 'numericExactness', _pct), ('Text match', 'textMatch', _pct),
            ('Citation validity (answers with citations)', 'citationValidity', _pct),
            ('Answers stating money that cite a source', 'moneyCited', _pct),
            ('Errors', 'errors', str), ('Truncated', 'truncated', str),
            ('Mean cost / question', 'meanCostUsd', _usd), ('Latency p50', 'latencyP50Ms', _secs),
            ('Latency p95', 'latencyP95Ms', _secs)]
    out = ['# Penny evaluation results', '',
           f'Generated by `eval/run_eval.py` on {r["startedAt"]}. Raw results: `{raw_path}`. '
           'Resume figures come only from this file.', '',
           '| Run metadata | |', '|---|---|',
           f'| Dataset | `{r["datasetVersion"]}`: {kinds["statement"]} synthetic statements, '
           f'{kinds["receipt"]} synthetic receipts, {r["ledger"]["expected"]} gold transactions, {n} questions |',
           f'| Ledger check | booked entries equal the gold transactions ({r["ledger"]["booked"]}/{r["ledger"]["expected"]}) |',
           f'| Embeddings | Titan Text Embeddings V2, {r["embedDimensions"]} dims, index `{r["vectorIndex"]}` |',
           f'| Chunker version | `{r["chunkerVersion"]}` |',
           '| Models | ' + ', '.join(f'{m} (`{r["models"][m]["modelId"]}`)' for m in models) + ' |', '',
           '## Agent', '', '| Metric | ' + ' | '.join(models) + ' |', '|---|' + '---|' * len(models)]
    for label, key, fmt in rows:
        out.append(f'| {label} | ' + ' | '.join(fmt(r['models'][m]['summary'][key]) for m in models) + ' |')
    ret, ev = r['retrieval'], r['evidence']
    out += ['', '## Retrieval (search_documents, same session filter as the agent)', '',
            f'{ret["n"]} questions with gold documents: hit@3 {_frac(ret["hit@3"], ret["n"])}, '
            f'hit@5 {_frac(ret["hit@5"], ret["n"])}, MRR {ret["mrr"]}.', '',
            '## Evidence linking (statement lines, text-layer PDFs)', '',
            f'{ev["booked"]}/{ev["n"]} gold lines were booked as entries; '
            f'{ev["evidenceCorrect"]}/{ev["booked"]} carry the whole source line on the right page '
            f'({_frac(ev["evidenceAccuracy"], ev["booked"])}).', '',
            '## Misses', '']
    for m in models:
        misses = [q for q in r['models'][m]['questions'] if not q['pass']]
        out.append(f'- **{m}:** ' + (', '.join(f'{q["id"]} ({q["category"]})' for q in misses) or 'none'))
    out += ['', '## Method and caveats', '',
            '- Deterministic checks only, no LLM judge. Every rate shows its denominator; a check that does '
            'not apply to a question is excluded from that rate. Errored or truncated answers fail.',
            '- **Numeric exactness:** every gold amount appears in the answer (two-decimal amounts compared as '
            f'Decimal, sign ignored), and the answer lists at most {metrics.MAX_EXTRA_FACTOR}x as many other '
            'amounts. No-data questions pass only if the answer states no non-zero figure ($-prefixed or two-decimal).',
            '- **Text match:** a case-insensitive substring (account digits, item names). **Tool selection:** '
            'the expected tool was among the tools called (extra tools are allowed); questions expecting no tool are excluded.',
            '- **Citation validity:** of answers that carry citations, the share with no invalid ref removed by '
            'the server validator. **Money cited:** of answers stating money, the share citing at least one '
            'source; it does not prove every figure is cited.',
            '- **Retrieval** embeds the raw question (no yearMonth/docType filter), not the agent\'s own tool '
            'queries. Balances printed on two statements accept either file as gold.',
            '- **Evidence** requires the whole printed line, so a correct partial quote counts as a miss (conservative).',
            '- The ledger check compares dates and amounts only. A parse that books a transaction to the wrong kind '
            'of account (e.g. transfer instead of expense) shows up as an agent miss on the summary questions.',
            f'- n = {n} per model: one question is {100 / n:.0f} percentage points. One run, default sampling: '
            'results vary between runs.',
            '- Latency is measured from a laptop calling Bedrock directly, not through API Gateway and Lambda. '
            'Costs use list prices from `penny_common/pricing.py`.',
            '- Not measured in this lean version: the 512 vs 1024-dimension ablation, the old advisor baseline, '
            'and the citation validator\'s false-negative rate.', '']
    return '\n'.join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['setup', 'run'])
    p.add_argument('--site', help='CloudFront URL (default: FinanceStack SiteUrl output)')
    p.add_argument('--models', default=','.join(MODELS), help=f'comma-separated, from: {", ".join(MODELS)}')
    p.add_argument('--limit', type=int, default=None, help='only the first N questions (smoke run)')
    a = p.parse_args()
    site = (a.site or site_url()).rstrip('/')
    if a.command == 'setup':
        setup(site)
    else:
        names = [m.strip() for m in a.models.split(',') if m.strip()]
        unknown = [m for m in names if m not in MODELS]
        if unknown:
            sys.exit(f'unknown model(s): {unknown}')
        run(site, names, a.limit)


if __name__ == '__main__':
    main()
