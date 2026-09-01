"""
dashboard_data.py

Consolidated data-loading layer for the Streamlit dashboard. Reads every
output file produced by the pipeline (matcher, validator, LLM explainer)
so dashboard.py doesn't repeat file-loading logic across sections.

All loaders return empty/default structures if a file doesn't exist yet,
so the dashboard doesn't crash if you haven't run every stage.
"""

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_csv(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _load_json(filename, default=None):
    path = DATA_DIR / filename
    if not path.exists():
        return default if default is not None else {}
    with open(path) as f:
        return json.load(f)


def load_settlements():
    return _load_csv("razorpay_settlements.csv")


def load_matches():
    return _load_csv("matches.csv")


def load_exceptions():
    return _load_json("exceptions.json", default=[])


def load_metrics():
    return _load_json("metrics.json", default={})


def load_validation_report():
    return _load_json("validation_report.json", default={})


def load_llm_audit_log():
    return _load_json("llm_audit_log.json", default=[])


def load_llm_review_needed():
    return _load_json("llm_review_needed.json", default=[])


def load_llm_metrics():
    return _load_json("llm_metrics.json", default={})


def get_all_data():
    """Single call to load everything the dashboard needs."""
    return {
        "settlements": load_settlements(),
        "matches": load_matches(),
        "exceptions": load_exceptions(),
        "metrics": load_metrics(),
        "validation_report": load_validation_report(),
        "llm_audit_log": load_llm_audit_log(),
        "llm_review_needed": load_llm_review_needed(),
        "llm_metrics": load_llm_metrics(),
    }
