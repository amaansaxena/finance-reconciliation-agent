import json
import csv
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR = Path(__file__).resolve().parent.parent / "data"

FEE_TAX_TOLERANCE = 0.5   # rupees, rounding slack when checking fee-adjusted amounts
AMOUNT_TOLERANCE = 0.5    # rupees, general float rounding slack


def load_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_data():
    settlements = load_csv(DATA_DIR / "razorpay_settlements.csv")
    bank = load_csv(DATA_DIR / "bank_statement.csv")
    ledger = load_csv(DATA_DIR / "internal_ledger.csv")

    for s in settlements:
        s["amount"] = float(s["amount"])
        s["fee"] = float(s["fee"])
        s["tax"] = float(s["tax"])
        s["settlement_date"] = parse_date(s["settlement_date"])

    for b in bank:
        b["credited_amount"] = float(b["credited_amount"])
        b["value_date"] = parse_date(b["value_date"])

    for l in ledger:
        l["expected_amount"] = float(l["expected_amount"])

    return settlements, bank, ledger


def close(a, b, tol=AMOUNT_TOLERANCE):
    return abs(a - b) <= tol


def run_matcher(settlements, bank, ledger):
    # index bank rows and ledger rows by utr / payment_id for fast lookup
    bank_by_utr = defaultdict(list)
    for b in bank:
        bank_by_utr[b["utr"]].append(b)

    ledger_by_payment_id = {l["payment_id"]: l for l in ledger}

    # detect UTRs used by more than one settlement (duplicate UTR case)
    settlements_by_utr = defaultdict(list)
    for s in settlements:
        settlements_by_utr[s["utr"]].append(s)

    matches = []
    exceptions = []

    for s in settlements:
        utr = s["utr"]
        pid = s["payment_id"]
        ledger_row = ledger_by_payment_id.get(pid)

        # --- Case: duplicate UTR shared by multiple settlements ---
        if len(settlements_by_utr[utr]) > 1:
            exceptions.append({
                "payment_id": pid,
                "utr": utr,
                "reason": "ambiguous_duplicate_utr",
                "detail": f"UTR shared by {len(settlements_by_utr[utr])} settlements; cannot disambiguate which bank credit belongs to which."
            })
            continue

        bank_rows = bank_by_utr.get(utr, [])

        # --- Case: no bank entry at all ---
        if not bank_rows:
            exceptions.append({
                "payment_id": pid,
                "utr": utr,
                "reason": "no_bank_entry",
                "detail": "Settlement has no corresponding bank statement row for this UTR."
            })
            continue

        resolved = False

        # --- Tier 1: exact match (single bank row, same amount, same date) ---
        if len(bank_rows) == 1:
            b = bank_rows[0]
            if close(b["credited_amount"], s["amount"]) and b["value_date"] == s["settlement_date"]:
                confidence = 100 if ledger_row else 90
                matches.append({
                    "payment_id": pid, "utr": utr,
                    "reason": "exact_match",
                    "confidence": confidence,
                    "settlement_amount": s["amount"],
                    "bank_amount": b["credited_amount"],
                    "ledger_confirmed": bool(ledger_row)
                })
                resolved = True

            # --- Tier 2: fee/tax-adjusted ---
            elif not resolved:
                expected_net = round(s["amount"] - s["fee"] - s["tax"], 2)
                if close(b["credited_amount"], expected_net, FEE_TAX_TOLERANCE):
                    matches.append({
                        "payment_id": pid, "utr": utr,
                        "reason": "fee_deduction",
                        "confidence": 85 if ledger_row else 75,
                        "settlement_amount": s["amount"],
                        "bank_amount": b["credited_amount"],
                        "expected_net": expected_net,
                        "ledger_confirmed": bool(ledger_row)
                    })
                    resolved = True

            # --- Tier 3: date-lagged (same amount, date +1/+2) ---
            if not resolved and close(b["credited_amount"], s["amount"]):
                lag = (b["value_date"] - s["settlement_date"]).days
                if lag in (1, 2):
                    matches.append({
                        "payment_id": pid, "utr": utr,
                        "reason": "date_lag",
                        "confidence": 80 if ledger_row else 70,
                        "settlement_amount": s["amount"],
                        "bank_amount": b["credited_amount"],
                        "lag_days": lag,
                        "ledger_confirmed": bool(ledger_row)
                    })
                    resolved = True

        # --- Tier 4: partial settlement (multiple bank rows summing to settlement amount) ---
        if not resolved and len(bank_rows) > 1:
            total_credited = round(sum(b["credited_amount"] for b in bank_rows), 2)
            if close(total_credited, s["amount"], tol=1.0):
                matches.append({
                    "payment_id": pid, "utr": utr,
                    "reason": "partial_settlement",
                    "confidence": 75 if ledger_row else 65,
                    "settlement_amount": s["amount"],
                    "bank_amount_total": total_credited,
                    "num_bank_rows": len(bank_rows),
                    "ledger_confirmed": bool(ledger_row)
                })
                resolved = True

        # --- Fallback: doesn't fit any known pattern ---
        if not resolved:
            exceptions.append({
                "payment_id": pid,
                "utr": utr,
                "reason": "unresolved",
                "detail": f"Settlement amount {s['amount']} does not match any bank row pattern within tolerance."
            })
            continue

        # --- Check ledger separately: matched settlement+bank but no ledger row ---
        if resolved and not ledger_row:
            exceptions.append({
                "payment_id": pid,
                "utr": utr,
                "reason": "no_ledger_entry",
                "detail": "Settlement and bank credit matched, but no internal ledger record exists for this payment_id."
            })

    return matches, exceptions


def compute_metrics(settlements, matches, exceptions):
    total = len(settlements)
    matched = len(matches)
    excepted = len(exceptions)

    reason_counts = defaultdict(int)
    for m in matches:
        reason_counts[m["reason"]] += 1
    exception_reason_counts = defaultdict(int)
    for e in exceptions:
        exception_reason_counts[e["reason"]] += 1

    avg_confidence = round(sum(m["confidence"] for m in matches) / matched, 2) if matched else 0

    return {
        "total_settlements": total,
        "matched_count": matched,
        "match_rate_pct": round(matched / total * 100, 2),
        "exception_count": excepted,
        "exception_rate_pct": round(excepted / total * 100, 2),
        "average_confidence": avg_confidence,
        "matches_by_reason": dict(reason_counts),
        "exceptions_by_reason": dict(exception_reason_counts),
    }


def write_matches_csv(path, matches):
    if not matches:
        return
    all_keys = set()
    for m in matches:
        all_keys.update(m.keys())
    fieldnames = ["payment_id", "utr", "reason", "confidence"] + sorted(
        k for k in all_keys if k not in ("payment_id", "utr", "reason", "confidence")
    )
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in matches:
            writer.writerow(m)


def main():
    settlements, bank, ledger = load_data()
    matches, exceptions = run_matcher(settlements, bank, ledger)
    metrics = compute_metrics(settlements, matches, exceptions)

    write_matches_csv(OUT_DIR / "matches.csv", matches)

    with open(OUT_DIR / "exceptions.json", "w") as f:
        json.dump(exceptions, f, indent=2, default=str)

    with open(OUT_DIR / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print("=== RECONCILIATION SUMMARY ===")
    print(f"Total settlements:   {metrics['total_settlements']}")
    print(f"Matched:             {metrics['matched_count']} ({metrics['match_rate_pct']}%)")
    print(f"Exceptions:          {metrics['exception_count']} ({metrics['exception_rate_pct']}%)")
    print(f"Avg match confidence:{metrics['average_confidence']}%")
    print()
    print("Matches by reason:")
    for reason, count in metrics["matches_by_reason"].items():
        print(f"  {reason:22s}: {count}")
    print()
    print("Exceptions by reason:")
    for reason, count in metrics["exceptions_by_reason"].items():
        print(f"  {reason:22s}: {count}")
    print()
    print(f"Files written: matches.csv, exceptions.json, metrics.json -> {OUT_DIR}")


if __name__ == "__main__":
    main()
