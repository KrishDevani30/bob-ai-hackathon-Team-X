"""Detection engine — runs all rules over the full dataset."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from backend.detection.models import Deviation
from backend.detection.rules import (
    check_dosing,
    check_eligibility_violation,
    check_late_data_entry,
    check_missing_assessments,
    check_missed_visit,
    check_prohibited_medication,
    check_visit_window,
)
from backend.protocol.schema import ProtocolSpec


class DetectionEngine:
    """Orchestrates all deviation rules over a dataset."""

    def __init__(self, spec: ProtocolSpec) -> None:
        self.spec = spec

    def run(
        self,
        visits: list[dict[str, Any]],
        conmeds: list[dict[str, Any]],
        eligibility: list[dict[str, Any]],
        reference_date: date | None = None,
    ) -> list[Deviation]:
        """Run all rules and return combined deviations.

        Args:
            visits: raw visit record dicts.
            conmeds: concomitant medication record dicts.
            eligibility: patient eligibility data dicts.
            reference_date: override today's date for missed-visit window calculation.

        Returns:
            Flat list of all detected Deviation objects.
        """
        t0 = time.perf_counter()

        visits_df = _to_df(visits)
        conmeds_df = _to_df(conmeds)
        elig_df = _to_df(eligibility)

        deviations: list[Deviation] = []

        if not visits_df.empty:
            deviations += check_visit_window(visits_df, self.spec)
            deviations += check_missed_visit(visits_df, self.spec, reference_date)
            deviations += check_dosing(visits_df, self.spec)
            deviations += check_missing_assessments(visits_df, self.spec)
            deviations += check_late_data_entry(visits_df, self.spec)

        if not conmeds_df.empty and not visits_df.empty:
            deviations += check_prohibited_medication(conmeds_df, visits_df, self.spec)

        if not elig_df.empty:
            deviations += check_eligibility_violation(elig_df, self.spec)

        elapsed = time.perf_counter() - t0
        print(f"[engine] detected {len(deviations)} deviations in {elapsed:.2f}s")
        return deviations


def _to_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def load_engine_from_files(
    spec: ProtocolSpec,
    data_dir: Path,
) -> tuple["DetectionEngine", list[dict], list[dict], list[dict]]:
    """Convenience loader — reads JSON files and returns engine + data."""
    import json

    def _read(name: str) -> list[dict]:
        p = data_dir / name
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8"))

    engine = DetectionEngine(spec)
    visits = _read("visits.json")
    conmeds = _read("conmed_records.json")
    eligibility = _read("eligibility_data.json")
    return engine, visits, conmeds, eligibility
