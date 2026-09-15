"""Streamlit dashboard — Clinical Trial Risk Monitor.

Run: streamlit run dashboard/app.py
Requires the FastAPI backend to be running at BACKEND_URL (default: http://localhost:8000).
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Clinical Trial Risk Monitor",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _get(endpoint: str, params: dict | None = None) -> Any:
    try:
        r = requests.get(f"{BACKEND_URL}{endpoint}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"API error: {exc}")
        return None


def _post(endpoint: str, json_body: dict | None = None) -> Any:
    try:
        r = requests.post(f"{BACKEND_URL}{endpoint}", json=json_body or {}, timeout=120)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"API error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.title("🔬 CTRM")
st.sidebar.caption("Clinical Trial Risk Monitor")

view = st.sidebar.radio(
    "View",
    ["Overview", "Site Risk Heatmap", "Site Drill-Down", "CAPA Generator"],
    index=0,
)

# Quick-action buttons
st.sidebar.divider()
if st.sidebar.button("⚙️ Ingest Data"):
    with st.spinner("Ingesting..."):
        res = _post("/ingest")
        if res:
            st.sidebar.success(
                f"Loaded: {res['sites_loaded']} sites, {res['patients_loaded']} patients, "
                f"{res['visits_loaded']} visits"
            )

if st.sidebar.button("🔍 Run Analysis"):
    with st.spinner("Analyzing (this may take ~30 s) ..."):
        res = _post("/analyze")
        if res:
            st.sidebar.success(
                f"Detected {res['deviations_detected']} deviations across "
                f"{res['sites_scored']} sites in {res['elapsed_seconds']:.1f}s"
            )

# ---------------------------------------------------------------------------
# Shared data loaders (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_risks() -> list[dict]:
    data = _get("/sites/risk")
    return data["items"] if data else []


@st.cache_data(ttl=300)
def load_deviations(site_id: str | None = None) -> list[dict]:
    params = {"page_size": 500}
    if site_id:
        params["site_id"] = site_id
    data = _get("/deviations", params=params)
    return data["items"] if data else []


# ---------------------------------------------------------------------------
# COLOUR MAP
# ---------------------------------------------------------------------------

BAND_COLORS = {
    "Critical": "#d62728",
    "High": "#ff7f0e",
    "Medium": "#bcbd22",
    "Low": "#2ca02c",
}

SEV_COLORS = {
    "Major": "#d62728",
    "Minor": "#ff7f0e",
    "Administrative": "#1f77b4",
}

# ---------------------------------------------------------------------------
# VIEW 1: Overview
# ---------------------------------------------------------------------------

if view == "Overview":
    st.title("📊 Overview")

    risks = load_risks()
    if not risks:
        st.info("No data yet. Use the sidebar to Ingest Data then Run Analysis.")
        st.stop()

    risks_df = pd.DataFrame(risks)

    # Top KPI row
    total_deviations = risks_df["total_deviations"].sum()
    total_sites = len(risks_df)
    band_counts = risks_df["risk_band"].value_counts()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Sites", total_sites)
    col2.metric("Total Deviations", int(total_deviations))
    col3.metric("🔴 Critical", int(band_counts.get("Critical", 0)))
    col4.metric("🟠 High", int(band_counts.get("High", 0)))
    col5.metric("🟡 Medium", int(band_counts.get("Medium", 0)))

    st.divider()

    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Sites by Risk Band")
        band_df = pd.DataFrame(band_counts).reset_index()
        band_df.columns = ["band", "count"]
        band_df["color"] = band_df["band"].map(BAND_COLORS)
        st.bar_chart(
            band_df.set_index("band")["count"],
            use_container_width=True,
            color="#1a3c5e",
        )

    with col_b:
        st.subheader("Severity Distribution")
        devs = load_deviations()
        if devs:
            devs_df = pd.DataFrame(devs)
            sev_counts = devs_df["severity"].fillna("Unclassified").value_counts()
            st.bar_chart(sev_counts, use_container_width=True, color="#3b82d4")

    st.subheader("🏆 Top 10 Riskiest Sites — Action Required")
    top10 = risks_df.nlargest(10, "score")[["site_id", "score", "risk_band", "total_deviations", "total_visits"]]
    top10["risk_band"] = top10["risk_band"].apply(
        lambda b: f"{'🔴' if b == 'Critical' else '🟠' if b == 'High' else '🟡' if b == 'Medium' else '🟢'} {b}"
    )
    st.dataframe(top10, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# VIEW 2: Site Risk Heatmap
# ---------------------------------------------------------------------------

elif view == "Site Risk Heatmap":
    st.title("🗺️ Site Risk Heatmap")

    risks = load_risks()
    if not risks:
        st.info("No data yet.")
        st.stop()

    risks_df = pd.DataFrame(risks)

    # Merge site metadata from API
    @st.cache_data(ttl=300)
    def _load_site_meta() -> pd.DataFrame:
        rows = []
        # Use risk list which has site_id; metadata comes from site detail calls
        # but that would be 200 calls — instead collect from deviation data
        # Using risk data alone is sufficient for heatmap
        return pd.DataFrame()

    # Filters
    col1, col2 = st.columns(2)
    band_filter = col1.multiselect(
        "Filter by Risk Band",
        options=["Critical", "High", "Medium", "Low"],
        default=["Critical", "High", "Medium", "Low"],
    )
    score_min = col2.slider("Minimum Risk Score", 0, 100, 0)

    filtered = risks_df[
        risks_df["risk_band"].isin(band_filter) & (risks_df["score"] >= score_min)
    ].copy()

    filtered["band_icon"] = filtered["risk_band"].map(
        {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}
    )
    filtered["display"] = filtered["band_icon"] + " " + filtered["site_id"]

    st.caption(f"Showing {len(filtered)} of {len(risks_df)} sites")

    display_cols = ["site_id", "score", "risk_band", "total_deviations", "total_visits"]
    st.dataframe(
        filtered[display_cols].sort_values("score", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "score": st.column_config.ProgressColumn(
                "Risk Score", min_value=0, max_value=100, format="%.1f"
            ),
            "risk_band": "Risk Band",
        },
    )

# ---------------------------------------------------------------------------
# VIEW 3: Site Drill-Down
# ---------------------------------------------------------------------------

elif view == "Site Drill-Down":
    st.title("🔬 Site Drill-Down")

    risks = load_risks()
    if not risks:
        st.info("No data yet.")
        st.stop()

    site_options = [r["site_id"] for r in sorted(risks, key=lambda x: -x["score"])]
    selected = st.selectbox("Select Site", options=site_options)

    data = _get(f"/sites/{selected}")
    if not data:
        st.stop()

    st.subheader(f"Site: {selected}")
    meta_col, score_col = st.columns(2)

    with meta_col:
        st.markdown(f"**Country:** {data.get('country', 'N/A')}")
        st.markdown(f"**PI:** {data.get('principal_investigator', 'N/A')}")
        st.markdown(f"**Staff:** {data.get('staff_count', 'N/A')}")
        st.markdown(f"**Monitoring Visits Done:** {data.get('monitoring_visits_completed', 'N/A')}")

    risk = data.get("risk_score")
    if risk:
        band = risk["risk_band"]
        icon = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(band, "⚪")
        with score_col:
            st.metric("Risk Score", f"{risk['score']:.1f} / 100", delta=band)
            st.progress(int(risk["score"]) / 100)

    st.divider()

    if risk and risk.get("indicators"):
        st.subheader("Risk Score Breakdown")
        ind_df = pd.DataFrame(risk["indicators"])
        ind_df = ind_df.sort_values("contribution", ascending=True)
        st.bar_chart(
            ind_df.set_index("name")["contribution"],
            use_container_width=True,
            color="#1a3c5e",
        )
        with st.expander("Full indicator table"):
            st.dataframe(ind_df, use_container_width=True, hide_index=True)

    devs = data.get("recent_deviations", [])
    if devs:
        st.subheader(f"Recent Deviations ({len(devs)})")
        devs_df = pd.DataFrame(devs)[
            ["deviation_type", "severity", "patient_id", "visit_id", "magnitude", "detected_at"]
        ]
        st.dataframe(devs_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# VIEW 4: CAPA Generator
# ---------------------------------------------------------------------------

elif view == "CAPA Generator":
    st.title("📋 CAPA Report Generator")
    st.caption(
        "Generates audit-ready CAPA reports grounded in computed data. "
        "All reports are drafts requiring qualified human review."
    )

    risks = load_risks()
    if not risks:
        st.info("No data yet. Run analysis first.")
        st.stop()

    site_options = [r["site_id"] for r in sorted(risks, key=lambda x: -x["score"])]
    selected = st.selectbox("Select Site", options=site_options)

    risk_info = next((r for r in risks if r["site_id"] == selected), None)
    if risk_info:
        band = risk_info["risk_band"]
        icon = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(band, "⚪")
        st.info(f"{icon} Risk Band: **{band}** | Score: **{risk_info['score']:.1f}** | Deviations: **{risk_info['total_deviations']}**")

    if st.button("🚀 Generate CAPA Report", type="primary"):
        with st.spinner("Generating CAPA report (LLM drafting narrative)..."):
            result = _post(f"/capa/{selected}")

        if result:
            st.success(f"Report generated: {result['report_date']} | Band: {result['risk_band']}")

            col1, col2 = st.columns(2)
            col1.markdown(f"[📄 Download DOCX]({BACKEND_URL}{result['download_docx']})")
            col2.markdown(f"[📑 Download PDF]({BACKEND_URL}{result['download_pdf']})")

            st.divider()
            narrative = result.get("narrative_preview", {})

            sections = [
                ("🔍 Root Cause Analysis", "root_cause_analysis"),
                ("⚡ Immediate Corrective Action", "immediate_corrective_action"),
                ("🛡️ Preventive Action", "preventive_action"),
                ("✅ Effectiveness Check Criteria", "effectiveness_check_criteria"),
            ]
            for title, key in sections:
                with st.expander(title, expanded=True):
                    st.write(narrative.get(key, "N/A"))

            refs = narrative.get("regulatory_references", [])
            if refs:
                with st.expander("📚 Regulatory References"):
                    for r in refs:
                        st.markdown(f"- {r}")

            st.warning(
                "⚠️ This is a system-generated DRAFT. "
                "All content requires review by a qualified clinical research professional "
                "before any regulatory submission or site communication."
            )
