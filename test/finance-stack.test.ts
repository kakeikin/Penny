import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
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

test('creates S3 bucket', () => {
  template.resourceCountIs('AWS::S3::Bucket', 1);
});

test('creates API Gateway', () => {
  template.resourceCountIs('AWS::ApiGateway::RestApi', 1);
});

test('creates Secrets Manager secret for Claude API key', () => {
  template.hasResourceProperties('AWS::SecretsManager::Secret', {
    Name: 'finance/claude-api-key',
  });
});
