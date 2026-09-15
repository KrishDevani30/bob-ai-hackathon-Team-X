"""Load and validate a protocol specification JSON file."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from backend.protocol.schema import ProtocolSpec

_DEFAULT_SPEC_PATH = Path(__file__).parent.parent.parent / "data" / "protocol_spec.json"


def load_protocol(path: Path | str | None = None) -> ProtocolSpec:
    """Parse and validate a protocol spec from *path*.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError: if the JSON is malformed or fails Pydantic validation.
    """
    resolved = Path(path) if path else _DEFAULT_SPEC_PATH
    if not resolved.exists():
        raise FileNotFoundError(f"Protocol spec not found: {resolved}")

    raw = resolved.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Protocol spec is not valid JSON: {exc}") from exc

    try:
        spec = ProtocolSpec.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Protocol spec validation failed:\n{exc}") from exc

    return spec
