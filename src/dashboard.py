import sys
from pathlib import Path
from collections import Counter

import streamlit as st
import pandas as pd

from dashboard_data import get_all_data

SRC_DIR = Path(__file__).resolve().parent

st.set_page_config(
    page_title="AI Finance Controller",
    page_icon="\U0001F4CA",
    layout="wide"
)

st.title("AI Finance Controller")
st.caption(
    "Autonomous 3-way reconciliation agent matching Razorpay settlements, "
    "bank statements, and internal ledgers - with confidence-scored matching "
    "and an honest, auditable exception list."
)

data = get_all_data()
metrics = data["metrics"]
llm_metrics = data["llm_metrics"]

if not metrics:
    st.warning(
        "No pipeline output found yet. Click **Run pipeline now** in the "
        "sidebar, or run the scripts manually: `generate_data.py` -> "
        "`matcher.py` -> `validate_matcher.py`."
    )
    st.stop()

tab_overview, tab_matches, tab_exceptions, tab_llm = st.tabs(
    ["Overview", "Matches", "Exceptions", "LLM Explanations"]
)

# OVERVIEW TAB 
with tab_overview:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Settlements", metrics.get("total_settlements", 0))
    col2.metric("Match Rate", f"{metrics.get('match_rate_pct', 0)}%")
    col3.metric("Exception Rate", f"{metrics.get('exception_rate_pct', 0)}%")
    col4.metric(
        "LLM Explanation Accuracy",
        f"{llm_metrics.get('llm_explanation_accuracy_pct', 'N/A')}%"
        if llm_metrics else "Not run yet"
    )

    st.divider()

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("Matches by Reason")
        matches_by_reason = metrics.get("matches_by_reason", {})
        if matches_by_reason:
            df = pd.DataFrame(
                list(matches_by_reason.items()), columns=["Reason", "Count"]
            ).set_index("Reason")
            st.bar_chart(df)
        else:
            st.info("No match data available.")

    with chart_col2:
        st.subheader("Exceptions by Reason")
        exceptions_by_reason = metrics.get("exceptions_by_reason", {})
        if exceptions_by_reason:
            df = pd.DataFrame(
                list(exceptions_by_reason.items()), columns=["Reason", "Count"]
            ).set_index("Reason")
            st.bar_chart(df)
        else:
            st.info("No exception data available.")

    st.divider()
    st.subheader("Resolved vs Exception Split")
    resolved = metrics.get("matched_count", 0)
    excepted = metrics.get("exception_count", 0)
    split_df = pd.DataFrame(
        {"Outcome": ["Resolved", "Exception"], "Count": [resolved, excepted]}
    ).set_index("Outcome")
    st.bar_chart(split_df)

    validation_report = data["validation_report"]
    if validation_report:
        st.divider()
        st.subheader("Per-Category Accuracy (vs Ground Truth)")
        per_cat = validation_report.get("per_category_accuracy", {})
        rows = []
        for cat, vals in per_cat.items():
            correct, total = vals["correct"], vals["total"]
            pct = round(correct / total * 100, 1) if total else 0
            rows.append({"Case Type": cat, "Correct": correct, "Total": total, "Accuracy %": pct})
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

#  MATCHES TAB
with tab_matches:
    st.subheader("Resolved Matches")
    matches = data["matches"]
    if matches:
        df = pd.DataFrame(matches)
        reasons = sorted(df["reason"].unique().tolist())
        selected_reasons = st.multiselect(
            "Filter by reason", reasons, default=reasons
        )
        filtered = df[df["reason"].isin(selected_reasons)]
        st.caption(f"Showing {len(filtered)} of {len(df)} matched records")
        st.dataframe(filtered, use_container_width=True, hide_index=True)
    else:
        st.info("No matches found. Run the pipeline first.")

#  EXCEPTIONS TAB 
with tab_exceptions:
    st.subheader("Honest Exception List")
    st.caption(
        "Cases the rule engine could not safely resolve. These are never "
        "force-matched - they are flagged for human review."
    )
    exceptions = data["exceptions"]
    if exceptions:
        reason_counts = Counter(e["reason"] for e in exceptions)
        cols = st.columns(len(reason_counts))
        for col, (reason, count) in zip(cols, reason_counts.items()):
            col.metric(reason.replace("_", " ").title(), count)

        st.divider()
        df = pd.DataFrame(exceptions)
        reasons = sorted(df["reason"].unique().tolist())
        selected = st.multiselect(
            "Filter by reason", reasons, default=reasons, key="exception_filter"
        )
        filtered = df[df["reason"].isin(selected)]
        st.dataframe(filtered, use_container_width=True, hide_index=True)
    else:
        st.info("No exceptions found. Run the pipeline first.")

# LLM EXPLANATIONS TAB 
with tab_llm:
    st.subheader("LLM Explanation Layer")
    st.caption(
        "For ambiguous-but-resolved cases (fee deductions, date lags, partial "
        "settlements), an LLM generates a plain-language explanation. Every "
        "explanation is programmatically validated against the actual numbers "
        "before being accepted - the LLM never makes the match decision itself."
    )

    audit_log = data["llm_audit_log"]
    review_needed = data["llm_review_needed"]

    if audit_log:
        col1, col2, col3 = st.columns(3)
        col1.metric("Explanations Generated", len(audit_log))
        accepted = sum(1 for a in audit_log if a.get("status") == "accepted")
        col2.metric("Accepted", accepted)
        col3.metric("Rejected / Escalated", len(audit_log) - accepted)

        st.divider()
        status_filter = st.radio(
            "Show", ["All", "Accepted only", "Rejected only"], horizontal=True
        )
        if status_filter == "Accepted only":
            display_log = [a for a in audit_log if a.get("status") == "accepted"]
        elif status_filter == "Rejected only":
            display_log = [a for a in audit_log if a.get("status") != "accepted"]
        else:
            display_log = audit_log

        PAGE_SIZE = 25
        total_entries = len(display_log)
        total_pages = max(1, (total_entries - 1) // PAGE_SIZE + 1)

        if total_pages > 1:
            page = st.number_input(
                f"Page (1-{total_pages})", min_value=1, max_value=total_pages,
                value=1, step=1
            )
        else:
            page = 1

        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        page_entries = display_log[start:end]

        st.caption(
            f"Showing entries {start + 1}-{min(end, total_entries)} of {total_entries}"
        )

        for entry in page_entries:
            status = entry.get("status", "unknown")
            icon = "\u2705" if status == "accepted" else "\u26A0\uFE0F"
            with st.expander(
                f"{icon} {entry.get('payment_id')} - {entry.get('true_reason')} ({status})"
            ):
                st.write("**Explanation:**", entry.get("llm_output", {}).get("explanation", "N/A"))
                if status == "accepted":
                    st.write("**Validation:**", entry.get("validation", {}))
                else:
                    validation = entry.get("validation", {})
                    st.error("**Why this was rejected:**")
                    if not validation.get("reason_code_matches", True):
                        st.write(
                            f"- The LLM's own classification did not match the rule "
                            f"engine's decision (`{entry.get('true_reason')}`)."
                        )
                    if not validation.get("numeric_check_passed", True):
                        st.write(
                            f"- The underlying numbers did not pass the programmatic "
                            f"sanity check: {validation.get('detail', 'no detail available')}"
                        )
                    st.caption(
                        "This case was escalated to llm_review_needed.json instead of "
                        "being trusted, since it failed one or more validation checks."
                    )

        if review_needed:
            st.divider()
            st.subheader(f"Escalated for Human Review ({len(review_needed)})")
            st.caption(
                "Cases where the LLM's explanation failed validation, or the API "
                "call itself failed. These are never silently accepted."
            )
            df_review = pd.DataFrame(review_needed)
            st.dataframe(df_review, use_container_width=True, hide_index=True)
    else:
        st.info(
            "No LLM explanations found yet. Run `python src/llm_explainer.py` "
            "(with a GEMINI_API_KEY in your .env) to generate them."
        )