import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import { FinanceStack } from '../lib/finance-stack';

let template: Template;
beforeAll(() => {
  const app = new cdk.App();
  const stack = new FinanceStack(app, 'TestStack');
  template = Template.fromStack(stack);
});

test('creates seven DynamoDB tables', () => {
  template.resourceCountIs('AWS::DynamoDB::Table', 7);
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

test('creates ten app Lambda functions plus the S3 notifications handler', () => {
  template.resourceCountIs('AWS::Lambda::Function', 11);
});

test('creates API Gateway', () => {
  template.resourceCountIs('AWS::ApiGateway::RestApi', 1);
  template.hasResourceProperties('AWS::ApiGateway::RestApi', {
    Name: 'finance-api',
  });
});

test('does not create any Secrets Manager secret (Claude goes through Bedrock IAM)', () => {
  template.resourceCountIs('AWS::SecretsManager::Secret', 0);
});

test('creates CloudFront distribution', () => {
  template.resourceCountIs('AWS::CloudFront::Distribution', 1);
});

describe('RAG resources', () => {
  const fnLogicalId = (prefix: string) =>
    Object.keys(template.findResources('AWS::Lambda::Function')).find(id => id.startsWith(prefix))!;

  const policyStatementsFor = (fnPrefix: string) => {
    const roleRef = template.findResources('AWS::Lambda::Function')[fnLogicalId(fnPrefix)].Properties.Role['Fn::GetAtt'][0];
    return Object.values(template.findResources('AWS::IAM::Policy'))
      .filter((p: any) => p.Properties.Roles.some((r: any) => r.Ref === roleRef))
      .flatMap((p: any) => p.Properties.PolicyDocument.Statement);
  };

  const actionsOf = (st: any) => ([] as string[]).concat(st.Action);

  test('vector index is 512-dim cosine with non-filterable text metadata', () => {
    template.hasResourceProperties('AWS::S3Vectors::Index', {
      IndexName: 'penny-docs-v1',
      DataType: 'float32',
      Dimension: 512,
      DistanceMetric: 'cosine',
      MetadataConfiguration: { NonFilterableMetadataKeys: ['text', 'page', 'fileKey', 'fileName'] },
    });
  });

  test('IndexLambda has a DLQ, 2 async retries and a 5 minute timeout', () => {
    const fn = template.findResources('AWS::Lambda::Function')[fnLogicalId('IndexLambda')];
    expect(fn.Properties.DeadLetterConfig.TargetArn).toBeDefined();
    expect(fn.Properties.Timeout).toBe(300);
    template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', {
      FunctionName: { Ref: fnLogicalId('IndexLambda') },
      MaximumRetryAttempts: 2,
    });
  });

  test('ParseLambda timeout leaves room for the truncation retry, with one async retry', () => {
    const fn = template.findResources('AWS::Lambda::Function')[fnLogicalId('ParseLambda')];
    expect(fn.Properties.Timeout).toBe(600);
    template.hasResourceProperties('AWS::Lambda::EventInvokeConfig', {
      FunctionName: { Ref: fnLogicalId('ParseLambda') },
      MaximumRetryAttempts: 1,
    });
  });

  test('IndexLambda is wired to the vector bucket/index and created after the index', () => {
    const fnId = fnLogicalId('IndexLambda');
    const fn = template.findResources('AWS::Lambda::Function')[fnId];
    expect(fn.Properties.Environment.Variables.VECTOR_INDEX).toBe('penny-docs-v1');
    expect(fn.Properties.Environment.Variables.EMBED_DIMENSIONS).toBe('512');
    template.hasResourceProperties('AWS::S3Vectors::Index', { Dimension: 512 });
    expect(fn.Properties.Environment.Variables.VECTOR_BUCKET).toEqual({ 'Fn::Join': ['', ['penny-vectors-', { Ref: 'AWS::AccountId' }]] });
    expect(fn.DependsOn).toEqual(expect.arrayContaining([expect.stringMatching(/^DocsVectorIndex/)]));
    template.hasResourceProperties('AWS::S3Vectors::VectorBucket', {
      VectorBucketName: { 'Fn::Join': ['', ['penny-vectors-', { Ref: 'AWS::AccountId' }]] },
    });
  });

  test('S3 notifies IndexLambda only for the text/ prefix', () => {
    const notif = Object.values(template.findResources('Custom::S3BucketNotifications'))[0] as any;
    const configs = notif.Properties.NotificationConfiguration.LambdaFunctionConfigurations;
    const byPrefix = Object.fromEntries(configs.map((c: any) => [c.Filter.Key.FilterRules[0].Value,
                                                                 c.LambdaFunctionArn['Fn::GetAtt'][0]]));
    expect(Object.keys(byPrefix).sort()).toEqual(['text/', 'uploads/']);
    expect(byPrefix['text/']).toBe(fnLogicalId('IndexLambda'));
    expect(byPrefix['uploads/']).toBe(fnLogicalId('ParseLambda'));
  });

  test('IndexLambda IAM: S3 Vectors on the index only, Titan V2 only, ListBucket for manifest reads', () => {
    const statements = policyStatementsFor('IndexLambda');
    const vectorSt = statements.find((st: any) => actionsOf(st).includes('s3vectors:PutVectors'));
    expect(vectorSt.Resource).toEqual({ 'Fn::GetAtt': [expect.stringMatching(/^DocsVectorIndex/), 'IndexArn'] });
    expect(actionsOf(vectorSt).sort()).toEqual(['s3vectors:DeleteVectors', 's3vectors:PutVectors']);
    const bedrockSt = statements.find((st: any) => actionsOf(st).includes('bedrock:InvokeModel'));
    expect(JSON.stringify(bedrockSt.Resource)).toContain('titan-embed-text-v2');
    expect(statements.some((st: any) => actionsOf(st).some(a => a === 's3:List*' || a === 's3:ListBucket'))).toBe(true);
    expect(statements.some((st: any) => actionsOf(st).includes('dynamodb:UpdateItem'))).toBe(true);
    // never allowed to write the text/ prefix (would re-trigger itself)
    const writes = statements.filter((st: any) => actionsOf(st).some(
      a => a.startsWith('s3:PutObject') || a.startsWith('s3:DeleteObject') || a.startsWith('s3:Abort')));
    expect(JSON.stringify(writes)).not.toContain('text/');
    const deletes = statements.filter((st: any) => actionsOf(st).some(a => a.startsWith('s3:DeleteObject')));
    expect(deletes).toEqual([]);   // not on text/, not on manifests/
  });

  test('GET /api/entries/{id}/evidence is integrated with QueryLambda', () => {
    const [resId] = Object.keys(template.findResources('AWS::ApiGateway::Resource', { Properties: { PathPart: 'evidence' } }));
    expect(resId).toBeDefined();
    const methods = Object.values(template.findResources('AWS::ApiGateway::Method', {
      Properties: { HttpMethod: 'GET', ResourceId: { Ref: resId } },
    })) as any[];
    expect(methods).toHaveLength(1);
    expect(JSON.stringify(methods[0].Properties.Integration.Uri)).toContain(fnLogicalId('QueryLambda'));
  });
});

describe('Advisor agent', () => {
  const fnId = (prefix: string) =>
    Object.keys(template.findResources('AWS::Lambda::Function')).find(id => id.startsWith(prefix))!;

  test('AdvisorLambda is wired to the vector index and model, within the API Gateway limit', () => {
    const fn = template.findResources('AWS::Lambda::Function')[fnId('AdvisorLambda')];
    const env = fn.Properties.Environment.Variables;
    expect(env.VECTOR_INDEX).toBe('penny-docs-v1');
    expect(env.EMBED_DIMENSIONS).toBe('512');
    expect(env.ADVISOR_MODEL_ID).toBe('us.anthropic.claude-haiku-4-5-20251001-v1:0');
    expect(env.VECTOR_BUCKET).toEqual({ 'Fn::Join': ['', ['penny-vectors-', { Ref: 'AWS::AccountId' }]] });
    expect(fn.Properties.Timeout).toBeLessThanOrEqual(30);
  });

  test('AdvisorLambda can query (not write) the vector index', () => {
    const roleRef = template.findResources('AWS::Lambda::Function')[fnId('AdvisorLambda')].Properties.Role['Fn::GetAtt'][0];
    const statements = Object.values(template.findResources('AWS::IAM::Policy'))
      .filter((p: any) => p.Properties.Roles.some((r: any) => r.Ref === roleRef))
      .flatMap((p: any) => p.Properties.PolicyDocument.Statement);
    const actions = statements.flatMap((st: any) => ([] as string[]).concat(st.Action));
    const vectorSt = statements.find((st: any) => ([] as string[]).concat(st.Action).includes('s3vectors:QueryVectors'));
    expect(([] as string[]).concat(vectorSt.Action).sort()).toEqual(['s3vectors:GetVectors', 's3vectors:QueryVectors']);
    expect(vectorSt.Resource).toEqual({ 'Fn::GetAtt': [expect.stringMatching(/^DocsVectorIndex/), 'IndexArn'] });
    expect(actions).not.toContain('s3vectors:PutVectors');
    expect(actions).not.toContain('s3vectors:DeleteVectors');
  });
});
