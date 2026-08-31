"""
generate_data.py

Generates 3 synthetic CSVs simulating a real finance-ops reconciliation batch:
  - razorpay_settlements.csv
  - bank_statement.csv
  - internal_ledger.csv

The noise model (case counts and logic) is documented in docs/noise_model.md.
Uses a fixed random seed so results are reproducible.
"""

import random
import string
import csv
from datetime import date, timedelta
from pathlib import Path
from faker import Faker

SEED = 42
random.seed(SEED)
fake = Faker()
Faker.seed(SEED)

OUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR.mkdir(exist_ok=True)

BASE_DATE = date(2026, 8, 1)

FEE_RATE = 0.02      # 2% platform fee
TAX_RATE = 0.18      # 18% GST on fee


def make_payment_id(i):
    return f"pay_{i:05d}"


def make_utr(i):
    return "UTR" + "".join(random.choices(string.digits, k=10)) + f"{i:03d}"


def make_invoice_id(i):
    return f"INV-{2026}-{i:05d}"


def random_amount():
    return round(random.uniform(500, 75000), 2)


def compute_fee_tax(amount):
    fee = round(amount * FEE_RATE, 2)
    tax = round(fee * TAX_RATE, 2)
    return fee, tax


def random_settlement_date():
    offset = random.randint(0, 25)
    return BASE_DATE + timedelta(days=offset)


def build_records():
    settlements = []
    bank_rows = []
    ledger_rows = []

    idx = 1

    def new_clean_case():
        nonlocal idx
        amount = random_amount()
        fee, tax = compute_fee_tax(amount)
        sdate = random_settlement_date()
        pid = make_payment_id(idx)
        utr = make_utr(idx)
        inv = make_invoice_id(idx)

        settlements.append({
            "payment_id": pid, "amount": amount, "fee": fee, "tax": tax,
            "settlement_date": sdate.isoformat(), "utr": utr
        })
        bank_rows.append({
            "utr": utr, "credited_amount": amount,
            "value_date": sdate.isoformat(),
            "narration": f"NEFT CR {fake.company().upper()} {utr}"
        })
        ledger_rows.append({
            "invoice_id": inv, "expected_amount": amount,
            "payment_id": pid, "status": "open"
        })
        idx += 1

    def new_partial_settlement_case():
        nonlocal idx
        amount = random_amount()
        fee, tax = compute_fee_tax(amount)
        sdate = random_settlement_date()
        pid = make_payment_id(idx)
        utr = make_utr(idx)
        inv = make_invoice_id(idx)

        split1 = round(amount * random.uniform(0.3, 0.6), 2)
        split2 = round(amount - split1, 2)

        settlements.append({
            "payment_id": pid, "amount": amount, "fee": fee, "tax": tax,
            "settlement_date": sdate.isoformat(), "utr": utr
        })
        bank_rows.append({
            "utr": utr, "credited_amount": split1,
            "value_date": sdate.isoformat(),
            "narration": f"NEFT CR PARTIAL 1 {utr}"
        })
        bank_rows.append({
            "utr": utr, "credited_amount": split2,
            "value_date": (sdate + timedelta(days=1)).isoformat(),
            "narration": f"NEFT CR PARTIAL 2 {utr}"
        })
        ledger_rows.append({
            "invoice_id": inv, "expected_amount": amount,
            "payment_id": pid, "status": "open"
        })
        idx += 1

    def new_fee_adjusted_case():
        nonlocal idx
        amount = random_amount()
        fee, tax = compute_fee_tax(amount)
        net_credited = round(amount - fee - tax, 2)
        sdate = random_settlement_date()
        pid = make_payment_id(idx)
        utr = make_utr(idx)
        inv = make_invoice_id(idx)

        settlements.append({
            "payment_id": pid, "amount": amount, "fee": fee, "tax": tax,
            "settlement_date": sdate.isoformat(), "utr": utr
        })
        bank_rows.append({
            "utr": utr, "credited_amount": net_credited,
            "value_date": sdate.isoformat(),
            "narration": f"NEFT CR NET {utr}"
        })
        ledger_rows.append({
            "invoice_id": inv, "expected_amount": amount,
            "payment_id": pid, "status": "open"
        })
        idx += 1

    def new_date_lagged_case():
        nonlocal idx
        amount = random_amount()
        fee, tax = compute_fee_tax(amount)
        sdate = random_settlement_date()
        lag = random.choice([1, 2])
        pid = make_payment_id(idx)
        utr = make_utr(idx)
        inv = make_invoice_id(idx)

        settlements.append({
            "payment_id": pid, "amount": amount, "fee": fee, "tax": tax,
            "settlement_date": sdate.isoformat(), "utr": utr
        })
        bank_rows.append({
            "utr": utr, "credited_amount": amount,
            "value_date": (sdate + timedelta(days=lag)).isoformat(),
            "narration": f"NEFT CR LAGGED {utr}"
        })
        ledger_rows.append({
            "invoice_id": inv, "expected_amount": amount,
            "payment_id": pid, "status": "open"
        })
        idx += 1

    def new_duplicate_utr_case():
        nonlocal idx
        # two unrelated settlements sharing the same UTR (data quality issue)
        shared_utr = make_utr(idx)
        for _ in range(2):
            amount = random_amount()
            fee, tax = compute_fee_tax(amount)
            sdate = random_settlement_date()
            pid = make_payment_id(idx)
            inv = make_invoice_id(idx)

            settlements.append({
                "payment_id": pid, "amount": amount, "fee": fee, "tax": tax,
                "settlement_date": sdate.isoformat(), "utr": shared_utr
            })
            ledger_rows.append({
                "invoice_id": inv, "expected_amount": amount,
                "payment_id": pid, "status": "open"
            })
            idx += 1
        # only one bank credit exists for the shared UTR -> ambiguous
        bank_rows.append({
            "utr": shared_utr, "credited_amount": random_amount(),
            "value_date": random_settlement_date().isoformat(),
            "narration": f"NEFT CR AMBIGUOUS {shared_utr}"
        })

    def new_missing_bank_entry_case():
        nonlocal idx
        amount = random_amount()
        fee, tax = compute_fee_tax(amount)
        sdate = random_settlement_date()
        pid = make_payment_id(idx)
        utr = make_utr(idx)
        inv = make_invoice_id(idx)

        settlements.append({
            "payment_id": pid, "amount": amount, "fee": fee, "tax": tax,
            "settlement_date": sdate.isoformat(), "utr": utr
        })
        # no bank row created at all
        ledger_rows.append({
            "invoice_id": inv, "expected_amount": amount,
            "payment_id": pid, "status": "open"
        })
        idx += 1

    def new_missing_ledger_entry_case():
        nonlocal idx
        amount = random_amount()
        fee, tax = compute_fee_tax(amount)
        sdate = random_settlement_date()
        pid = make_payment_id(idx)
        utr = make_utr(idx)

        settlements.append({
            "payment_id": pid, "amount": amount, "fee": fee, "tax": tax,
            "settlement_date": sdate.isoformat(), "utr": utr
        })
        bank_rows.append({
            "utr": utr, "credited_amount": amount,
            "value_date": sdate.isoformat(),
            "narration": f"NEFT CR {utr}"
        })
        # no ledger row created at all
        idx += 1

    # Build the batch per the documented composition (docs/noise_model.md)
    # Scaled ~4.3x from the original 70-case pilot batch, same proportions,
    # to strengthen statistical significance of match-rate / precision metrics.
    for _ in range(160):
        new_clean_case()
    for _ in range(34):
        new_partial_settlement_case()
    for _ in range(34):
        new_fee_adjusted_case()
    for _ in range(26):
        new_date_lagged_case()
    for _ in range(17):
        new_duplicate_utr_case()
    for _ in range(13):
        new_missing_bank_entry_case()
    for _ in range(13):
        new_missing_ledger_entry_case()

    return settlements, bank_rows, ledger_rows


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    settlements, bank_rows, ledger_rows = build_records()

    write_csv(OUT_DIR / "razorpay_settlements.csv", settlements,
               ["payment_id", "amount", "fee", "tax", "settlement_date", "utr"])
    write_csv(OUT_DIR / "bank_statement.csv", bank_rows,
               ["utr", "credited_amount", "value_date", "narration"])
    write_csv(OUT_DIR / "internal_ledger.csv", ledger_rows,
               ["invoice_id", "expected_amount", "payment_id", "status"])

    print(f"Generated {len(settlements)} settlement records")
    print(f"Generated {len(bank_rows)} bank statement rows")
    print(f"Generated {len(ledger_rows)} ledger rows")
    print(f"Files written to: {OUT_DIR}")


if __name__ == "__main__":
    main()