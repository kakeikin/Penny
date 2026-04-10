import * as cdk from 'aws-cdk-lib';
import { FinanceStack } from '../lib/finance-stack';

const app = new cdk.App();
new FinanceStack(app, 'FinanceStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
  },
});
