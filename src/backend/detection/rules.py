"""Individual deviation detection rules.

Each rule accepts a pandas DataFrame slice and a ProtocolSpec and returns
a list[Deviation].  All rules use vectorized operations — no row-by-row loops.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from backend.detection.models import Deviation
from backend.protocol.schema import ProtocolSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SAFETY_CRITICAL_VISITS = {"V01", "V04", "V06", "V08", "V09"}


def _visit_map(spec: ProtocolSpec) -> dict[str, Any]:
    """Return a dict keyed by visit_id with visit definition data."""
    return {v.visit_id: v for v in spec.visit_schedule}


def _business_days_between(start: pd.Series, end: pd.Series) -> pd.Series:
    """Vectorized business-day count between two datetime64 Series."""
    delta = (end - start).dt.days
    weeks = delta // 7
    remainder = delta % 7
    start_dow = start.dt.dayofweek
    # Count weekday days in the remainder
    biz = weeks * 5
    for d in range(7):
        mask = (start_dow + d) % 7
        biz += ((mask < 5) & (d < remainder)).astype(int)
    return biz


# ---------------------------------------------------------------------------
# Rule: check_visit_window
# ---------------------------------------------------------------------------

def check_visit_window(visits_df: pd.DataFrame, spec: ProtocolSpec) -> list[Deviation]:
    """Detect visits where actual_date falls outside target_day ± window."""
    vmap = _visit_map(spec)
    deviations: list[Deviation] = []

    df = visits_df.dropna(subset=["actual_date"]).copy()
    df["actual_date_dt"] = pd.to_datetime(df["actual_date"])
    df["scheduled_date_dt"] = pd.to_datetime(df["scheduled_date"])

    for pv_id, vdef in vmap.items():
        subset = df[df["protocol_visit_id"] == pv_id].copy()
        if subset.empty:
            continue

        subset["delta"] = (
            subset["actual_date_dt"] - subset["scheduled_date_dt"]
        ).dt.days

        mask_early = subset["delta"] < -vdef.window_before_days
        mask_late = subset["delta"] > vdef.window_after_days
        oow = subset[mask_early | mask_late]

        for _, row in oow.iterrows():
            delta = int(row["delta"])
            deviations.append(Deviation(
                patient_id=row["patient_id"],
                site_id=row["site_id"],
                visit_id=row["visit_id"],
                rule_id="R001",
                deviation_type="out_of_window",
                magnitude=abs(delta),
                is_safety_critical=vdef.is_safety_critical,
                context={
                    "protocol_visit_id": pv_id,
                    "target_day": vdef.target_day,
                    "window_before_days": vdef.window_before_days,
                    "window_after_days": vdef.window_after_days,
                    "scheduled_date": row["scheduled_date"],
                    "actual_date": row["actual_date"],
                    "delta_days": delta,
                },
            ))
    return deviations


# ---------------------------------------------------------------------------
# Rule: check_missed_visit
# ---------------------------------------------------------------------------

def check_missed_visit(
    visits_df: pd.DataFrame, spec: ProtocolSpec, reference_date: date | None = None
) -> list[Deviation]:
    """Detect visits with no actual_date where the window has already closed."""
    ref = reference_date or date.today()
    vmap = _visit_map(spec)
    deviations: list[Deviation] = []

    missed = visits_df[visits_df["actual_date"].isna()].copy()
    missed["scheduled_date_dt"] = pd.to_datetime(missed["scheduled_date"])

    for pv_id, vdef in vmap.items():
        subset = missed[missed["protocol_visit_id"] == pv_id].copy()
        if subset.empty:
            continue

        window_close = subset["scheduled_date_dt"] + pd.Timedelta(days=vdef.window_after_days)
        past_window = window_close < pd.Timestamp(ref)
        overdue = subset[past_window]

        for _, row in overdue.iterrows():
            deviations.append(Deviation(
                patient_id=row["patient_id"],
                site_id=row["site_id"],
                visit_id=row["visit_id"],
                rule_id="R002",
                deviation_type="missed_visit",
                is_safety_critical=vdef.is_safety_critical,
                context={
                    "protocol_visit_id": pv_id,
                    "scheduled_date": row["scheduled_date"],
                    "window_after_days": vdef.window_after_days,
                    "is_safety_critical": vdef.is_safety_critical,
                },
            ))
    return deviations


# ---------------------------------------------------------------------------
# Rule: check_dosing
# ---------------------------------------------------------------------------

def check_dosing(visits_df: pd.DataFrame, spec: ProtocolSpec) -> list[Deviation]:
    """Detect doses outside the acceptable range defined in the protocol."""
    dosing = spec.dosing
    deviations: list[Deviation] = []

    df = visits_df.dropna(subset=["actual_date", "dose_administered_mg"]).copy()
    df["dose_administered_mg"] = pd.to_numeric(df["dose_administered_mg"], errors="coerce")
    df = df.dropna(subset=["dose_administered_mg"])

    mask_low = df["dose_administered_mg"] < dosing.min_acceptable_dose_mg
    mask_high = df["dose_administered_mg"] > dosing.max_acceptable_dose_mg
    bad = df[mask_low | mask_high]

    for _, row in bad.iterrows():
        dose = float(row["dose_administered_mg"])
        pct_dev = round((dose - dosing.planned_dose_mg) / dosing.planned_dose_mg * 100, 2)
        pv_id = row.get("protocol_visit_id", "")
        vdef = vmap_by_id(spec, pv_id)
        is_sc = vdef.is_safety_critical if vdef else False

        deviations.append(Deviation(
            patient_id=row["patient_id"],
            site_id=row["site_id"],
            visit_id=row["visit_id"],
            rule_id="R003",
            deviation_type="dosing_deviation",
            magnitude=abs(pct_dev),
            is_safety_critical=is_sc,
            context={
                "protocol_visit_id": pv_id,
                "drug_name": dosing.drug_name,
                "planned_dose_mg": dosing.planned_dose_mg,
                "min_acceptable_dose_mg": dosing.min_acceptable_dose_mg,
                "max_acceptable_dose_mg": dosing.max_acceptable_dose_mg,
                "dose_administered_mg": dose,
                "pct_deviation_from_planned": pct_dev,
            },
        ))
    return deviations


def vmap_by_id(spec: ProtocolSpec, visit_id: str):
    for v in spec.visit_schedule:
        if v.visit_id == visit_id:
            return v
    return None


# ---------------------------------------------------------------------------
# Rule: check_prohibited_medication
# ---------------------------------------------------------------------------

def check_prohibited_medication(
    conmeds_df: pd.DataFrame,
    visits_df: pd.DataFrame,
    spec: ProtocolSpec,
) -> list[Deviation]:
    """Detect concomitant meds overlapping the treatment period that match the prohibited list."""
    if conmeds_df.empty:
        return []

    # Treatment period per patient: first actual visit → last actual visit
    active_visits = visits_df.dropna(subset=["actual_date"]).copy()
    active_visits["actual_date_dt"] = pd.to_datetime(active_visits["actual_date"])

    treatment_window = (
        active_visits.groupby("patient_id")["actual_date_dt"]
        .agg(tx_start="min", tx_end="max")
        .reset_index()
    )

    prohibited_names = {m.drug_name.lower() for m in spec.prohibited_medications}
    prohibited_atc = {m.atc_class.lower() for m in spec.prohibited_medications}
    severity_floor_map = {
        m.drug_name.lower(): m.severity_floor.value for m in spec.prohibited_medications
    }
    severity_floor_map.update(
        {m.atc_class.lower(): m.severity_floor.value for m in spec.prohibited_medications}
    )

    df = conmeds_df.copy()
    df["start_dt"] = pd.to_datetime(df["start_date"])
    df["stop_dt"] = pd.to_datetime(df["stop_date"])
    df["drug_lower"] = df["drug_name"].str.lower()
    df["atc_lower"] = df["atc_class"].str.lower()

    is_prohibited = df["drug_lower"].isin(prohibited_names) | df["atc_lower"].isin(prohibited_atc)
    df = df[is_prohibited].merge(treatment_window, on="patient_id", how="inner")

    # Overlap: conmed_start ≤ tx_end AND conmed_stop ≥ tx_start
    overlaps = df[(df["start_dt"] <= df["tx_end"]) & (df["stop_dt"] >= df["tx_start"])]

    deviations: list[Deviation] = []
    for _, row in overlaps.iterrows():
        dname = row["drug_lower"]
        aclass = row["atc_lower"]
        sev_floor = severity_floor_map.get(dname) or severity_floor_map.get(aclass, "Major")

        deviations.append(Deviation(
            patient_id=row["patient_id"],
            site_id=row["site_id"],
            visit_id=None,
            rule_id="R004",
            deviation_type="prohibited_medication",
            is_safety_critical=True,
            context={
                "drug_name": row["drug_name"],
                "atc_class": row["atc_class"],
                "start_date": row["start_date"],
                "stop_date": row["stop_date"],
                "treatment_start": str(row["tx_start"].date()),
                "treatment_end": str(row["tx_end"].date()),
                "severity_floor": sev_floor,
                "reason": next(
                    (m.reason for m in spec.prohibited_medications
                     if m.drug_name.lower() == dname or m.atc_class.lower() == aclass),
                    "Prohibited medication",
                ),
            },
        ))
    return deviations


# ---------------------------------------------------------------------------
# Rule: check_missing_assessments
# ---------------------------------------------------------------------------

def check_missing_assessments(visits_df: pd.DataFrame, spec: ProtocolSpec) -> list[Deviation]:
    """Detect required assessments that were not completed at a given visit."""
    vmap = _visit_map(spec)
    deviations: list[Deviation] = []

    df = visits_df.dropna(subset=["actual_date"]).copy()

    for pv_id, vdef in vmap.items():
        if not vdef.required_assessments:
            continue
        subset = df[df["protocol_visit_id"] == pv_id]
        if subset.empty:
            continue

        required_set = set(vdef.required_assessments)

        for _, row in subset.iterrows():
            completed = set(row["assessments_completed"] or [])
            missing = required_set - completed
            if not missing:
                continue

            # Determine if any missing assessment is safety-critical
            is_sc = any(
                spec.required_assessments.get(a, type("", (), {"is_safety_critical": False})()).is_safety_critical  # type: ignore[union-attr]
                for a in missing
            ) or vdef.is_safety_critical

            deviations.append(Deviation(
                patient_id=row["patient_id"],
                site_id=row["site_id"],
                visit_id=row["visit_id"],
                rule_id="R005",
                deviation_type="missing_assessment",
                magnitude=float(len(missing)),
                is_safety_critical=is_sc,
                context={
                    "protocol_visit_id": pv_id,
                    "required_assessments": sorted(required_set),
                    "completed_assessments": sorted(completed),
                    "missing_assessments": sorted(missing),
                    "is_safety_critical_visit": vdef.is_safety_critical,
                },
            ))
    return deviations


# ---------------------------------------------------------------------------
# Rule: check_late_data_entry
# ---------------------------------------------------------------------------

def check_late_data_entry(visits_df: pd.DataFrame, _spec: ProtocolSpec) -> list[Deviation]:
    """Detect data_entry_date more than 5 business days after actual_date."""
    df = visits_df.dropna(subset=["actual_date", "data_entry_date"]).copy()
    df["actual_dt"] = pd.to_datetime(df["actual_date"])
    df["entry_dt"] = pd.to_datetime(df["data_entry_date"])

    df["biz_days"] = _business_days_between(df["actual_dt"], df["entry_dt"])
    late = df[df["biz_days"] > 5]

    deviations: list[Deviation] = []
    for _, row in late.iterrows():
        deviations.append(Deviation(
            patient_id=row["patient_id"],
            site_id=row["site_id"],
            visit_id=row["visit_id"],
            rule_id="R006",
            deviation_type="late_data_entry",
            magnitude=float(row["biz_days"]),
            is_safety_critical=False,
            context={
                "protocol_visit_id": row.get("protocol_visit_id", ""),
                "actual_date": row["actual_date"],
                "data_entry_date": row["data_entry_date"],
                "business_days_lag": int(row["biz_days"]),
                "threshold_business_days": 5,
            },
        ))
    return deviations


# ---------------------------------------------------------------------------
# Rule: check_eligibility_violation
# ---------------------------------------------------------------------------

_NUMERIC_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def check_eligibility_violation(
    eligibility_df: pd.DataFrame, spec: ProtocolSpec
) -> list[Deviation]:
    """Detect patients whose baseline data violates inclusion/exclusion criteria."""
    criteria = (
        list(spec.eligibility_criteria.inclusion)
        + list(spec.eligibility_criteria.exclusion)
    )
    deviations: list[Deviation] = []

    for criterion in criteria:
        field = criterion.field
        if field not in eligibility_df.columns:
            continue

        op = criterion.operator
        threshold = criterion.value

        if op == "in":
            mask_ok = eligibility_df[field].isin(threshold)
        elif op in _NUMERIC_OPS:
            mask_ok = _NUMERIC_OPS[op](eligibility_df[field], threshold)
        else:
            continue  # unknown operator; skip

        violators = eligibility_df[~mask_ok]
        for _, row in violators.iterrows():
            deviations.append(Deviation(
                patient_id=row["patient_id"],
                site_id=row["site_id"],
                visit_id=None,
                rule_id="R007",
                deviation_type="eligibility_violation",
                is_safety_critical=True,
                context={
                    "criterion_id": criterion.criterion_id,
                    "description": criterion.description,
                    "field": field,
                    "operator": op,
                    "threshold": threshold,
                    "observed_value": row[field],
                },
            ))
    return deviations
