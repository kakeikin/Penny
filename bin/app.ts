import * as cdk from 'aws-cdk-lib';
import { FinanceStack } from '../lib/finance-stack';

const app = new cdk.App();
new FinanceStack(app, 'FinanceStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    // Pinned: Lambda code creates bedrock/s3vectors clients in us-east-1, and IAM grants are region-scoped.
    region: 'us-east-1',
  },
});
