import * as cdk from 'aws-cdk-lib';
import * as path from 'path';
import { FinanceStack } from '../lib/finance-stack';
import { assertLayerInSync } from '../lib/layer-check';

// Refuse to synth/deploy with a stale layer (CI synthesizes without building the layer).
if (!process.env.CI) {
  assertLayerInSync(path.join(__dirname, '..'));
}

const app = new cdk.App();
new FinanceStack(app, 'FinanceStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    // Pinned: Lambda code creates bedrock/s3vectors clients in us-east-1, and IAM grants are region-scoped.
    region: 'us-east-1',
  },
});
