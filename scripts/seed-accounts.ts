import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
import { DynamoDBDocumentClient, PutCommand } from '@aws-sdk/lib-dynamodb';

const client = DynamoDBDocumentClient.from(new DynamoDBClient({ region: process.env.AWS_REGION ?? 'us-east-1' }));
const TABLE = process.env.ACCOUNTS_TABLE ?? 'finance-accounts';

const accounts = [
  { accountId: '1000', name: 'Assets', type: 'ASSET', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '1100', name: 'Bank Accounts', type: 'ASSET', parentId: '1000', isSystem: true, currency: 'CNY' },
  { accountId: '1200', name: 'Cash / Alipay / WeChat Pay', type: 'ASSET', parentId: '1000', isSystem: true, currency: 'CNY' },
  { accountId: '1300', name: 'Investments', type: 'ASSET', parentId: '1000', isSystem: true, currency: 'CNY' },
  { accountId: '2000', name: 'Liabilities', type: 'LIABILITY', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '2100', name: 'Credit Cards', type: 'LIABILITY', parentId: '2000', isSystem: true, currency: 'CNY' },
  { accountId: '2200', name: 'Loans', type: 'LIABILITY', parentId: '2000', isSystem: true, currency: 'CNY' },
  { accountId: '3000', name: 'Equity', type: 'EQUITY', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '3100', name: 'Opening Equity', type: 'EQUITY', parentId: '3000', isSystem: true, currency: 'CNY' },
  { accountId: '4000', name: 'Income', type: 'INCOME', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '4100', name: 'Salary', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '4200', name: 'Side Income', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '4300', name: 'Investment Returns', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '4400', name: 'Other Income', type: 'INCOME', parentId: '4000', isSystem: true, currency: 'CNY' },
  { accountId: '5000', name: 'Expenses', type: 'EXPENSE', parentId: null, isSystem: true, currency: 'CNY' },
  { accountId: '5100', name: 'Dining', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5200', name: 'Transportation', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5300', name: 'Shopping', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5400', name: 'Housing', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5500', name: 'Healthcare', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5600', name: 'Entertainment', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5700', name: 'Education', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
  { accountId: '5800', name: 'Other Expenses', type: 'EXPENSE', parentId: '5000', isSystem: true, currency: 'CNY' },
];

async function seed() {
  console.log(`Seeding ${accounts.length} accounts to table: ${TABLE}`);
  for (const account of accounts) {
    await client.send(new PutCommand({ TableName: TABLE, Item: account }));
    console.log(`  ✓ ${account.accountId} ${account.name}`);
  }
  console.log('Done.');
}

seed().catch(console.error);
