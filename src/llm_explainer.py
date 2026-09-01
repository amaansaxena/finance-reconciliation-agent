"""
llm_explainer.py

LLM reasoning layer for the reconciliation pipeline.

Scope (deliberately narrow):
  - Runs ONLY on matched cases that involved ambiguity: fee_deduction,
    date_lag, partial_settlement. Clean exact_match cases don't need
    explaining, and exceptions are already final per the rule engine.
  - The LLM NEVER makes or changes a match decision. It only generates a
    human-readable explanation, which is then programmatically validated
    against the actual numbers before being accepted.
  - If validation fails, the case is escalated to llm_review_needed.json
    instead of trusting the LLM's explanation blindly.

Usage:
  Dry run (no API key needed, uses a mock explainer to test the pipeline):
    python src/llm_explainer.py --dry-run

  Real run (requires GEMINI_API_KEY in .env):
    python src/llm_explainer.py

Outputs:
  - llm_audit_log.json     : every call's input, raw output, validation result
  - llm_review_needed.json : explanations that failed validation
  - llm_metrics.json       : explanation accuracy summary
"""

import os
import csv
import json
import time
import argparse
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Reason codes eligible for LLM explanation - clean matches don't need it,
# exceptions are already final.
EXPLAIN_REASONS = {"fee_deduction", "date_lag", "partial_settlement"}

VALIDATION_TOLERANCE = 0.5  # rupees


def load_matches():
    with open(DATA_DIR / "matches.csv") as f:
        return list(csv.DictReader(f))


def close(a, b, tol=VALIDATION_TOLERANCE):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def build_prompt(match_row):
    """Builds the structured prompt sent to the LLM for one ambiguous match."""
    reason = match_row["reason"]
    return f"""You are a financial reconciliation assistant. You are given a transaction
match that a deterministic rule engine already classified as "{reason}".
Your job is ONLY to explain why this classification makes sense, using the
numbers provided. Do not question or override the classification.

Transaction data:
{json.dumps(match_row, indent=2)}

Respond with ONLY a JSON object, no other text, in this exact schema:
{{
  "explanation": "<one sentence explaining the numeric relationship>",
  "flagged_reason_code": "fee_deduction" | "date_lag" | "partial_settlement" | "other",
  "confidence_in_explanation": <integer 0-100>
}}"""


def call_gemini(prompt, api_key, model="gemini-3.5-flash-lite"):
    """Real Gemini API call using the current google-genai SDK."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    return json.loads(response.text)


def call_gemini_with_retry(prompt, api_key, max_retries=3, base_delay=15):
    """
    Wraps call_gemini with retry-with-backoff for transient rate-limit (429)
    errors, so one throttled call doesn't kill the whole batch run.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return call_gemini(prompt, api_key)
        except Exception as e:
            last_error = e
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                delay = base_delay * (attempt + 1)
                print(f"    Rate limited, waiting {delay}s before retry ({attempt + 1}/{max_retries})...", flush=True)
                time.sleep(delay)
            else:
                raise
    raise last_error


def call_mock(match_row):
    """
    Mock explainer for --dry-run mode. Deterministically generates a plausible
    explanation from the actual numbers, so we can test the validation logic
    end-to-end without hitting a real API. Occasionally injects a wrong
    explanation to prove the validation layer actually catches bad output.
    """
    reason = match_row["reason"]
    pid = match_row["payment_id"]

    # inject a deliberately wrong explanation for ~1 in 15 cases to prove
    # the validator isn't a no-op
    inject_error = (int(pid.split("_")[-1]) % 15 == 0)

    if reason == "fee_deduction":
        settlement = match_row.get("settlement_amount", "0")
        bank = match_row.get("bank_amount", "0")
        expl = f"Bank amount {bank} is settlement amount {settlement} minus fee and tax."
        flagged = "fee_deduction" if not inject_error else "date_lag"
    elif reason == "date_lag":
        lag = match_row.get("lag_days", "?")
        expl = f"Bank credit landed {lag} day(s) after the settlement date, a normal processing lag."
        flagged = "date_lag" if not inject_error else "fee_deduction"
    elif reason == "partial_settlement":
        num_rows = match_row.get("num_bank_rows", "?")
        total = match_row.get("bank_amount_total", "0")
        expl = f"Settlement was split across {num_rows} bank credits summing to {total}."
        flagged = "partial_settlement" if not inject_error else "other"
    else:
        expl = "Unrecognized case."
        flagged = "other"

    return {
        "explanation": expl,
        "flagged_reason_code": flagged,
        "confidence_in_explanation": 90 if not inject_error else 55
    }


def validate_explanation(match_row, llm_output):
    """
    Programmatic sanity check: does the LLM's flagged_reason_code match what
    the rule engine actually assigned? This is the core "don't trust the LLM
    blindly" mechanism - a mismatch means the explanation is rejected,
    regardless of how confident or well-written it sounds.
    """
    true_reason = match_row["reason"]
    flagged = llm_output.get("flagged_reason_code")

    reason_code_matches = (flagged == true_reason)

    # secondary numeric sanity check depending on case type
    numeric_check_passed = True
    detail = ""
    if true_reason == "fee_deduction":
        expected_net = match_row.get("expected_net")
        bank_amount = match_row.get("bank_amount")
        if expected_net and bank_amount:
            numeric_check_passed = close(expected_net, bank_amount)
            detail = f"expected_net={expected_net} vs bank_amount={bank_amount}"
    elif true_reason == "date_lag":
        lag = match_row.get("lag_days")
        numeric_check_passed = str(lag) in ("1", "2")
        detail = f"lag_days={lag}"
    elif true_reason == "partial_settlement":
        total = match_row.get("bank_amount_total")
        settlement = match_row.get("settlement_amount")
        if total and settlement:
            numeric_check_passed = close(total, settlement, tol=1.0)
            detail = f"bank_amount_total={total} vs settlement_amount={settlement}"

    passed = reason_code_matches and numeric_check_passed
    return passed, {
        "reason_code_matches": reason_code_matches,
        "numeric_check_passed": numeric_check_passed,
        "detail": detail
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                         help="Use mock explainer instead of calling the real Gemini API")
    args = parser.parse_args()

    matches = load_matches()
    eligible = [m for m in matches if m["reason"] in EXPLAIN_REASONS]

    print(f"Total matches: {len(matches)}")
    print(f"Eligible for LLM explanation: {len(eligible)}")
    print(f"Mode: {'DRY RUN (mock explainer)' if args.dry_run else 'LIVE (Gemini API)'}\n")

    api_key = None
    if not args.dry_run:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("ERROR: GEMINI_API_KEY not found in environment.")
            print("Add it to a .env file or export it, or run with --dry-run to test first.")
            return

    audit_log = []
    review_needed = []
    accepted_count = 0

    for i, match_row in enumerate(eligible, 1):
        prompt = build_prompt(match_row)
        print(f"  [{i}/{len(eligible)}] Processing {match_row['payment_id']} ({match_row['reason']})...", flush=True)

        try:
            if args.dry_run:
                llm_output = call_mock(match_row)
            else:
                llm_output = call_gemini_with_retry(prompt, api_key)
        except Exception as e:
            audit_log.append({
                "payment_id": match_row["payment_id"],
                "error": str(e),
                "status": "api_call_failed"
            })
            review_needed.append({
                "payment_id": match_row["payment_id"],
                "reason": "api_call_failed",
                "detail": str(e)
            })
            continue

        passed, validation_detail = validate_explanation(match_row, llm_output)

        audit_entry = {
            "payment_id": match_row["payment_id"],
            "true_reason": match_row["reason"],
            "llm_output": llm_output,
            "validation": validation_detail,
            "status": "accepted" if passed else "rejected"
        }
        audit_log.append(audit_entry)

        if passed:
            accepted_count += 1
        else:
            review_needed.append({
                "payment_id": match_row["payment_id"],
                "reason": "llm_validation_failed",
                "detail": validation_detail,
                "llm_explanation": llm_output.get("explanation")
            })

        if not args.dry_run:
            time.sleep(3)  # stay well under free-tier per-minute rate limits

    accuracy_pct = round(accepted_count / len(eligible) * 100, 2) if eligible else 0

    with open(DATA_DIR / "llm_audit_log.json", "w") as f:
        json.dump(audit_log, f, indent=2)
    with open(DATA_DIR / "llm_review_needed.json", "w") as f:
        json.dump(review_needed, f, indent=2)

    llm_metrics = {
        "eligible_for_explanation": len(eligible),
        "accepted": accepted_count,
        "rejected_or_failed": len(eligible) - accepted_count,
        "llm_explanation_accuracy_pct": accuracy_pct
    }
    with open(DATA_DIR / "llm_metrics.json", "w") as f:
        json.dump(llm_metrics, f, indent=2)

    print("=== LLM EXPLANATION LAYER SUMMARY ===")
    print(f"Eligible cases:           {len(eligible)}")
    print(f"Accepted (validated):     {accepted_count}")
    print(f"Rejected/escalated:       {len(eligible) - accepted_count}")
    print(f"Explanation accuracy:     {accuracy_pct}%")
    print(f"\nFiles written: llm_audit_log.json, llm_review_needed.json, llm_metrics.json -> {DATA_DIR}")


if __name__ == "__main__":
    main()