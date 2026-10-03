import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3n from 'aws-cdk-lib/aws-s3-notifications';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import { Construct } from 'constructs';
import * as path from 'path';

export class FinanceStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ── DynamoDB Tables ───────────────────────────────────────
    const accountsTable = new dynamodb.Table(this, 'AccountsTable', {
      tableName: 'finance-accounts',
      partitionKey: { name: 'accountId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const entriesTable = new dynamodb.Table(this, 'EntriesTable', {
      tableName: 'finance-journal-entries',
      partitionKey: { name: 'entryId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    entriesTable.addGlobalSecondaryIndex({
      indexName: 'date-index',
      partitionKey: { name: 'yearMonth', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'date', type: dynamodb.AttributeType.STRING },
    });

    const linesTable = new dynamodb.Table(this, 'LinesTable', {
      tableName: 'finance-journal-lines',
      partitionKey: { name: 'entryId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'lineId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const budgetsTable = new dynamodb.Table(this, 'BudgetsTable', {
      tableName: 'finance-budgets',
      partitionKey: { name: 'accountId', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const monthlyCache = new dynamodb.Table(this, 'MonthlyCacheTable', {
      tableName: 'finance-monthly-cache',
      partitionKey: { name: 'yearMonth', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const exchangeRates = new dynamodb.Table(this, 'ExchangeRatesTable', {
      tableName: 'finance-exchange-rates',
      partitionKey: { name: 'base', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const pushSubscriptions = new dynamodb.Table(this, 'PushSubscriptionsTable', {
      tableName: 'finance-push-subscriptions',
      partitionKey: { name: 'endpoint', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const alertTopic = new sns.Topic(this, 'AlertTopic', {
      topicName: 'finance-budget-alerts',
      displayName: 'Penny Budget Alerts',
    });

    // ── S3 Bucket ─────────────────────────────────────────────
    const appBucket = new s3.Bucket(this, 'AppBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      cors: [{
        allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.GET],
        allowedOrigins: ['*'],
        allowedHeaders: ['*'],
      }],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ── Lambda environment shared vars ────────────────────────
    const lambdaEnv = {
      ACCOUNTS_TABLE: accountsTable.tableName,
      ENTRIES_TABLE: entriesTable.tableName,
      LINES_TABLE: linesTable.tableName,
      APP_BUCKET: appBucket.bucketName,
      BUDGETS_TABLE: budgetsTable.tableName,
      MONTHLY_CACHE_TABLE: monthlyCache.tableName,
      EXCHANGE_RATES_TABLE: exchangeRates.tableName,
      PUSH_SUBSCRIPTIONS_TABLE: pushSubscriptions.tableName,
    };

    // ── Lambda Layer: Python dependencies ─────────────────────
    const pyLayer = new lambda.LayerVersion(this, 'PyDepsLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/layer')),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      compatibleArchitectures: [lambda.Architecture.X86_64],
      description: 'pinned boto3, pypdf, pywebpush + penny_common',
    });

    // ── Lambda Functions ──────────────────────────────────────
    const parseFn = new lambda.Function(this, 'ParseLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/parse')),
      layers: [pyLayer],
      environment: lambdaEnv,
      // Bedrock read_timeout is 270s; 600s leaves room for the no-transcript retry after truncation.
      timeout: cdk.Duration.seconds(600),
      retryAttempts: 1,   // one bad file must not burn ~30 min of Lambda time across async retries
      memorySize: 512,
    });

    const confirmFn = new lambda.Function(this, 'ConfirmLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/confirm')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(10),
    });

    const manualEntryFn = new lambda.Function(this, 'ManualEntryLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/manual-entry')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(10),
    });

    const queryFn = new lambda.Function(this, 'QueryLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/query')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(30),
    });

    const exportFn = new lambda.Function(this, 'ExportLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/export')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(30),
    });

    // ── Permissions ───────────────────────────────────────────
    [parseFn, confirmFn, manualEntryFn, queryFn, exportFn].forEach(fn => {
      accountsTable.grantReadWriteData(fn);
      entriesTable.grantReadWriteData(fn);
      linesTable.grantReadWriteData(fn);
      appBucket.grantReadWrite(fn);
    });

    [parseFn, confirmFn, queryFn, exportFn].forEach(fn => budgetsTable.grantReadData(fn));
    budgetsTable.grantReadWriteData(manualEntryFn);

    // ParseLambda uses Bedrock instead of the Anthropic API key
    parseFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: ['*'],
    }));

    parseFn.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'aws-marketplace:ViewSubscriptions',
        'aws-marketplace:Subscribe',
        'aws-marketplace:Unsubscribe',
      ],
      resources: ['*'],
    }));

    // S3 triggers ParseLambda on uploads/ prefix
    appBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(parseFn),
      { prefix: 'uploads/' },
    );

    // ── RAG: vector store + IndexLambda ───────────────────────
    // Every CfnIndex property forces replacement, and CloudFormation creates the replacement
    // before deleting the old one -- so a fixed name would collide. To change dimension or
    // metadata config: bump the suffix (-v2) and run scripts/backfill_index.py --force.
    const VECTOR_INDEX_NAME = 'penny-docs-v1';
    const EMBED_MODEL_ARN = `arn:aws:bedrock:${cdk.Aws.REGION}::foundation-model/amazon.titan-embed-text-v2:0`;

    // Vectors are derived data (rebuildable from text/ in the retained AppBucket), so the
    // default DELETE removal policy is deliberate: RETAIN + fixed names would block redeploys.
    const vectorBucket = new s3vectors.CfnVectorBucket(this, 'VectorBucket', {
      vectorBucketName: `penny-vectors-${cdk.Aws.ACCOUNT_ID}`,
    });
    const vectorIndex = new s3vectors.CfnIndex(this, 'DocsVectorIndex', {
      vectorBucketArn: vectorBucket.attrVectorBucketArn,
      indexName: VECTOR_INDEX_NAME,
      dataType: 'float32',
      dimension: 512,
      distanceMetric: 'cosine',
      metadataConfiguration: { nonFilterableMetadataKeys: ['text', 'page', 'fileKey', 'fileName'] },
    });

    const indexDlq = new sqs.Queue(this, 'IndexDlq', { retentionPeriod: cdk.Duration.days(14) });
    const indexFn = new lambda.Function(this, 'IndexLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/indexer')),
      layers: [pyLayer],
      environment: {
        ...lambdaEnv,
        VECTOR_BUCKET: vectorBucket.vectorBucketName!,
        VECTOR_INDEX: VECTOR_INDEX_NAME,
      },
      timeout: cdk.Duration.minutes(5),
      memorySize: 512,
      retryAttempts: 2,
      deadLetterQueue: indexDlq,
    });
    indexFn.node.addDependency(vectorIndex);

    appBucket.grantRead(indexFn, 'text/*');
    // Key-pattern grants still put s3:List* on the bucket ARN; that is required so a missing
    // manifest returns NoSuchKey rather than AccessDenied, and it cannot be prefix-scoped.
    appBucket.grantReadWrite(indexFn, 'manifests/*');
    entriesTable.grant(indexFn, 'dynamodb:UpdateItem');
    indexFn.addToRolePolicy(new iam.PolicyStatement({
      // List/GetVectors are only needed by scripts/delete_document_vectors.py (operator creds).
      actions: ['s3vectors:PutVectors', 's3vectors:DeleteVectors'],
      resources: [vectorIndex.attrIndexArn],
    }));
    indexFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [EMBED_MODEL_ARN],
    }));

    // ParseLambda writes text/{docId}.json → IndexLambda. IndexLambda never writes text/.
    appBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(indexFn),
      { prefix: 'text/' },
    );

    // ── API Gateway ───────────────────────────────────────────
    const api = new apigw.RestApi(this, 'FinanceApi', {
      restApiName: 'finance-api',
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization', 'X-Session-Id'],
      },
    });

    const apiRoot = api.root.addResource('api');

    // POST /api/upload → parseFn
    const uploadRes = apiRoot.addResource('upload');
    uploadRes.addMethod('POST', new apigw.LambdaIntegration(parseFn));

    // /api/entries
    const entriesRes = apiRoot.addResource('entries');
    entriesRes.addMethod('GET', new apigw.LambdaIntegration(queryFn));
    entriesRes.addMethod('POST', new apigw.LambdaIntegration(manualEntryFn));

    const entryRes = entriesRes.addResource('{id}');
    entryRes.addMethod('PUT', new apigw.LambdaIntegration(manualEntryFn));
    entryRes.addMethod('DELETE', new apigw.LambdaIntegration(manualEntryFn));

    entryRes.addResource('evidence').addMethod('GET', new apigw.LambdaIntegration(queryFn));

    const confirmRes = entryRes.addResource('confirm');
    confirmRes.addMethod('PUT', new apigw.LambdaIntegration(confirmFn));

    // /api/accounts
    const accountsRes = apiRoot.addResource('accounts');
    accountsRes.addMethod('GET', new apigw.LambdaIntegration(queryFn));
    accountsRes.addMethod('POST', new apigw.LambdaIntegration(manualEntryFn));

    // /api/tags
    const tagsRes = apiRoot.addResource('tags');
    tagsRes.addMethod('GET', new apigw.LambdaIntegration(queryFn));

    // /api/budgets
    const budgetsRes = apiRoot.addResource('budgets');
    budgetsRes.addMethod('GET', new apigw.LambdaIntegration(queryFn));
    budgetsRes.addMethod('POST', new apigw.LambdaIntegration(manualEntryFn));
    const budgetById = budgetsRes.addResource('{accountId}');
    budgetById.addMethod('DELETE', new apigw.LambdaIntegration(manualEntryFn));

    // /api/alerts
    apiRoot.addResource('alerts').addMethod('GET', new apigw.LambdaIntegration(queryFn));

    // /api/advisor
    const advisorFn = new lambda.Function(this, 'AdvisorLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/advisor')),
      layers: [pyLayer],
      environment: lambdaEnv,
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
    });

    accountsTable.grantReadData(advisorFn);
    entriesTable.grantReadData(advisorFn);
    linesTable.grantReadData(advisorFn);
    advisorFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: ['*'],
    }));
    advisorFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['aws-marketplace:ViewSubscriptions', 'aws-marketplace:Subscribe', 'aws-marketplace:Unsubscribe'],
      resources: ['*'],
    }));

    const advisorIntegration = new apigw.LambdaIntegration(advisorFn);
    const advisor = apiRoot.addResource('advisor');
    advisor.addMethod('POST', advisorIntegration);

    // ── ExchangeRateLambda ────────────────────────────────────
    const exchangeRateFn = new lambda.Function(this, 'ExchangeRateLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/exchange-rate')),
      layers: [pyLayer],
      environment: { ...lambdaEnv },
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
    });
    exchangeRateFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: ['*'],
    }));
    exchangeRates.grantReadWriteData(exchangeRateFn);

    // ── MonthlyReportLambda ───────────────────────────────────
    const monthlyReportFn = new lambda.Function(this, 'MonthlyReportLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/monthly-report')),
      layers: [pyLayer],
      environment: { ...lambdaEnv, SNS_TOPIC_ARN: alertTopic.topicArn },
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
    });
    entriesTable.grantReadData(monthlyReportFn);
    linesTable.grantReadData(monthlyReportFn);
    budgetsTable.grantReadData(monthlyReportFn);
    monthlyCache.grantReadWriteData(monthlyReportFn);
    alertTopic.grantPublish(monthlyReportFn);

    // ── PushNotificationLambda ────────────────────────────────
    const pushNotificationFn = new lambda.Function(this, 'PushNotificationLambda', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/push-notification')),
      layers: [pyLayer],
      environment: { ...lambdaEnv, VAPID_CLAIMS_EMAIL: 'mailto:jia.jiax@northeastern.edu' },
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
    });
    pushSubscriptions.grantReadWriteData(pushNotificationFn);
    pushNotificationFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: ['*'],
    }));
    alertTopic.addSubscription(new snsSubscriptions.LambdaSubscription(pushNotificationFn));

    // ── EventBridge Rules ─────────────────────────────────────
    // Weekly Monday 00:00 UTC — fetch fresh exchange rates
    new events.Rule(this, 'WeeklyExchangeRate', {
      schedule: events.Schedule.cron({ minute: '0', hour: '0', weekDay: 'MON' }),
      targets: [new targets.LambdaFunction(exchangeRateFn)],
    });

    // Monthly 1st at 01:00 UTC — pre-compute P&L and check budgets
    new events.Rule(this, 'MonthlyReport', {
      schedule: events.Schedule.cron({ minute: '0', hour: '1', day: '1' }),
      targets: [new targets.LambdaFunction(monthlyReportFn)],
    });

    // /api/summary, /api/reports/*, /api/export/csv
    apiRoot.addResource('summary').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    const reportsRes = apiRoot.addResource('reports');
    reportsRes.addResource('income-statement').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    reportsRes.addResource('balance-sheet').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    reportsRes.addResource('net-worth').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    apiRoot.addResource('export').addResource('csv').addMethod('GET', new apigw.LambdaIntegration(exportFn));

    // /api/exchange-rates — served by queryFn
    apiRoot.addResource('exchange-rates').addMethod('GET', new apigw.LambdaIntegration(queryFn));
    exchangeRates.grantReadData(queryFn);
    monthlyCache.grantReadData(queryFn);

    // /api/push/subscribe — POST to subscribe, DELETE to unsubscribe
    const pushRes = apiRoot.addResource('push');
    const subscribeRes = pushRes.addResource('subscribe');
    subscribeRes.addMethod('POST',   new apigw.LambdaIntegration(manualEntryFn));
    subscribeRes.addMethod('DELETE', new apigw.LambdaIntegration(manualEntryFn));
    pushSubscriptions.grantReadWriteData(manualEntryFn);

    // ── CloudFront ────────────────────────────────────────────
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(appBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.RestApiOrigin(api),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
      defaultRootObject: 'index.html',
      errorResponses: [{ httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' }],
    });

    // ── Outputs ───────────────────────────────────────────────
    new cdk.CfnOutput(this, 'SiteUrl', { value: `https://${distribution.distributionDomainName}` });
    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
    new cdk.CfnOutput(this, 'BucketName', { value: appBucket.bucketName });
    new cdk.CfnOutput(this, 'DistributionId', { value: distribution.distributionId });
    new cdk.CfnOutput(this, 'VectorBucketName', { value: vectorBucket.vectorBucketName! });
    new cdk.CfnOutput(this, 'IndexDlqUrl', { value: indexDlq.queueUrl });
  }
}
