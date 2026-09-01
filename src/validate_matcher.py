"""
validate_matcher.py

Compares the matcher's actual output (matches.csv, exceptions.json) against
the ground truth answer key (ground_truth.csv, generated alongside the data
but never fed to the matcher itself).

Produces:
  - per-category accuracy (did each true case type land where it should?)
  - a confusion matrix (true_case_type vs matcher's actual outcome)
  - flags any misclassified records by payment_id for manual inspection

This is the honest per-category accuracy check - an aggregate match rate can
hide poor performance on a specific case type. Run this after matcher.py.
"""

import csv
import json
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Maps each true case type to the outcome (reason code) the matcher SHOULD
# produce if it's working correctly. Some true types can validly resolve to
# either a match reason or an exception reason (see note below).
EXPECTED_OUTCOME = {
    "clean_match": {"exact_match"},
    "partial_settlement": {"partial_settlement"},
    "fee_deduction": {"fee_deduction"},
    "date_lag": {"date_lag"},
    "ambiguous_duplicate_utr": {"ambiguous_duplicate_utr"},
    "no_bank_entry": {"no_bank_entry"},
    # no_ledger_entry is expected to ALSO succeed at the bank-match level
    # (exact_match, fee_deduction, or date_lag) while being flagged as an
    # exception separately - so we check it in two passes below.
    "no_ledger_entry": {"exact_match", "fee_deduction", "date_lag", "partial_settlement"},
}


def load_ground_truth():
    with open(DATA_DIR / "ground_truth.csv") as f:
        return {row["payment_id"]: row["true_case_type"] for row in csv.DictReader(f)}


def load_matches():
    with open(DATA_DIR / "matches.csv") as f:
        return {row["payment_id"]: row["reason"] for row in csv.DictReader(f)}


def load_exceptions():
    with open(DATA_DIR / "exceptions.json") as f:
        exceptions = json.load(f)
    # a payment_id can appear in exceptions more than once conceptually,
    # but in this pipeline each pid gets at most one exception entry except
    # no_ledger_entry which also has a matches.csv row - track as a set of reasons
    result = defaultdict(list)
    for e in exceptions:
        result[e["payment_id"]].append(e["reason"])
    return result


def validate():
    ground_truth = load_ground_truth()
    matches = load_matches()
    exceptions = load_exceptions()

    confusion = defaultdict(lambda: defaultdict(int))
    correct_by_type = defaultdict(int)
    total_by_type = defaultdict(int)
    misclassified = []

    for pid, true_type in ground_truth.items():
        total_by_type[true_type] += 1

        actual_outcomes = set()
        if pid in matches:
            actual_outcomes.add(matches[pid])
        if pid in exceptions:
            actual_outcomes.update(exceptions[pid])

        if not actual_outcomes:
            actual_outcomes.add("MISSING_FROM_OUTPUT")

        expected = EXPECTED_OUTCOME[true_type]

        # special case: no_ledger_entry must have BOTH a bank-level match
        # reason AND the no_ledger_entry exception reason present
        if true_type == "no_ledger_entry":
            has_bank_match = bool(actual_outcomes & expected)
            has_ledger_flag = "no_ledger_entry" in actual_outcomes
            is_correct = has_bank_match and has_ledger_flag
        else:
            is_correct = bool(actual_outcomes & expected)

        for outcome in actual_outcomes:
            confusion[true_type][outcome] += 1

        if is_correct:
            correct_by_type[true_type] += 1
        else:
            misclassified.append({
                "payment_id": pid,
                "true_case_type": true_type,
                "actual_outcomes": sorted(actual_outcomes)
            })

    return confusion, correct_by_type, total_by_type, misclassified


def print_report(confusion, correct_by_type, total_by_type, misclassified):
    print("=== PER-CATEGORY ACCURACY (matcher vs ground truth) ===\n")
    overall_correct = 0
    overall_total = 0
    for true_type in sorted(total_by_type):
        correct = correct_by_type[true_type]
        total = total_by_type[true_type]
        pct = round(correct / total * 100, 1) if total else 0
        overall_correct += correct
        overall_total += total
        flag = "" if pct == 100.0 else "  <-- CHECK THIS"
        print(f"  {true_type:28s}: {correct:3d}/{total:3d}  ({pct:5.1f}%){flag}")

    overall_pct = round(overall_correct / overall_total * 100, 2) if overall_total else 0
    print(f"\n  {'OVERALL':28s}: {overall_correct:3d}/{overall_total:3d}  ({overall_pct}%)\n")

    print("=== CONFUSION MATRIX (true_case_type -> actual outcome(s)) ===\n")
    for true_type in sorted(confusion):
        print(f"  {true_type}:")
        for outcome, count in sorted(confusion[true_type].items(), key=lambda x: -x[1]):
            print(f"      -> {outcome:28s}: {count}")

    print()
    if misclassified:
        print(f"=== MISCLASSIFIED RECORDS ({len(misclassified)}) ===\n")
        for m in misclassified[:20]:
            print(f"  {m['payment_id']}: expected reason for '{m['true_case_type']}', "
                  f"got {m['actual_outcomes']}")
        if len(misclassified) > 20:
            print(f"  ... and {len(misclassified) - 20} more")
    else:
        print("=== NO MISCLASSIFICATIONS - matcher is 100% accurate on this batch ===")


def main():
    confusion, correct_by_type, total_by_type, misclassified = validate()
    print_report(confusion, correct_by_type, total_by_type, misclassified)

    # also write a machine-readable report
    report = {
        "per_category_accuracy": {
            t: {"correct": correct_by_type[t], "total": total_by_type[t]}
            for t in total_by_type
        },
        "misclassified_count": len(misclassified),
        "misclassified_records": misclassified
    }
    with open(DATA_DIR / "validation_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report written to: {DATA_DIR / 'validation_report.json'}")


if __name__ == "__main__":
    main()
