import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { assertLayerInSync, layerProblems } from '../lib/layer-check';

function makeRepo(srcFiles: Record<string, string>, layerFiles?: Record<string, string>): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'layer-check-'));
  const src = path.join(root, 'lambda', 'common', 'penny_common');
  fs.mkdirSync(src, { recursive: true });
  for (const [name, body] of Object.entries(srcFiles)) fs.writeFileSync(path.join(src, name), body);
  if (layerFiles) {
    const built = path.join(root, 'lambda', 'layer', 'python', 'penny_common');
    fs.mkdirSync(built, { recursive: true });
    for (const [name, body] of Object.entries(layerFiles)) fs.writeFileSync(path.join(built, name), body);
  }
  return root;
}

test('in-sync layer passes', () => {
  const root = makeRepo({ '__init__.py': '', 'a.py': 'x = 1\n' }, { '__init__.py': '', 'a.py': 'x = 1\n' });
  expect(layerProblems(root)).toEqual([]);
  expect(() => assertLayerInSync(root)).not.toThrow();
});

test('missing, stale and absent layers are reported', () => {
  expect(layerProblems(makeRepo({ 'a.py': '' }))).toEqual(['lambda/layer/python/penny_common is missing']);
  const root = makeRepo({ 'a.py': 'new\n', 'b.py': '' }, { 'a.py': 'old\n' });
  expect(layerProblems(root)).toEqual([
    'penny_common/a.py in the layer is out of date',
    'penny_common/b.py is missing from the layer',
  ]);
  expect(() => assertLayerInSync(root)).toThrow(/build-layer\.sh/);
});
