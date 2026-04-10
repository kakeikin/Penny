import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { FinanceStack } from '../lib/finance-stack';

let template: Template;
beforeAll(() => {
  const app = new cdk.App();
  const stack = new FinanceStack(app, 'TestStack');
  template = Template.fromStack(stack);
});

test('creates three DynamoDB tables', () => {
  template.resourceCountIs('AWS::DynamoDB::Table', 3);
});

test('finance-accounts table has correct partition key', () => {
  template.hasResourceProperties('AWS::DynamoDB::Table', {
    TableName: 'finance-accounts',
    KeySchema: [{ AttributeName: 'accountId', KeyType: 'HASH' }],
  });
});

test('finance-journal-entries table has GSI on yearMonth/date', () => {
  template.hasResourceProperties('AWS::DynamoDB::Table', {
    TableName: 'finance-journal-entries',
    GlobalSecondaryIndexes: Match.arrayWith([
      Match.objectLike({ IndexName: 'date-index' }),
    ]),
  });
});

test('finance-journal-lines table has composite key', () => {
  template.hasResourceProperties('AWS::DynamoDB::Table', {
    TableName: 'finance-journal-lines',
    KeySchema: Match.arrayWith([
      { AttributeName: 'entryId', KeyType: 'HASH' },
      { AttributeName: 'lineId', KeyType: 'RANGE' },
    ]),
  });
});

test('creates S3 bucket with public access blocked', () => {
  template.resourceCountIs('AWS::S3::Bucket', 1);
  template.hasResourceProperties('AWS::S3::Bucket', {
    PublicAccessBlockConfiguration: {
      BlockPublicAcls: true,
      BlockPublicPolicy: true,
      IgnorePublicAcls: true,
      RestrictPublicBuckets: true,
    },
  });
});

test('creates five Lambda functions', () => {
  template.resourceCountIs('AWS::Lambda::Function', 6);
});

test('creates API Gateway', () => {
  template.resourceCountIs('AWS::ApiGateway::RestApi', 1);
  template.hasResourceProperties('AWS::ApiGateway::RestApi', {
    Name: 'finance-api',
  });
});

test('creates Secrets Manager secret for Claude API key', () => {
  template.hasResourceProperties('AWS::SecretsManager::Secret', {
    Name: 'finance/claude-api-key',
  });
});

test('creates CloudFront distribution', () => {
  template.resourceCountIs('AWS::CloudFront::Distribution', 1);
});
