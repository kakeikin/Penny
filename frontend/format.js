// The ledger's base currency is USD: every page formats money through Money.fmt.
const Money = {
  symbol: '$',
  fmt(v) {
    const n = Number(v);
    const abs = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? '-' : '') + Money.symbol + abs;      // -$12.00, not $-12.00
  },
};
