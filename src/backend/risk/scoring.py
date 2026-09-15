"""Site risk scoring — transparent, explainable 0–100 score."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from backend.detection.models import Deviation
from backend.risk.config import DEFAULT_WEIGHTS, RiskWeightsConfig, get_risk_band
from backend.risk.indicators import (
    compute_deviation_trend,
    compute_enrollment_velocity_risk,
    compute_mean_time_to_detection,
    compute_monitoring_coverage,
    compute_recurrence_rate,
    compute_severity_weighted_rate,
)


class IndicatorBreakdown(BaseModel):
    name: str
    raw_value: float
    normalized_value: float  # 0–100
    weight: float
    contribution: float  # normalized_value × weight


class SiteRiskScore(BaseModel):
    site_id: str
    score: float  # 0–100
    risk_band: str
    indicators: list[IndicatorBreakdown]
    total_deviations: int
    total_visits: int


def _normalize(value: float, max_val: float) -> float:
    """Clamp a raw value to [0, 100] assuming max_val represents 100."""
    if max_val <= 0:
        return 0.0
    return min(value / max_val * 100, 100.0)


def score_sites(
    deviations: list[Deviation],
    sites: list[dict[str, Any]],
    patients: list[dict[str, Any]],
    visits: list[dict[str, Any]],
    weights: RiskWeightsConfig | None = None,
    reference_date: date | None = None,
) -> list[SiteRiskScore]:
    """Compute risk scores for all sites.

    Returns a list sorted descending by score (highest risk first).
    """
    w = weights or DEFAULT_WEIGHTS

    # Build visit count per site
    visit_counts: dict[str, int] = {}
    for v in visits:
        site_id = v.get("site_id", "")
        visit_counts[site_id] = visit_counts.get(site_id, 0) + 1

    site_ids = [s["site_id"] for s in sites]
    for sid in site_ids:
        visit_counts.setdefault(sid, 0)

    # Compute all indicators
    swr = compute_severity_weighted_rate(deviations, visit_counts)
    rec = compute_recurrence_rate(deviations, visit_counts)
    mttd = compute_mean_time_to_detection(deviations, visits)
    trend = compute_deviation_trend(deviations, visit_counts, reference_date)
    enroll_risk = compute_enrollment_velocity_risk(sites, patients, deviations, visit_counts)
    mon_cov = compute_monitoring_coverage(sites, visit_counts)

    # Dev count per site
    dev_by_site: dict[str, int] = {}
    for d in deviations:
        dev_by_site[d.site_id] = dev_by_site.get(d.site_id, 0) + 1

    results: list[SiteRiskScore] = []

    for site in sites:
        sid = site["site_id"]
        n_visits = visit_counts.get(sid, 0)
        n_devs = dev_by_site.get(sid, 0)

        # Raw indicator values
        raw_swr = swr.get(sid, 0.0)
        raw_rec = rec.get(sid, 0.0)
        raw_mttd = mttd.get(sid, 0.0)
        raw_trend = trend.get(sid, 0.0)
        raw_enroll = enroll_risk.get(sid, 0.0)
        raw_mon = mon_cov.get(sid, 0.0)

        # Normalize to 0–100
        norm_swr = _normalize(raw_swr, 150)    # 150 weighted devs/100 visits → 100
        norm_rec = min(raw_rec, 100.0)          # already 0–100
        norm_mttd = _normalize(raw_mttd, 20)   # 20 day lag → 100
        # Trend: convert -100..+100 → 0..100 (50 = neutral)
        norm_trend = min(max((raw_trend + 100) / 2, 0), 100)
        norm_enroll = min(raw_enroll, 100.0)    # already 0–100
        norm_mon = min(raw_mon, 100.0)          # already 0–100

        indicators = [
            IndicatorBreakdown(
                name="severity_weighted_rate",
                raw_value=round(raw_swr, 3),
                normalized_value=round(norm_swr, 2),
                weight=w.severity_weighted_rate,
                contribution=round(norm_swr * w.severity_weighted_rate, 3),
            ),
            IndicatorBreakdown(
                name="recurrence_rate",
                raw_value=round(raw_rec, 3),
                normalized_value=round(norm_rec, 2),
                weight=w.recurrence_rate,
                contribution=round(norm_rec * w.recurrence_rate, 3),
            ),
            IndicatorBreakdown(
                name="mean_time_to_detection",
                raw_value=round(raw_mttd, 3),
                normalized_value=round(norm_mttd, 2),
                weight=w.mean_time_to_detection,
                contribution=round(norm_mttd * w.mean_time_to_detection, 3),
            ),
            IndicatorBreakdown(
                name="deviation_trend",
                raw_value=round(raw_trend, 3),
                normalized_value=round(norm_trend, 2),
                weight=w.deviation_trend,
                contribution=round(norm_trend * w.deviation_trend, 3),
            ),
            IndicatorBreakdown(
                name="enrollment_velocity_risk",
                raw_value=round(raw_enroll, 3),
                normalized_value=round(norm_enroll, 2),
                weight=w.enrollment_velocity_risk,
                contribution=round(norm_enroll * w.enrollment_velocity_risk, 3),
            ),
            IndicatorBreakdown(
                name="monitoring_coverage",
                raw_value=round(raw_mon, 3),
                normalized_value=round(norm_mon, 2),
                weight=w.monitoring_coverage,
                contribution=round(norm_mon * w.monitoring_coverage, 3),
            ),
        ]

        total_score = sum(ind.contribution for ind in indicators)
        total_score = round(min(max(total_score, 0), 100), 2)

        results.append(SiteRiskScore(
            site_id=sid,
            score=total_score,
            risk_band=get_risk_band(total_score),
            indicators=indicators,
            total_deviations=n_devs,
            total_visits=n_visits,
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results
