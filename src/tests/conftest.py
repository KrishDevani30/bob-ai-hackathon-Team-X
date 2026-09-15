"""conftest.py — shared pytest fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure src is on the path for all tests
SRC = Path(__file__).parent.parent
sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def protocol_spec():
    from backend.protocol.loader import load_protocol
    spec_path = SRC / "data" / "protocol_spec.json"
    return load_protocol(spec_path)


@pytest.fixture(scope="session")
def synthetic_data(tmp_path_factory):
    """Generate synthetic data once per test session."""
    from data.generate_synthetic_data import main
    out_dir = tmp_path_factory.mktemp("data")
    main(seed=42, out_dir=out_dir)
    return out_dir


@pytest.fixture(scope="session")
def ground_truth(synthetic_data):
    gt_path = synthetic_data / "ground_truth.json"
    return json.loads(gt_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def all_data(synthetic_data):
    def _r(name):
        return json.loads((synthetic_data / name).read_text(encoding="utf-8"))
    return {
        "sites": _r("sites.json"),
        "patients": _r("patients.json"),
        "visits": _r("visits.json"),
        "conmeds": _r("conmed_records.json"),
        "eligibility": _r("eligibility_data.json"),
    }


@pytest.fixture(scope="session")
def mock_llm():
    from backend.llm.mock import MockLLMClient
    return MockLLMClient()
