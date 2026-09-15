"""Tests for site risk scoring — breakdown contributions must sum to total."""

from __future__ import annotations

import pytest

from backend.detection.models import Deviation
from backend.risk.scoring import score_sites
from backend.risk.config import DEFAULT_WEIGHTS


def _make_deviation(site_id: str, dev_type: str, severity: str = "Minor") -> Deviation:
    d = Deviation(
        patient_id="P001",
        site_id=site_id,
        visit_id="V-001",
        rule_id="R001",
        deviation_type=dev_type,
        is_safety_critical=False,
        context={},
    )
    d.severity = severity
    return d


def _make_site(site_id: str) -> dict:
    return {
        "site_id": site_id,
        "site_name": f"Site {site_id}",
        "country": "USA",
        "principal_investigator": "Dr Test",
        "activation_date": "2023-01-01",
        "staff_count": 10,
        "monitoring_visits_completed": 2,
    }


def _make_visit(site_id: str, actual_date: str = "2024-01-15") -> dict:
    return {
        "visit_id": f"V-{site_id}-001",
        "patient_id": "P001",
        "site_id": site_id,
        "protocol_visit_id": "V02",
        "scheduled_date": actual_date,
        "actual_date": actual_date,
        "dose_administered_mg": 200,
        "assessments_completed": [],
        "data_entry_date": actual_date,
    }


class TestRiskScoring:
    def test_contributions_sum_to_total(self, protocol_spec):
        deviations = [_make_deviation("SITE-001", "out_of_window")]
        sites = [_make_site("SITE-001")]
        patients = [{"patient_id": "P001", "site_id": "SITE-001"}]
        visits = [_make_visit("SITE-001")]

        scores = score_sites(deviations, sites, patients, visits)
        assert len(scores) == 1
        score = scores[0]

        total_from_contributions = sum(ind.contribution for ind in score.indicators)
        assert abs(total_from_contributions - score.score) < 0.01, (
            f"Contributions sum ({total_from_contributions:.3f}) ≠ score ({score.score:.3f})"
        )

    def test_weights_sum_to_one(self):
        w = DEFAULT_WEIGHTS
        total = (
            w.severity_weighted_rate + w.recurrence_rate + w.mean_time_to_detection
            + w.deviation_trend + w.enrollment_velocity_risk + w.monitoring_coverage
        )
        assert abs(total - 1.0) < 1e-6

    def test_score_in_0_100_range(self, protocol_spec):
        deviations = [_make_deviation("SITE-001", "major_type", "Major") for _ in range(50)]
        sites = [_make_site("SITE-001")]
        patients = [{"patient_id": f"P{i}", "site_id": "SITE-001"} for i in range(10)]
        visits = [_make_visit("SITE-001", f"2024-0{i+1}-15") for i in range(5)]

        scores = score_sites(deviations, sites, patients, visits)
        for s in scores:
            assert 0 <= s.score <= 100

    def test_risk_bands_assigned_correctly(self, protocol_spec):
        from backend.risk.config import get_risk_band
        assert get_risk_band(10) == "Low"
        assert get_risk_band(29) == "Low"
        assert get_risk_band(30) == "Medium"
        assert get_risk_band(59) == "Medium"
        assert get_risk_band(60) == "High"
        assert get_risk_band(79) == "High"
        assert get_risk_band(80) == "Critical"
        assert get_risk_band(100) == "Critical"

    def test_high_risk_site_scores_higher_than_clean_site(self, protocol_spec):
        devs_risky = [_make_deviation("SITE-BAD", "out_of_window", "Major") for _ in range(20)]
        devs_clean: list[Deviation] = []
        sites = [_make_site("SITE-BAD"), _make_site("SITE-CLEAN")]
        patients = [
            {"patient_id": f"P{i}", "site_id": "SITE-BAD"} for i in range(5)
        ] + [
            {"patient_id": f"Q{i}", "site_id": "SITE-CLEAN"} for i in range(5)
        ]
        visits = [_make_visit("SITE-BAD")] * 5 + [_make_visit("SITE-CLEAN")] * 5

        scores = score_sites(devs_risky + devs_clean, sites, patients, visits)
        score_map = {s.site_id: s.score for s in scores}
        assert score_map["SITE-BAD"] > score_map["SITE-CLEAN"]

    def test_each_indicator_has_six_entries(self, protocol_spec):
        sites = [_make_site("SITE-001")]
        scores = score_sites([], sites, [], [])
        assert len(scores[0].indicators) == 6

    def test_sorted_descending_by_score(self, protocol_spec):
        devs = [_make_deviation("SITE-001", "out_of_window", "Major")] * 15
        sites = [_make_site("SITE-001"), _make_site("SITE-002")]
        patients = [{"patient_id": "P1", "site_id": "SITE-001"}]
        visits = [_make_visit("SITE-001")] * 5

        scores = score_sites(devs, sites, patients, visits)
        score_values = [s.score for s in scores]
        assert score_values == sorted(score_values, reverse=True)
