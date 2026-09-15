"""Streamlit dashboard — Clinical Trial Risk Monitor.

Run: streamlit run dashboard/app.py
Requires the FastAPI backend to be running at BACKEND_URL (default: http://localhost:8000).

Features:
  - CSV drag-and-drop upload (no pre-generated files needed)
  - World choropleth map — single /sites/meta + /sites/risk call, instant
  - Interactive risk-weight sliders with live client-side re-scoring
  - Plotly charts with hover tooltips throughout
"""

from __future__ import annotations

import json
import os
import tempfile
import pathlib
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Clinical Trial Risk Monitor",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — premium dark look
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .stApp { background: #0d1117; }
    section[data-testid="stSidebar"] { background: #161b22; border-right: 1px solid #30363d; }
    [data-testid="metric-container"] {
        background: #161b22; border: 1px solid #30363d;
        border-radius: 10px; padding: 14px 18px;
    }
    [data-testid="metric-container"] label { color: #8b949e !important; font-size: 0.75rem !important; }
    [data-testid="metric-container"] [data-testid="stMetricValue"] {
        color: #e6edf3 !important; font-size: 1.7rem !important; font-weight: 700 !important;
    }
    h1, h2, h3 { color: #e6edf3 !important; }
    p, span, div { color: #c9d1d9; }
    .sidebar-brand { font-size: 1.3rem; font-weight: 800; color: #58a6ff; letter-spacing: 1px; }
    .sidebar-sub  { font-size: 0.78rem; color: #8b949e; }
    [data-testid="stFileUploadDropzone"] {
        border: 2px dashed #30363d !important;
        background: #161b22 !important;
        border-radius: 10px !important;
    }
    hr { border-color: #30363d !important; }
    .stDataFrame { border-radius: 8px; overflow: hidden; }
    .stSlider label { color: #8b949e !important; font-size: 0.78rem !important; }
    .card {
        background: #161b22; border: 1px solid #30363d;
        border-radius: 12px; padding: 20px 24px; margin-bottom: 16px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Colour palettes
# ---------------------------------------------------------------------------

BAND_PLOTLY = {"Critical": "#da3633", "High": "#f0883e", "Medium": "#e3b341", "Low": "#3fb950"}
SEV_COLORS  = {"Major": "#da3633", "Minor": "#e3b341", "Administrative": "#1f6feb", "Unclassified": "#6e7681"}

INDICATOR_LABELS = {
    "severity_weighted_rate":   "Severity-Weighted Rate",
    "recurrence_rate":          "Recurrence Rate",
    "mean_time_to_detection":   "Time to Detection",
    "deviation_trend":          "Deviation Trend",
    "enrollment_velocity_risk": "Enrollment Velocity Risk",
    "monitoring_coverage":      "Monitoring Coverage",
}

# Country name → ISO-3 for choropleth
COUNTRY_ISO3 = {
    "United States": "USA", "Germany": "DEU", "France": "FRA", "Japan": "JPN",
    "United Kingdom": "GBR", "Canada": "CAN", "Australia": "AUS", "Brazil": "BRA",
    "India": "IND", "China": "CHN", "Italy": "ITA", "Spain": "ESP",
    "South Korea": "KOR", "Netherlands": "NLD", "Sweden": "SWE", "Poland": "POL",
    "Belgium": "BEL", "Switzerland": "CHE", "Austria": "AUT", "Mexico": "MEX",
    "Argentina": "ARG", "South Africa": "ZAF", "Turkey": "TUR", "Israel": "ISR",
    "Russia": "RUS", "Ukraine": "UKR", "Czech Republic": "CZE", "Hungary": "HUN",
    "Romania": "ROU", "Portugal": "PRT", "Denmark": "DNK", "Norway": "NOR",
    "Finland": "FIN", "New Zealand": "NZL", "Singapore": "SGP", "Taiwan": "TWN",
}

# ---------------------------------------------------------------------------
# Plotly dark layout helper
# ---------------------------------------------------------------------------

# Base layout — NO margin key here so per-chart overrides never conflict.
_PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#c9d1d9", family="Inter, sans-serif"),
    xaxis=dict(gridcolor="#21262d", showgrid=True),
    yaxis=dict(gridcolor="#21262d", showgrid=True),
)


def _layout(**overrides) -> dict:
    """Merge _PLOTLY_LAYOUT with per-chart overrides safely."""
    base = dict(_PLOTLY_LAYOUT)
    base.update(overrides)
    return base

# ---------------------------------------------------------------------------
# API helpers  (with retry on connection reset)
# ---------------------------------------------------------------------------

import time as _time


def _get(endpoint: str, params: dict | None = None, _retries: int = 2) -> Any:
    """GET with automatic retry on connection-reset errors (e.g. uvicorn reload)."""
    for attempt in range(_retries + 1):
        try:
            r = requests.get(f"{BACKEND_URL}{endpoint}", params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ConnectionError, ConnectionResetError):
            if attempt < _retries:
                _time.sleep(0.8 * (attempt + 1))  # brief back-off then retry
                continue
            st.warning(
                f"⚠️ Backend not reachable at `{BACKEND_URL}`. "
                "Make sure `uvicorn backend.app:app --reload-dir backend` is running."
            )
            return None
        except Exception as exc:
            st.error(f"API error [{endpoint}]: {exc}")
            return None


def _post(endpoint: str, json_body: dict | None = None, _retries: int = 1) -> Any:
    """POST with one retry on connection-reset errors."""
    for attempt in range(_retries + 1):
        try:
            r = requests.post(
                f"{BACKEND_URL}{endpoint}", json=json_body or {}, timeout=120
            )
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.ConnectionError, ConnectionResetError):
            if attempt < _retries:
                _time.sleep(1.0)
                continue
            st.warning(
                f"⚠️ Backend not reachable at `{BACKEND_URL}`. "
                "Make sure `uvicorn backend.app:app --reload-dir backend` is running."
            )
            return None
        except Exception as exc:
            st.error(f"API error [{endpoint}]: {exc}")
            return None


# ---------------------------------------------------------------------------
# Cached data loaders  (module-level so cache works across all views)
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


@st.cache_data(ttl=600)
def load_site_meta() -> pd.DataFrame:
    """Single call returning country/name for every site — used by world map."""
    data = _get("/sites/meta")
    if not data:
        return pd.DataFrame(columns=["site_id", "site_name", "country", "principal_investigator"])
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown('<div class="sidebar-brand">🔬 CTRM</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-sub">Clinical Trial Risk Monitor</div>', unsafe_allow_html=True)
    st.divider()

    view = st.radio(
        "Navigate",
        ["📊 Overview", "🗺️ World Risk Map", "📁 Data Upload",
         "🔬 Site Drill-Down", "⚖️ Risk Weight Tuner", "📋 CAPA Generator"],
        index=0,
    )

    st.divider()
    st.caption("Quick Actions")

    if st.button("⚙️ Ingest Data", use_container_width=True):
        with st.spinner("Ingesting…"):
            res = _post("/ingest")
            if res:
                st.success(
                    f"✅ {res['sites_loaded']} sites · {res['patients_loaded']} patients · "
                    f"{res['visits_loaded']} visits"
                )
        load_risks.clear()
        load_deviations.clear()
        load_site_meta.clear()

    if st.button("🔍 Run Analysis", use_container_width=True, type="primary"):
        with st.spinner("Running AI analysis (~30 s)…"):
            res = _post("/analyze")
            if res:
                st.success(
                    f"🎯 {res['deviations_detected']} deviations · "
                    f"{res['sites_scored']} sites · {res['elapsed_seconds']:.1f}s"
                )
        load_risks.clear()
        load_deviations.clear()
        load_site_meta.clear()

    st.divider()
    st.caption("Backend")
    hc = _get("/health")
    if hc:
        st.success(f"🟢 Online")
    else:
        st.error("🔴 Offline")


# ---------------------------------------------------------------------------
# Shared safe helpers
# ---------------------------------------------------------------------------

def _safe_pie(df: pd.DataFrame, values: str, names: str,
              color_map: dict | None = None, hole: float = 0.55) -> go.Figure | None:
    """Return a dark-mode pie chart, or None if df is empty."""
    if df is None or df.empty:
        return None
    kwargs: dict = dict(values=values, names=names, hole=hole)
    if color_map:
        kwargs["color"] = names
        kwargs["color_discrete_map"] = color_map
    fig = px.pie(df, **kwargs)
    fig.update_traces(textinfo="percent+label")
    fig.update_layout(**_layout(showlegend=False, margin=dict(l=0, r=0, t=10, b=0)))
    return fig


def _band_icon(b: str) -> str:
    return {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(b, "⚪")


def _risk_band_from_score(s: float) -> str:
    if s >= 80: return "Critical"
    if s >= 60: return "High"
    if s >= 30: return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# VIEW: OVERVIEW
# ---------------------------------------------------------------------------

if view == "📊 Overview":
    st.title("📊 Trial Overview")

    risks = load_risks()
    if not risks:
        st.info("No data yet. Use **⚙️ Ingest Data** then **🔍 Run Analysis** in the sidebar.")
        st.stop()

    risks_df = pd.DataFrame(risks)
    total_deviations = int(risks_df["total_deviations"].sum())
    total_visits     = int(risks_df["total_visits"].sum())
    total_sites      = len(risks_df)
    band_counts      = risks_df["risk_band"].value_counts()
    avg_score        = risks_df["score"].mean()
    dev_rate         = round(total_deviations / total_visits * 100, 1) if total_visits else 0

    # KPI row
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Sites",            total_sites)
    k2.metric("Total Deviations", f"{total_deviations:,}")
    k3.metric("🔴 Critical",       int(band_counts.get("Critical", 0)))
    k4.metric("🟠 High",           int(band_counts.get("High", 0)))
    k5.metric("Avg Risk Score",   f"{avg_score:.1f}")
    k6.metric("Dev / 100 Visits", dev_rate)

    st.divider()

    devs    = load_deviations()
    devs_df = pd.DataFrame(devs) if devs else pd.DataFrame()

    col_a, col_b = st.columns(2)

    # --- Risk band bar chart ---
    with col_a:
        st.subheader("Sites by Risk Band")
        bd = band_counts.rename_axis("band").reset_index(name="count")
        order = ["Critical", "High", "Medium", "Low"]
        bd["band"] = pd.Categorical(bd["band"], categories=order, ordered=True)
        bd = bd.sort_values("band")
        fig = px.bar(
            bd, x="band", y="count", color="band",
            color_discrete_map=BAND_PLOTLY, text="count",
            labels={"band": "Risk Band", "count": "Sites"},
        )
        fig.update_traces(textposition="outside", marker_line_width=0)
        fig.update_layout(showlegend=False, **_PLOTLY_LAYOUT)
        st.plotly_chart(fig, width='stretch')

    # --- Severity donut ---
    with col_b:
        st.subheader("Deviation Severity Distribution")
        if not devs_df.empty and "severity" in devs_df.columns:
            sev_counts = (
                devs_df["severity"]
                .fillna("Unclassified")
                .value_counts()
                .rename_axis("severity")
                .reset_index(name="count")
            )
            fig2 = _safe_pie(sev_counts, "count", "severity", SEV_COLORS, hole=0.55)
            if fig2:
                st.plotly_chart(fig2, width='stretch')
        else:
            st.info("Run analysis to see deviation data.")

    # --- Deviation type breakdown ---
    if not devs_df.empty and "deviation_type" in devs_df.columns:
        st.subheader("Deviation Type × Severity")
        devs_df["severity"] = devs_df["severity"].fillna("Unclassified")
        type_sev = (
            devs_df.groupby(["deviation_type", "severity"], dropna=False)
            .size()
            .reset_index(name="count")
        )
        fig3 = px.bar(
            type_sev, x="deviation_type", y="count", color="severity",
            color_discrete_map=SEV_COLORS,
            labels={"deviation_type": "Type", "count": "Count", "severity": "Severity"},
            barmode="stack",
        )
        fig3.update_layout(**_PLOTLY_LAYOUT, showlegend=True)
        st.plotly_chart(fig3, width='stretch')

    # --- Top-10 table ---
    st.subheader("🏆 Top 10 Riskiest Sites — Action Required")
    top10 = risks_df.nlargest(10, "score")[
        ["site_id", "score", "risk_band", "total_deviations", "total_visits"]
    ].copy()
    top10["risk_band"] = top10["risk_band"].apply(lambda b: f"{_band_icon(b)} {b}")
    st.dataframe(
        top10, use_container_width=True, hide_index=True,
        column_config={
            "score": st.column_config.ProgressColumn(
                "Risk Score", min_value=0, max_value=100, format="%.1f"
            ),
        },
    )


# ---------------------------------------------------------------------------
# VIEW: WORLD RISK MAP
# ---------------------------------------------------------------------------

elif view == "🗺️ World Risk Map":
    st.title("🗺️ Global Site Risk Map")
    st.caption(
        "Country colours reflect the **highest site risk score** in that country. "
        "Loaded via two single API calls — instant."
    )

    risks = load_risks()
    if not risks:
        st.info("No data yet. Ingest data and run analysis first.")
        st.stop()

    risks_df = pd.DataFrame(risks)

    # Single bulk call for country metadata — NO per-site loop
    with st.spinner("Loading site geography (1 API call)…"):
        meta_df = load_site_meta()

    if meta_df.empty:
        st.warning("Site metadata not available. Re-ingest data and try again.")
        st.stop()

    # Merge risk scores with country info
    merged = risks_df.merge(meta_df, on="site_id", how="left")
    merged["country"] = merged["country"].fillna("Unknown")

    # Aggregate per country
    country_df = (
        merged.groupby("country")
        .agg(
            max_score      =("score",             "max"),
            avg_score      =("score",             "mean"),
            total_sites    =("site_id",           "count"),
            total_devs     =("total_deviations",  "sum"),
        )
        .reset_index()
    )
    country_df["iso3"]     = country_df["country"].map(COUNTRY_ISO3)
    country_df["risk_band"]= country_df["max_score"].apply(_risk_band_from_score)
    country_df["max_score"]= country_df["max_score"].round(1)
    country_df["avg_score"]= country_df["avg_score"].round(1)

    # Filters
    fc1, fc2 = st.columns(2)
    band_filter   = fc1.multiselect("Show Risk Bands", ["Critical","High","Medium","Low"],
                                    default=["Critical","High","Medium","Low"])
    color_metric  = fc2.selectbox("Colour by", ["max_score","avg_score","total_devs"])

    filtered = country_df[country_df["risk_band"].isin(band_filter)].copy()

    # Choropleth — countries not in COUNTRY_ISO3 are still shown as unknown
    fig_map = px.choropleth(
        filtered,
        locations="iso3",
        color=color_metric,
        hover_name="country",
        hover_data={
            "iso3": False,
            "max_score": True,
            "avg_score": True,
            "total_sites": True,
            "total_devs": True,
            "risk_band": True,
        },
        color_continuous_scale=[
            [0.00, "#0d4429"],
            [0.30, "#3fb950"],
            [0.60, "#e3b341"],
            [0.79, "#f0883e"],
            [0.80, "#da3633"],
            [1.00, "#8b1a1a"],
        ],
        range_color=[0, 100] if "score" in color_metric else None,
        labels={"max_score": "Max Risk", "avg_score": "Avg Risk", "total_devs": "Deviations"},
    )
    fig_map.update_geos(
        showframe=False, showcoastlines=True, coastlinecolor="#30363d",
        showland=True, landcolor="#161b22", showocean=True, oceancolor="#0d1117",
        showlakes=False, showcountries=True, countrycolor="#30363d",
        projection_type="natural earth",
    )
    fig_map.update_layout(
        paper_bgcolor="#0d1117", geo_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", family="Inter, sans-serif"),
        margin=dict(l=0, r=0, t=10, b=0),
        coloraxis_colorbar=dict(
            title=dict(text="Score", font=dict(color="#c9d1d9")),
            tickfont=dict(color="#c9d1d9"),
            bgcolor="#161b22", bordercolor="#30363d",
        ),
        height=480,
    )
    st.plotly_chart(fig_map, width='stretch')

    # Country summary table
    st.subheader("Country Risk Summary")
    disp = filtered.copy()
    disp["risk_band"] = disp["risk_band"].apply(lambda b: f"{_band_icon(b)} {b}")
    st.dataframe(
        disp[["country","max_score","avg_score","total_sites","total_devs","risk_band"]]
        .sort_values("max_score", ascending=False),
        use_container_width=True, hide_index=True,
        column_config={
            "max_score": st.column_config.ProgressColumn("Max Score", min_value=0, max_value=100, format="%.1f"),
            "avg_score": "Avg Score", "total_sites": "Sites",
            "total_devs": "Deviations", "risk_band": "Worst Band",
        },
    )

    # Per-site scatter by country
    st.subheader("Site Score Distribution by Country")
    merged_disp = merged[merged["risk_band"].isin(band_filter)].sort_values("score", ascending=False)
    if not merged_disp.empty:
        fig_strip = px.strip(
            merged_disp, x="country", y="score", color="risk_band",
            color_discrete_map=BAND_PLOTLY,
            hover_data=["site_id", "principal_investigator", "total_deviations"],
            labels={"score": "Risk Score", "country": "Country"},
        )
        fig_strip.update_layout(**_PLOTLY_LAYOUT, height=320)
        st.plotly_chart(fig_strip, width='stretch')


# ---------------------------------------------------------------------------
# VIEW: DATA UPLOAD
# ---------------------------------------------------------------------------

elif view == "📁 Data Upload":
    st.title("📁 Data Upload")
    st.caption(
        "Drag-and-drop trial CSV files to ingest — no pre-generated JSON needed. "
        "After upload, click **🔍 Run Analysis** in the sidebar."
    )

    st.info(
        "**Expected columns per file:**\n\n"
        "- **Sites**: `site_id`, `site_name`, `country`, `principal_investigator`, "
        "`activation_date`, `staff_count`, `monitoring_visits_completed`\n"
        "- **Patients**: `patient_id`, `site_id`, `age`, `sex`, `diagnosis_code`, "
        "`enrollment_date`, `ecog_ps`, `creatinine_clearance_ml_min`, `alt_x_uln`, "
        "`qtcf_ms`, `prior_cdk46_inhibitor`, `active_cns_mets`, `pregnant_or_breastfeeding`\n"
        "- **Visits**: `visit_id`, `patient_id`, `site_id`, `protocol_visit_id`, "
        "`scheduled_date`, `actual_date`, `dose_administered_mg`, "
        "`assessments_completed` (semicolon-separated), `data_entry_date`\n"
        "- **Conmeds** *(optional)*: `record_id`, `patient_id`, `site_id`, "
        "`drug_name`, `atc_class`, `start_date`, `stop_date`"
    )

    uc1, uc2 = st.columns(2)
    with uc1:
        sites_file   = st.file_uploader("Sites CSV",    type=["csv"], key="up_sites")
        visits_file  = st.file_uploader("Visits CSV",   type=["csv"], key="up_visits")
    with uc2:
        patients_file = st.file_uploader("Patients CSV", type=["csv"], key="up_patients")
        conmeds_file  = st.file_uploader("Conmeds CSV (optional)", type=["csv"], key="up_conmeds")

    def _csv_to_records(f) -> list[dict]:
        df = pd.read_csv(f)
        records = []
        for _, row in df.iterrows():
            rec = row.where(pd.notnull(row), None).to_dict()
            if "assessments_completed" in rec and isinstance(rec["assessments_completed"], str):
                rec["assessments_completed"] = [
                    a.strip() for a in rec["assessments_completed"].split(";") if a.strip()
                ]
            for bool_col in ["prior_cdk46_inhibitor", "active_cns_mets", "pregnant_or_breastfeeding"]:
                if bool_col in rec and rec[bool_col] is not None:
                    rec[bool_col] = str(rec[bool_col]).lower() in ("true", "1", "yes")
            records.append(rec)
        return records

    if st.button("🚀 Upload & Ingest", type="primary", use_container_width=True):
        if not sites_file or not patients_file or not visits_file:
            st.error("Sites, Patients, and Visits CSVs are all required.")
        else:
            with st.spinner("Parsing and ingesting CSVs…"):
                try:
                    sites_data    = _csv_to_records(sites_file)
                    patients_data = _csv_to_records(patients_file)
                    visits_data   = _csv_to_records(visits_file)
                    conmeds_data  = _csv_to_records(conmeds_file) if conmeds_file else []

                    tmp = pathlib.Path(tempfile.mkdtemp())
                    (tmp / "sites.json").write_text(json.dumps(sites_data), encoding="utf-8")
                    (tmp / "patients.json").write_text(json.dumps(patients_data), encoding="utf-8")
                    (tmp / "visits.json").write_text(json.dumps(visits_data), encoding="utf-8")
                    (tmp / "conmed_records.json").write_text(json.dumps(conmeds_data), encoding="utf-8")

                    res = _post("/ingest", {"data_dir": str(tmp)})
                    if res:
                        st.success(
                            f"✅ Ingested: **{res['sites_loaded']}** sites · "
                            f"**{res['patients_loaded']}** patients · "
                            f"**{res['visits_loaded']}** visits · "
                            f"**{res['conmeds_loaded']}** conmeds"
                        )
                        load_risks.clear()
                        load_deviations.clear()
                        load_site_meta.clear()
                        st.balloons()
                except Exception as exc:
                    st.error(f"Parse error: {exc}")

    st.divider()
    st.subheader("Or ingest from default data directory")
    if st.button("⚙️ Ingest from server data dir", use_container_width=True):
        with st.spinner("Ingesting…"):
            res = _post("/ingest")
            if res:
                st.success(
                    f"✅ {res['sites_loaded']} sites · {res['patients_loaded']} patients · "
                    f"{res['visits_loaded']} visits"
                )
            load_risks.clear()
            load_deviations.clear()
            load_site_meta.clear()

    if sites_file:
        sites_file.seek(0)
        with st.expander("Preview: Sites"):
            st.dataframe(pd.read_csv(sites_file).head(10), use_container_width=True, hide_index=True)
    if patients_file:
        patients_file.seek(0)
        with st.expander("Preview: Patients"):
            st.dataframe(pd.read_csv(patients_file).head(10), use_container_width=True, hide_index=True)
    if visits_file:
        visits_file.seek(0)
        with st.expander("Preview: Visits"):
            st.dataframe(pd.read_csv(visits_file).head(10), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# VIEW: SITE DRILL-DOWN
# ---------------------------------------------------------------------------

elif view == "🔬 Site Drill-Down":
    st.title("🔬 Site Drill-Down")

    risks = load_risks()
    if not risks:
        st.info("No data yet.")
        st.stop()

    site_options = [r["site_id"] for r in sorted(risks, key=lambda x: -x["score"])]
    selected = st.selectbox("Select Site (sorted by risk, highest first)", options=site_options)

    data = _get(f"/sites/{selected}")
    if not data:
        st.stop()

    risk = data.get("risk_score")
    band = risk["risk_band"] if risk else "Unknown"
    band_color = BAND_PLOTLY.get(band, "#6e7681")

    score_str = f"{risk['score']:.1f}" if risk else "N/A"

    st.markdown(
        f"""
        <div class="card" style="border-left: 4px solid {band_color};">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <div style="font-size:1.2rem; font-weight:700; color:#e6edf3;">{selected}</div>
                    <div style="color:#8b949e; font-size:0.85rem;">
                        {data.get('site_name','')} &nbsp;·&nbsp; {data.get('country','')}
                        &nbsp;·&nbsp; PI: {data.get('principal_investigator','N/A')}
                    </div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:2rem; font-weight:800; color:{band_color};">
                        {score_str}
                    </div>
                    <div style="font-size:0.85rem; color:#8b949e;">
                        {_band_icon(band)} {band} Risk
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Staff Count",            data.get("staff_count", "N/A"))
    m2.metric("Monitoring Visits Done", data.get("monitoring_visits_completed", "N/A"))
    if risk:
        m3.metric("Total Deviations", risk.get("total_deviations", 0))

    st.divider()

    # Indicator bar chart
    if risk and risk.get("indicators"):
        st.subheader("Risk Score Breakdown")
        ind_df = pd.DataFrame(risk["indicators"])
        ind_df["label"] = ind_df["name"].map(INDICATOR_LABELS).fillna(ind_df["name"])
        ind_df = ind_df.sort_values("contribution", ascending=True)

        fig_ind = px.bar(
            ind_df, x="contribution", y="label", orientation="h",
            color="contribution",
            color_continuous_scale=["#3fb950", "#e3b341", "#f0883e", "#da3633"],
            text=ind_df["contribution"].apply(lambda x: f"{x:.2f}"),
            labels={"contribution": "Contribution to Score", "label": "Indicator"},
            hover_data={"raw_value": True, "normalized_value": True, "weight": True},
        )
        fig_ind.update_traces(textposition="outside")
        fig_ind.update_layout(
            showlegend=False, coloraxis_showscale=False,
            **_PLOTLY_LAYOUT, height=300,
        )
        st.plotly_chart(fig_ind, width='stretch')

        with st.expander("📋 Full indicator table"):
            st.dataframe(
                ind_df[["label", "raw_value", "normalized_value", "weight", "contribution"]],
                use_container_width=True, hide_index=True,
            )

    # Deviations
    devs = data.get("recent_deviations", [])
    if devs:
        devs_df = pd.DataFrame(devs)
        devs_df["severity"] = devs_df["severity"].fillna("Unclassified")
        st.subheader(f"Recent Deviations ({len(devs)})")

        sc1, sc2 = st.columns([2, 3])
        with sc1:
            sc = devs_df["severity"].value_counts().rename_axis("severity").reset_index(name="count")
            fig_sc = _safe_pie(sc, "count", "severity", SEV_COLORS)
            if fig_sc:
                fig_sc.update_layout(height=220)
                st.plotly_chart(fig_sc, width='stretch')

        with sc2:
            type_counts = (
                devs_df["deviation_type"].value_counts()
                .rename_axis("type").reset_index(name="count")
            )
            fig_tc = px.bar(
                type_counts, x="count", y="type", orientation="h",
                color="count", color_continuous_scale="Reds",
                labels={"type": "", "count": "Count"},
            )
            fig_tc.update_layout(**_layout(
                showlegend=False, coloraxis_showscale=False,
                height=220, margin=dict(l=0, r=0, t=0, b=0),
            ))
            st.plotly_chart(fig_tc, width='stretch')

        display_cols = ["deviation_type", "severity", "patient_id", "visit_id", "magnitude", "detected_at"]
        avail = [c for c in display_cols if c in devs_df.columns]
        st.dataframe(devs_df[avail], use_container_width=True, hide_index=True)

    elif not devs:
        st.success("No deviations recorded for this site.")


# ---------------------------------------------------------------------------
# VIEW: RISK WEIGHT TUNER
# ---------------------------------------------------------------------------

elif view == "⚖️ Risk Weight Tuner":
    st.title("⚖️ Interactive Risk Weight Tuner")
    st.caption(
        "Adjust indicator weights and see site scores re-computed instantly — "
        "no re-run of the full analysis needed. "
        "Click **Apply & Persist** to push new weights to the backend."
    )

    risks = load_risks()
    if not risks:
        st.info("No data yet. Ingest data and run analysis first.")
        st.stop()

    risks_df = pd.DataFrame(risks)

    wt_col, preview_col = st.columns([1, 2])

    with wt_col:
        st.subheader("Tune Weights")
        w_swr  = st.slider("Severity-Weighted Rate",   0, 60, 35, step=1) / 100
        w_rec  = st.slider("Recurrence Rate",          0, 60, 20, step=1) / 100
        w_mttd = st.slider("Mean Time to Detection",   0, 60, 15, step=1) / 100
        w_tr   = st.slider("Deviation Trend",          0, 60, 15, step=1) / 100
        w_enr  = st.slider("Enrollment Velocity Risk", 0, 60, 10, step=1) / 100
        w_mon  = st.slider("Monitoring Coverage",      0, 60,  5, step=1) / 100

        raw_sum = w_swr + w_rec + w_mttd + w_tr + w_enr + w_mon
        norm = raw_sum if raw_sum > 0 else 1.0
        weights = {
            "severity_weighted_rate":   round(w_swr  / norm, 4),
            "recurrence_rate":          round(w_rec  / norm, 4),
            "mean_time_to_detection":   round(w_mttd / norm, 4),
            "deviation_trend":          round(w_tr   / norm, 4),
            "enrollment_velocity_risk": round(w_enr  / norm, 4),
            "monitoring_coverage":      round(w_mon  / norm, 4),
        }
        actual_sum = sum(weights.values())
        st.markdown(f"**Effective weight sum:** `{actual_sum:.4f}` ✔", unsafe_allow_html=False)

        # Weight donut
        wt_labels = list(INDICATOR_LABELS.values())
        wt_values = list(weights.values())
        wt_df = pd.DataFrame({"label": wt_labels, "weight": wt_values})
        fig_w = px.pie(wt_df, names="label", values="weight", hole=0.55,
                       color_discrete_sequence=px.colors.sequential.Blues_r)
        fig_w.update_traces(textinfo="percent")
        fig_w.update_layout(**_layout(
            showlegend=False, height=260, margin=dict(l=0, r=0, t=0, b=0),
        ))
        st.plotly_chart(fig_w, width='stretch')

    with preview_col:
        def _rescore(row: pd.Series, w: dict) -> float:
            ind_list = row.get("indicators") if isinstance(row.get("indicators"), list) else []
            if not ind_list:
                return float(row.get("score", 0.0))
            total = sum(
                ind["normalized_value"] * w.get(ind["name"], ind["weight"])
                for ind in ind_list
            )
            return round(min(max(total, 0), 100), 2)

        re_df = risks_df.copy()
        re_df["new_score"] = re_df.apply(lambda r: _rescore(r, weights), axis=1)
        re_df["new_band"]  = re_df["new_score"].apply(_risk_band_from_score)
        re_df["delta"]     = (re_df["new_score"] - re_df["score"]).round(2)

        st.subheader("Live Re-Scored Sites")
        fig_cmp = go.Figure()
        fig_cmp.add_trace(go.Scatter(
            x=re_df["score"], y=re_df["new_score"],
            mode="markers",
            marker=dict(
                color=re_df["new_score"],
                colorscale=[[0,"#3fb950"],[0.3,"#e3b341"],[0.6,"#f0883e"],[1,"#da3633"]],
                size=8, cmin=0, cmax=100, showscale=True,
                colorbar=dict(
                    title=dict(text="New Score", font=dict(color="#c9d1d9")),
                    tickfont=dict(color="#c9d1d9"),
                ),
            ),
            text=re_df["site_id"],
            hovertemplate="<b>%{text}</b><br>Old: %{x:.1f}<br>New: %{y:.1f}<extra></extra>",
        ))
        fig_cmp.add_shape(type="line", x0=0, y0=0, x1=100, y1=100,
                          line=dict(color="#30363d", dash="dash"))
        fig_cmp.update_layout(**_layout(
            xaxis_title="Original Score", yaxis_title="Tuned Score",
            height=300,
        ))
        st.plotly_chart(fig_cmp, width='stretch')

        st.caption("Sites with largest positive delta (risk increased):")
        movers = re_df.nlargest(10, "delta")[
            ["site_id", "score", "new_score", "delta", "new_band"]
        ].copy()
        movers["new_band"] = movers["new_band"].apply(lambda b: f"{_band_icon(b)} {b}")
        st.dataframe(
            movers, use_container_width=True, hide_index=True,
            column_config={
                "score":     "Original Score",
                "new_score": "Tuned Score",
                "delta":     st.column_config.NumberColumn("Δ Score", format="%+.2f"),
                "new_band":  "New Band",
            },
        )

    st.divider()
    st.subheader("Band Distribution: Before vs After")
    nc1, nc2 = st.columns(2)
    with nc1:
        st.caption("Original")
        obd = risks_df["risk_band"].value_counts().rename_axis("band").reset_index(name="count")
        fig_ob = _safe_pie(obd, "count", "band", BAND_PLOTLY)
        if fig_ob:
            fig_ob.update_layout(height=220)
            st.plotly_chart(fig_ob, width='stretch')
    with nc2:
        st.caption("After Tuning")
        nbd = re_df["new_band"].value_counts().rename_axis("band").reset_index(name="count")
        fig_nb = _safe_pie(nbd, "count", "band", BAND_PLOTLY)
        if fig_nb:
            fig_nb.update_layout(height=220)
            st.plotly_chart(fig_nb, width='stretch')

    st.divider()
    if st.button("🔄 Apply Weights & Re-run Analysis on Backend", type="primary", use_container_width=True):
        with st.spinner("Sending weights and re-scoring…"):
            res = _post("/analyze/rescore", {"weights": weights})
            if res:
                st.success(
                    f"✅ Re-scored {res.get('sites_scored','?')} sites in "
                    f"{res.get('elapsed_seconds','?')} s"
                )
                load_risks.clear()


# ---------------------------------------------------------------------------
# VIEW: CAPA GENERATOR
# ---------------------------------------------------------------------------

elif view == "📋 CAPA Generator":
    st.title("📋 CAPA Report Generator")
    st.caption(
        "Generates audit-ready CAPA reports grounded in computed data. "
        "All reports are **drafts** requiring qualified human review."
    )

    risks = load_risks()
    if not risks:
        st.info("No data yet. Run analysis first.")
        st.stop()

    site_options = [r["site_id"] for r in sorted(risks, key=lambda x: -x["score"])]
    selected = st.selectbox("Select Site (sorted highest risk first)", options=site_options)

    risk_info = next((r for r in risks if r["site_id"] == selected), None)
    if risk_info:
        band = risk_info["risk_band"]
        band_color = BAND_PLOTLY.get(band, "#6e7681")
        st.markdown(
            f"""
            <div style="background:{band_color}22; border:1px solid {band_color};
                        border-radius:8px; padding:12px 18px; margin-bottom:16px;">
                {_band_icon(band)} <strong>{band} Risk</strong> &nbsp;|&nbsp;
                Score: <strong>{risk_info['score']:.1f}</strong> &nbsp;|&nbsp;
                Deviations: <strong>{risk_info['total_deviations']}</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if st.button("🚀 Generate CAPA Report", type="primary"):
        with st.spinner("AI drafting CAPA narrative…"):
            result = _post(f"/capa/{selected}")

        if result:
            st.success(f"✅ Report generated — {result['report_date']} | Band: {result['risk_band']}")

            dl1, dl2 = st.columns(2)
            dl1.link_button("📄 Download DOCX", f"{BACKEND_URL}{result['download_docx']}")
            dl2.link_button("📑 Download PDF",  f"{BACKEND_URL}{result['download_pdf']}")

            st.divider()
            narrative = result.get("narrative_preview", {})

            sections = [
                ("🔍 Root Cause Analysis",         "root_cause_analysis"),
                ("⚡ Immediate Corrective Action",  "immediate_corrective_action"),
                ("🛡️ Preventive Action",            "preventive_action"),
                ("✅ Effectiveness Check Criteria", "effectiveness_check_criteria"),
            ]
            for title, key in sections:
                with st.expander(title, expanded=True):
                    st.write(narrative.get(key, "N/A"))

            refs = narrative.get("regulatory_references", [])
            if refs:
                with st.expander("📚 Regulatory References"):
                    for ref in refs:
                        st.markdown(f"- {ref}")

            st.warning(
                "⚠️ This is a system-generated DRAFT. "
                "All content requires review by a qualified clinical research professional "
                "before any regulatory submission or site communication."
            )
