"""Tests for the protocol loader and schema validation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from backend.protocol.loader import load_protocol
from backend.protocol.schema import ProtocolSpec


def test_load_valid_spec(protocol_spec):
    assert protocol_spec.study_id == "ONC-2024-001"
    assert len(protocol_spec.visit_schedule) >= 9
    assert protocol_spec.dosing.drug_name == "Veloranib"
    assert len(protocol_spec.prohibited_medications) > 0


def test_all_assessment_codes_in_catalog(protocol_spec):
    known = set(protocol_spec.required_assessments.keys())
    for visit in protocol_spec.visit_schedule:
        for code in visit.required_assessments:
            assert code in known, f"Visit {visit.visit_id} references unknown code {code}"


def test_dosing_min_lte_max(protocol_spec):
    d = protocol_spec.dosing
    assert d.min_acceptable_dose_mg <= d.max_acceptable_dose_mg


def test_malformed_json_raises():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        f.write("{not valid json")
        path = Path(f.name)
    with pytest.raises(ValueError, match="not valid JSON"):
        load_protocol(path)
    path.unlink()


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_protocol(Path("/nonexistent/path/spec.json"))


def test_invalid_schema_raises():
    bad = {"study_id": "X", "protocol_version": "1"}  # missing required fields
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(bad, f)
        path = Path(f.name)
    with pytest.raises(ValueError, match="validation failed"):
        load_protocol(path)
    path.unlink()


def test_unknown_assessment_code_raises():
    from backend.protocol.schema import (
        ProtocolSpec, VisitDefinition, DosingSpec, AssessmentDefinition
    )
    with pytest.raises(ValueError, match="unknown assessment codes"):
        ProtocolSpec(
            study_id="X", protocol_version="1", title="T",
            visit_schedule=[
                VisitDefinition(
                    visit_id="V01", visit_name="V1", target_day=0,
                    window_before_days=1, window_after_days=1,
                    is_safety_critical=False,
                    required_assessments=["NONEXISTENT_CODE"],
                )
            ],
            dosing=DosingSpec(
                drug_name="Drug", planned_dose_mg=100, dose_unit="mg",
                frequency="QD", min_acceptable_dose_mg=80, max_acceptable_dose_mg=120,
            ),
            required_assessments={"VITALS": AssessmentDefinition(name="Vitals", is_safety_critical=False)},
        )
