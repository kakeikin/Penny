import * as fs from 'fs';
import * as path from 'path';

/**
 * Compare lambda/common/penny_common/*.py with the copy inside the built Lambda layer.
 * Returns human-readable problems; an empty list means the layer is in sync.
 *
 * The layer (lambda/layer/python) is gitignored and built by scripts/build-layer.sh.
 * Deploying a stale one makes every Lambda that imports penny_common fail at init.
 */
export function layerProblems(repoRoot: string): string[] {
  const src = path.join(repoRoot, 'lambda', 'common', 'penny_common');
  const built = path.join(repoRoot, 'lambda', 'layer', 'python', 'penny_common');
  if (!fs.existsSync(built)) {
    return [`${path.relative(repoRoot, built)} is missing`];
  }
  const problems: string[] = [];
  for (const name of fs.readdirSync(src).filter(f => f.endsWith('.py')).sort()) {
    const builtFile = path.join(built, name);
    if (!fs.existsSync(builtFile)) {
      problems.push(`penny_common/${name} is missing from the layer`);
    } else if (!fs.readFileSync(builtFile).equals(fs.readFileSync(path.join(src, name)))) {
      problems.push(`penny_common/${name} in the layer is out of date`);
    }
  }
  return problems;
}

export function assertLayerInSync(repoRoot: string): void {
  const problems = layerProblems(repoRoot);
  if (problems.length) {
    throw new Error(`Lambda layer is stale:\n  - ${problems.join('\n  - ')}\n` +
                    'Run ./scripts/build-layer.sh before cdk synth/deploy (set CI=true to skip this check).');
  }
}
