"""Leading indicator computation for site risk scoring."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from backend.detection.models import Deviation
from backend.protocol.schema import SeverityLevel

_SEVERITY_WEIGHTS = {
    SeverityLevel.MAJOR.value: 10,
    SeverityLevel.MINOR.value: 3,
    SeverityLevel.ADMINISTRATIVE.value: 1,
    None: 2,  # unclassified
}


def compute_severity_weighted_rate(
    deviations: list[Deviation],
    visit_counts: dict[str, int],
) -> dict[str, float]:
    """Severity-weighted deviations per 100 visits per site."""
    site_scores: dict[str, float] = {}
    dev_df = _deviations_to_df(deviations)

    for site_id, n_visits in visit_counts.items():
        site_devs = dev_df[dev_df["site_id"] == site_id] if not dev_df.empty else dev_df
        if n_visits == 0:
            site_scores[site_id] = 0.0
            continue
        weighted = sum(
            _SEVERITY_WEIGHTS.get(row["severity"], 2)
            for _, row in site_devs.iterrows()
        )
        site_scores[site_id] = (weighted / n_visits) * 100
    return site_scores


def compute_recurrence_rate(
    deviations: list[Deviation],
    visit_counts: dict[str, int],
) -> dict[str, float]:
    """Fraction of deviation types that repeat at a site (0–1 scale * 100)."""
    dev_df = _deviations_to_df(deviations)
    result: dict[str, float] = {}
    for site_id in visit_counts:
        site_devs = dev_df[dev_df["site_id"] == site_id] if not dev_df.empty else dev_df
        if site_devs.empty:
            result[site_id] = 0.0
            continue
        type_counts = site_devs["deviation_type"].value_counts()
        recurring = (type_counts > 1).sum()
        total_types = len(type_counts)
        result[site_id] = (recurring / total_types) * 100 if total_types > 0 else 0.0
    return result


def compute_mean_time_to_detection(
    deviations: list[Deviation],
    visits: list[dict[str, Any]],
) -> dict[str, float]:
    """Mean business-day lag between actual_date and data_entry_date per site."""
    if not visits:
        return {}
    visits_df = pd.DataFrame(visits)
    visits_df = visits_df.dropna(subset=["actual_date", "data_entry_date"])
    if visits_df.empty:
        return {}

    visits_df["actual_dt"] = pd.to_datetime(visits_df["actual_date"])
    visits_df["entry_dt"] = pd.to_datetime(visits_df["data_entry_date"])
    visits_df["lag_days"] = (visits_df["entry_dt"] - visits_df["actual_dt"]).dt.days.clip(lower=0)

    result = (
        visits_df.groupby("site_id")["lag_days"]
        .mean()
        .fillna(0)
        .to_dict()
    )
    return {k: float(v) for k, v in result.items()}


def compute_deviation_trend(
    deviations: list[Deviation],
    visit_counts: dict[str, int],
    reference_date: date | None = None,
) -> dict[str, float]:
    """Trend score: positive = worsening (last 90 days vs prior period).

    Returns a value from -100 (improving) to +100 (sharply worsening).
    """
    ref = reference_date or date.today()
    cutoff_recent = ref - timedelta(days=90)
    cutoff_prior = ref - timedelta(days=180)

    dev_df = _deviations_to_df(deviations)
    result: dict[str, float] = {}

    for site_id in visit_counts:
        site_devs = dev_df[dev_df["site_id"] == site_id] if not dev_df.empty else dev_df
        if site_devs.empty:
            result[site_id] = 0.0
            continue

        site_devs = site_devs.copy()
        site_devs["detected_date"] = pd.to_datetime(site_devs["detected_at"]).dt.date

        recent = site_devs[site_devs["detected_date"] >= cutoff_recent]
        prior = site_devs[
            (site_devs["detected_date"] >= cutoff_prior)
            & (site_devs["detected_date"] < cutoff_recent)
        ]
        n_visits = max(visit_counts.get(site_id, 1), 1)
        recent_rate = len(recent) / n_visits * 100
        prior_rate = len(prior) / n_visits * 100

        if prior_rate == 0:
            trend = min(recent_rate * 10, 100.0)
        else:
            trend = min(((recent_rate - prior_rate) / prior_rate) * 100, 100.0)
        result[site_id] = max(trend, -100.0)
    return result


def compute_enrollment_velocity_risk(
    sites: list[dict[str, Any]],
    patients: list[dict[str, Any]],
    deviations: list[Deviation],
    visit_counts: dict[str, int],
) -> dict[str, float]:
    """Sites enrolling fast with rising deviations → high risk (0–100)."""
    patient_df = pd.DataFrame(patients) if patients else pd.DataFrame()
    dev_df = _deviations_to_df(deviations)
    result: dict[str, float] = {}

    for site in sites:
        site_id = site["site_id"]
        n_patients = (
            len(patient_df[patient_df["site_id"] == site_id])
            if not patient_df.empty else 0
        )
        n_devs = len(dev_df[dev_df["site_id"] == site_id]) if not dev_df.empty else 0
        n_visits = max(visit_counts.get(site_id, 1), 1)
        dev_rate = n_devs / n_visits

        # Score: enrollment quartile × deviation rate
        enrollment_score = min(n_patients / 30 * 100, 100)
        result[site_id] = min(enrollment_score * dev_rate * 2, 100.0)
    return result


def compute_monitoring_coverage(
    sites: list[dict[str, Any]],
    visit_counts: dict[str, int],
) -> dict[str, float]:
    """Visits per completed monitoring visit — higher = less oversight (0–100 risk)."""
    result: dict[str, float] = {}
    for site in sites:
        site_id = site["site_id"]
        n_visits = visit_counts.get(site_id, 0)
        mon_visits = max(site.get("monitoring_visits_completed", 1), 1)
        ratio = n_visits / mon_visits
        # Normalize: 0 ratio=0 risk, ratio≥50 → 100 risk
        result[site_id] = min(ratio / 50 * 100, 100.0)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deviations_to_df(deviations: list[Deviation]) -> pd.DataFrame:
    if not deviations:
        return pd.DataFrame(columns=["site_id", "patient_id", "deviation_type", "severity", "detected_at"])
    return pd.DataFrame([
        {
            "deviation_id": d.deviation_id,
            "site_id": d.site_id,
            "patient_id": d.patient_id,
            "deviation_type": d.deviation_type,
            "severity": d.severity,
            "detected_at": d.detected_at.isoformat() if d.detected_at else None,
            "is_safety_critical": d.is_safety_critical,
        }
        for d in deviations
    ])
