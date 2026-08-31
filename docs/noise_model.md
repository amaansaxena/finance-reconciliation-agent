# Synthetic Data & Noise Model

## Why synthetic data with injected noise
Real reconciliation is never clean 1:1 matching. To make this project defensible
(not a cherry-picked demo), the synthetic batch deliberately includes realistic
failure modes seen in real finance-ops: partial settlements, fee-adjusted amounts,
date lags, duplicate references, and genuine unmatched records.

## The three data sources
1. `razorpay_settlements.csv` — payment_id, amount, fee, tax, settlement_date, utr
2. `bank_statement.csv` — utr, credited_amount, value_date, narration
3. `internal_ledger.csv` — invoice_id, expected_amount, payment_id, status

## Batch composition (~314 total settlement records)
Scaled ~4.3x from an initial 70-record pilot batch (same proportions), to
strengthen the statistical significance of match-rate and precision/recall
metrics while keeping every failure mode's design intent unchanged.

| Case type                          | Count | Description |
|-------------------------------------|-------|--------------|
| Clean 1:1 match                     | 160   | Same amount, same/near date, single bank credit, ledger entry exists |
| Partial settlement (split payout)   | 34    | One settlement appears as 2 separate bank credits summing to the settlement amount |
| Fee/tax-adjusted amount             | 34    | Bank credited amount = settlement amount minus fee/tax, so raw amounts don't match exactly |
| Date-lagged settlement              | 26    | Bank credit lands T+1 or T+2 after settlement_date |
| Duplicate UTR                       | 17 pairs (34 settlements) | Same UTR used by two unrelated settlements (a real-world data quality issue) |
| Missing bank entry (exception)      | 13    | Settlement exists, no corresponding bank credit at all |
| Missing ledger entry (exception)    | 13    | Settlement and bank credit exist, but no internal ledger record |

This composition is fixed with a random seed so results are reproducible, and the
counts are documented here (not tuned after the fact) so match-rate results can't
be accused of being cherry-picked.

## What "resolved" vs "exception" means downstream
- Clean, partial, fee-adjusted, and date-lagged cases should all be resolvable by
  the matcher (with varying confidence).
- Duplicate UTR and missing-entry cases are genuine exceptions i.e. the matcher is
  expected to flag these, not force a match. A good pipeline recognizes these
  rather than resolving them incorrectly.