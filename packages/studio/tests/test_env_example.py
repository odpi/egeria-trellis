"""TR-48: packages/studio/.env.example names every setting Studio reads, with no secret values in it."""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import AliasChoices

from trellis_studio.config import StudioSettings

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _names(field) -> list:
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return [c for c in alias.choices if isinstance(c, str)]
    return [alias] if isinstance(alias, str) else []


def test_every_setting_studio_reads_is_in_the_example():
    text = EXAMPLE.read_text()
    for name, field in StudioSettings.model_fields.items():
        names = _names(field)
        assert names, name
        assert any(re.search(rf"\b{re.escape(n)}\b", text) for n in names), f"{name} ({names}) is not in .env.example"


def test_the_jwt_secret_is_required_and_no_secret_has_a_value():
    text = EXAMPLE.read_text()
    assert re.search(r"^TRELLIS_JWT_SECRET=$", text, re.M)
    assert "Required" in text
    for key in ("TRELLIS_JWT_SECRET", "TRELLIS_PORTAL_SECRET", "STUDIO_PORTAL_SECRET"):
        for m in re.finditer(rf"^#?\s*{key}=(.*)$", text, re.M):
            assert m.group(1).strip() == "", f"{key} has a value in the example"
    assert not re.search(r"^EGERIA_USER(_PASSWORD)?=", text, re.M)        # Studio has no service account
