"""Tests for individual detection rules — including boundary conditions."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from backend.detection.rules import (
    check_dosing,
    check_eligibility_violation,
    check_late_data_entry,
    check_missing_assessments,
    check_missed_visit,
    check_prohibited_medication,
    check_visit_window,
)


# ---------------------------------------------------------------------------
# check_visit_window
# ---------------------------------------------------------------------------

class TestCheckVisitWindow:
    def _make_visit(self, actual_offset_days: int, pv_id: str = "V02") -> list[dict]:
        """Create a single visit with actual_date offset from scheduled_date."""
        scheduled = date(2024, 3, 1)
        actual = scheduled + timedelta(days=actual_offset_days)
        return [{
            "visit_id": "V-001",
            "patient_id": "P001",
            "site_id": "SITE-001",
            "protocol_visit_id": pv_id,
            "scheduled_date": scheduled.isoformat(),
            "actual_date": actual.isoformat(),
            "dose_administered_mg": 200.0,
            "assessments_completed": [],
            "data_entry_date": actual.isoformat(),
        }]

    def test_within_window_no_deviation(self, protocol_spec):
        # V02: window -2/+2 — offset of 0 should be clean
        rows = self._make_visit(0)
        devs = check_visit_window(pd.DataFrame(rows), protocol_spec)
        assert devs == []

    def test_exactly_at_window_edge_no_deviation(self, protocol_spec):
        # V02 window +2 — exactly +2 days → no deviation
        rows = self._make_visit(+2)
        devs = check_visit_window(pd.DataFrame(rows), protocol_spec)
        assert devs == []

    def test_exactly_at_negative_window_edge_no_deviation(self, protocol_spec):
        # V02 window -2 — exactly -2 days → no deviation
        rows = self._make_visit(-2)
        devs = check_visit_window(pd.DataFrame(rows), protocol_spec)
        assert devs == []

    def test_one_day_outside_late_window_is_deviation(self, protocol_spec):
        # V02 window +2 — +3 days → deviation
        rows = self._make_visit(+3)
        devs = check_visit_window(pd.DataFrame(rows), protocol_spec)
        assert len(devs) == 1
        assert devs[0].deviation_type == "out_of_window"
        assert devs[0].magnitude == 3

    def test_one_day_outside_early_window_is_deviation(self, protocol_spec):
        # V02 window -2 — -3 days → deviation
        rows = self._make_visit(-3)
        devs = check_visit_window(pd.DataFrame(rows), protocol_spec)
        assert len(devs) == 1
        assert devs[0].magnitude == 3

    def test_no_actual_date_skipped(self, protocol_spec):
        rows = [{
            "visit_id": "V-001", "patient_id": "P001", "site_id": "SITE-001",
            "protocol_visit_id": "V02", "scheduled_date": "2024-03-01",
            "actual_date": None, "dose_administered_mg": None,
            "assessments_completed": [], "data_entry_date": "2024-03-05",
        }]
        devs = check_visit_window(pd.DataFrame(rows), protocol_spec)
        assert devs == []


# ---------------------------------------------------------------------------
# check_missed_visit
# ---------------------------------------------------------------------------

class TestCheckMissedVisit:
    def _make_missed(self, scheduled_days_ago: int, pv_id: str = "V02") -> list[dict]:
        scheduled = date.today() - timedelta(days=scheduled_days_ago)
        return [{
            "visit_id": "V-002", "patient_id": "P001", "site_id": "SITE-001",
            "protocol_visit_id": pv_id, "scheduled_date": scheduled.isoformat(),
            "actual_date": None, "dose_administered_mg": None,
            "assessments_completed": [], "data_entry_date": scheduled.isoformat(),
        }]

    def test_past_window_is_missed(self, protocol_spec):
        # V02 window +2 — scheduled 30 days ago, no actual date → missed
        rows = self._make_missed(30)
        devs = check_missed_visit(pd.DataFrame(rows), protocol_spec)
        assert len(devs) == 1
        assert devs[0].deviation_type == "missed_visit"

    def test_window_not_closed_is_not_missed(self, protocol_spec):
        # V02 window +2 — scheduled yesterday, still in window → not missed
        rows = self._make_missed(1)
        devs = check_missed_visit(pd.DataFrame(rows), protocol_spec)
        assert devs == []

    def test_has_actual_date_not_missed(self, protocol_spec):
        rows = [{
            "visit_id": "V-003", "patient_id": "P001", "site_id": "SITE-001",
            "protocol_visit_id": "V02", "scheduled_date": "2023-01-01",
            "actual_date": "2023-01-02", "dose_administered_mg": 200,
            "assessments_completed": [], "data_entry_date": "2023-01-05",
        }]
        devs = check_missed_visit(pd.DataFrame(rows), protocol_spec)
        assert devs == []


# ---------------------------------------------------------------------------
# check_dosing
# ---------------------------------------------------------------------------

class TestCheckDosing:
    def _make_dose_visit(self, dose_mg: float) -> list[dict]:
        return [{
            "visit_id": "V-004", "patient_id": "P001", "site_id": "SITE-001",
            "protocol_visit_id": "V02", "scheduled_date": "2024-01-15",
            "actual_date": "2024-01-15", "dose_administered_mg": dose_mg,
            "assessments_completed": [], "data_entry_date": "2024-01-16",
        }]

    def test_planned_dose_no_deviation(self, protocol_spec):
        devs = check_dosing(pd.DataFrame(self._make_dose_visit(200.0)), protocol_spec)
        assert devs == []

    def test_at_minimum_no_deviation(self, protocol_spec):
        # min=160 — exactly 160 → no deviation
        devs = check_dosing(pd.DataFrame(self._make_dose_visit(160.0)), protocol_spec)
        assert devs == []

    def test_at_maximum_no_deviation(self, protocol_spec):
        # max=240 — exactly 240 → no deviation
        devs = check_dosing(pd.DataFrame(self._make_dose_visit(240.0)), protocol_spec)
        assert devs == []

    def test_below_minimum_is_deviation(self, protocol_spec):
        devs = check_dosing(pd.DataFrame(self._make_dose_visit(159.0)), protocol_spec)
        assert len(devs) == 1
        assert devs[0].deviation_type == "dosing_deviation"
        assert devs[0].magnitude > 0  # % deviation from planned

    def test_above_maximum_is_deviation(self, protocol_spec):
        devs = check_dosing(pd.DataFrame(self._make_dose_visit(241.0)), protocol_spec)
        assert len(devs) == 1

    def test_no_actual_date_skipped(self, protocol_spec):
        rows = self._make_dose_visit(50.0)
        rows[0]["actual_date"] = None
        devs = check_dosing(pd.DataFrame(rows), protocol_spec)
        assert devs == []


# ---------------------------------------------------------------------------
# check_prohibited_medication
# ---------------------------------------------------------------------------

class TestCheckProhibitedMedication:
    def _make_conmed(self, drug: str, atc: str, overlap: bool = True) -> list[dict]:
        if overlap:
            # Treatment period: 2024-01-01 to 2024-06-01; conmed overlaps
            return [{
                "record_id": "C001", "patient_id": "P001", "site_id": "SITE-001",
                "drug_name": drug, "atc_class": atc,
                "start_date": "2024-01-15", "stop_date": "2024-03-01",
            }]
        else:
            # Conmed before treatment period
            return [{
                "record_id": "C001", "patient_id": "P001", "site_id": "SITE-001",
                "drug_name": drug, "atc_class": atc,
                "start_date": "2023-06-01", "stop_date": "2023-12-31",
            }]

    def _make_visits(self) -> list[dict]:
        return [
            {
                "visit_id": "V-001", "patient_id": "P001", "site_id": "SITE-001",
                "protocol_visit_id": "V01", "scheduled_date": "2024-01-01",
                "actual_date": "2024-01-01", "dose_administered_mg": 200,
                "assessments_completed": [], "data_entry_date": "2024-01-02",
            },
            {
                "visit_id": "V-002", "patient_id": "P001", "site_id": "SITE-001",
                "protocol_visit_id": "V08", "scheduled_date": "2024-06-01",
                "actual_date": "2024-06-01", "dose_administered_mg": 200,
                "assessments_completed": [], "data_entry_date": "2024-06-02",
            },
        ]

    def test_prohibited_drug_overlap_detected(self, protocol_spec):
        conmeds = self._make_conmed("Warfarin", "B01AA03", overlap=True)
        visits = self._make_visits()
        devs = check_prohibited_medication(
            pd.DataFrame(conmeds), pd.DataFrame(visits), protocol_spec
        )
        assert len(devs) >= 1
        assert devs[0].deviation_type == "prohibited_medication"
        assert devs[0].is_safety_critical is True

    def test_prohibited_drug_no_overlap_not_detected(self, protocol_spec):
        conmeds = self._make_conmed("Warfarin", "B01AA03", overlap=False)
        visits = self._make_visits()
        devs = check_prohibited_medication(
            pd.DataFrame(conmeds), pd.DataFrame(visits), protocol_spec
        )
        assert devs == []

    def test_safe_drug_not_flagged(self, protocol_spec):
        conmeds = self._make_conmed("Omeprazole", "A02BC01", overlap=True)
        visits = self._make_visits()
        devs = check_prohibited_medication(
            pd.DataFrame(conmeds), pd.DataFrame(visits), protocol_spec
        )
        assert devs == []


# ---------------------------------------------------------------------------
# check_missing_assessments
# ---------------------------------------------------------------------------

class TestCheckMissingAssessments:
    def _make_visit(self, completed: list[str], pv_id: str = "V02") -> list[dict]:
        return [{
            "visit_id": "V-005", "patient_id": "P001", "site_id": "SITE-001",
            "protocol_visit_id": pv_id, "scheduled_date": "2024-02-01",
            "actual_date": "2024-02-01", "dose_administered_mg": 200,
            "assessments_completed": completed, "data_entry_date": "2024-02-02",
        }]

    def test_all_assessments_complete_no_deviation(self, protocol_spec):
        # V02 requires: VITALS, LABS_CBC, AE_REVIEW, DOSE_ADMIN
        rows = self._make_visit(["VITALS", "LABS_CBC", "AE_REVIEW", "DOSE_ADMIN"])
        devs = check_missing_assessments(pd.DataFrame(rows), protocol_spec)
        assert devs == []

    def test_missing_one_assessment_detected(self, protocol_spec):
        rows = self._make_visit(["VITALS", "LABS_CBC"])  # missing AE_REVIEW, DOSE_ADMIN
        devs = check_missing_assessments(pd.DataFrame(rows), protocol_spec)
        assert len(devs) == 1
        missing = devs[0].context["missing_assessments"]
        assert "AE_REVIEW" in missing or "DOSE_ADMIN" in missing


# ---------------------------------------------------------------------------
# check_late_data_entry
# ---------------------------------------------------------------------------

class TestCheckLateDataEntry:
    def _make_visit(self, actual: str, entry: str) -> list[dict]:
        return [{
            "visit_id": "V-006", "patient_id": "P001", "site_id": "SITE-001",
            "protocol_visit_id": "V02", "scheduled_date": actual,
            "actual_date": actual, "dose_administered_mg": 200,
            "assessments_completed": [], "data_entry_date": entry,
        }]

    def test_same_day_not_late(self, protocol_spec):
        devs = check_late_data_entry(
            pd.DataFrame(self._make_visit("2024-03-01", "2024-03-01")), protocol_spec
        )
        assert devs == []

    def test_exactly_5_business_days_not_late(self, protocol_spec):
        # Mon → Mon + 5 biz days = the following Monday
        devs = check_late_data_entry(
            pd.DataFrame(self._make_visit("2024-03-04", "2024-03-11")), protocol_spec
        )
        assert devs == []

    def test_6_business_days_is_late(self, protocol_spec):
        # Mon → following Tuesday = 6 biz days
        devs = check_late_data_entry(
            pd.DataFrame(self._make_visit("2024-03-04", "2024-03-12")), protocol_spec
        )
        assert len(devs) == 1
        assert devs[0].magnitude == 6


# ---------------------------------------------------------------------------
# check_eligibility_violation
# ---------------------------------------------------------------------------

class TestCheckEligibilityViolation:
    def _make_patient(self, **overrides) -> list[dict]:
        base = {
            "patient_id": "P001", "site_id": "SITE-001",
            "age": 45, "diagnosis_code": "NSCLC_IV",
            "ecog_ps": 1, "creatinine_clearance_ml_min": 70.0,
            "alt_x_uln": 1.5, "qtcf_ms": 440,
            "prior_cdk46_inhibitor": False,
            "active_cns_mets": False,
            "pregnant_or_breastfeeding": False,
        }
        base.update(overrides)
        return [base]

    def test_clean_patient_no_violations(self, protocol_spec):
        rows = self._make_patient()
        devs = check_eligibility_violation(pd.DataFrame(rows), protocol_spec)
        assert devs == []

    def test_underage_patient_flagged(self, protocol_spec):
        rows = self._make_patient(age=16)
        devs = check_eligibility_violation(pd.DataFrame(rows), protocol_spec)
        criterion_ids = [d.context["criterion_id"] for d in devs]
        assert "INC01" in criterion_ids

    def test_ecog_ps_violation_flagged(self, protocol_spec):
        rows = self._make_patient(ecog_ps=3)
        devs = check_eligibility_violation(pd.DataFrame(rows), protocol_spec)
        criterion_ids = [d.context["criterion_id"] for d in devs]
        assert "INC03" in criterion_ids

    def test_qtcf_exclusion_violation_flagged(self, protocol_spec):
        rows = self._make_patient(qtcf_ms=480)
        devs = check_eligibility_violation(pd.DataFrame(rows), protocol_spec)
        criterion_ids = [d.context["criterion_id"] for d in devs]
        assert "EXC02" in criterion_ids

    def test_all_violations_are_safety_critical(self, protocol_spec):
        rows = self._make_patient(age=16, ecog_ps=3)
        devs = check_eligibility_violation(pd.DataFrame(rows), protocol_spec)
        assert all(d.is_safety_critical for d in devs)
