"""The vendored `general_type` CASE (`configdata/quality/testgen/postgresql/general_type.sql`), ported to Python.

`general_type` (A text, B boolean, D date/timestamp, T time, N number, X anything else) decides which measures
the value profile takes for a column and which selection rules can apply. It is derived from the column's
catalog type, never measured. The CASE is not run against the target: RE already holds every column's type in
its stored inventory. Like the selection rules, the port is pinned to the SHA-256 of the CASE text it came from
(`GENERAL_TYPE_CASE_SHA256`), checked by tests/test_quality_catalogue.py.
"""
from __future__ import annotations

import re

from resource_explorer.surveyors.database.quality.catalogue import POSTGRES_DIR, sha256_text

GENERAL_TYPE_SQL = POSTGRES_DIR / "general_type.sql"
GENERAL_TYPE_CASE_SHA256 = "b059e1e13714472f197b29f3795436317bfd72ed990dfdab904fdb6716c91476"

#: Postgres's internal type names (`pg_type.typname`, which rows materialised from an Egeria survey can carry) ->
#: the `information_schema` names the CASE is written against.
_INTERNAL_NAMES = {
    "int2": "smallint", "int4": "integer", "int8": "bigint", "float4": "real", "float8": "double precision",
    "bool": "boolean", "varchar": "character varying", "bpchar": "character", "char": "character",
    "timestamptz": "timestamp with time zone", "timestamp": "timestamp without time zone",
    "timetz": "time with time zone", "time": "time without time zone", "decimal": "numeric",
}

_MODIFIER = re.compile(r"\(\s*(\d+)(?:\s*,\s*\d+)?\s*\)")


def vendored_case_text() -> str:
    """The CASE statement in the vendored file: every line from `CASE` to `END AS general_type,`."""
    lines = GENERAL_TYPE_SQL.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "CASE")
    end = next(i for i, line in enumerate(lines) if line.strip().startswith("END AS general_type"))
    return "\n".join(lines[start:end + 1]) + "\n"


def _information_schema_type(catalog_type: str) -> tuple[str, int | None]:
    """RE stores either `information_schema.columns.data_type` ("character varying") or, for a table only the
    catalog shows, `format_type()` ("character varying(20)", "timestamp(3) without time zone", "bit(1)").
    Reduce the second to the first, keeping the length modifier as `character_maximum_length`."""
    text = (catalog_type or "").strip()
    if text.endswith("[]"):
        return "ARRAY", None  # information_schema's word for every array type
    match = _MODIFIER.search(text)
    length = int(match.group(1)) if match else None
    if text.startswith("_"):
        return "ARRAY", None  # pg_type names an array type with a leading underscore (_int4)
    name = re.sub(r"\s+", " ", _MODIFIER.sub("", text)).strip()
    return _INTERNAL_NAMES.get(name.lower(), name), length


def general_type(catalog_type: str | None, character_maximum_length: int | None = None) -> str:
    """The CASE, term for term:

        WHEN c.data_type ILIKE '%char%' or c.data_type = 'text'                                  THEN 'A'
        WHEN c.data_type ILIKE 'boolean' OR (c.data_type = 'bit' AND c.character_maximum_length = 1) THEN 'B'
        WHEN c.data_type ILIKE 'date' OR c.data_type ILIKE 'timestamp%'                          THEN 'D'
        WHEN c.data_type ILIKE 'time with%'                                                       THEN 'T'
        WHEN LOWER(c.data_type) IN ('bigint', 'integer', 'smallint', 'double precision', 'real',
                                    'numeric', 'money')                                          THEN 'N'
        ELSE 'X'

    (`time with%` also matches "time without time zone": that is upstream's behaviour, kept.)"""
    data_type, length = _information_schema_type(catalog_type or "")
    if character_maximum_length is None:
        character_maximum_length = length
    lowered = data_type.lower()
    if "char" in lowered or data_type == "text":
        return "A"
    if lowered == "boolean" or (data_type == "bit" and character_maximum_length == 1):
        return "B"
    if lowered == "date" or lowered.startswith("timestamp"):
        return "D"
    if lowered.startswith("time with"):
        return "T"
    if lowered in ("bigint", "integer", "smallint", "double precision", "real", "numeric", "money"):
        return "N"
    return "X"


def case_matches_pin() -> bool:
    return sha256_text(vendored_case_text()) == GENERAL_TYPE_CASE_SHA256


# ── profile 1b: which numeric measures a column takes (upstream's is_decimal and logical_type) ────────────────────

NUMERIC_CHARS_SQL = POSTGRES_DIR / "numeric_chars.sql"
#: SHA-256 of the two vendored CASE texts, in file order: logical_type, then is_decimal.
NUMERIC_CHARS_SHA256 = ("55c1c8dec4374daf963b6953f68d23c44865fe23de0e842934f74f4f844cc511",
                        "b4a11c5e6316ed99daa71f25c0ee5b193d17633a5dce789de063668951885255")


def vendored_numeric_cases() -> list[str]:
    """The CASE texts of `numeric_chars.sql`, each the text after its `-- upstream lines` header."""
    blocks = NUMERIC_CHARS_SQL.read_text(encoding="utf-8").split("-- upstream lines ")[1:]
    return [block.split("\n", 1)[1].rstrip("\n") + "\n" for block in blocks]


def numeric_cases_match_pins() -> bool:
    return tuple(sha256_text(text) for text in vendored_numeric_cases()) == NUMERIC_CHARS_SHA256


#: The catalog read for the two CASEs: `information_schema.columns` as upstream's get_schema_ddf.sql reads it, for the
#: types general_type calls N. One statement for the whole database, beside the table facts.
NUMERIC_CHARS_QUERY = (
    "SELECT table_schema AS schema_name, table_name, column_name, data_type, numeric_scale "
    "FROM information_schema.columns "
    "WHERE LOWER(data_type) IN ('bigint', 'integer', 'smallint', 'double precision', 'real', 'numeric', 'money')"
)


def is_decimal(data_type: str | None, numeric_scale: int | None) -> bool | None:
    """The `is_decimal` CASE, term for term (NULL stays None):

        WHEN c.data_type = 'numeric' THEN COALESCE(numeric_scale, 1) > 0
        WHEN c.data_type = 'money' THEN TRUE
        ELSE numeric_scale > 0"""
    if data_type == "numeric":
        return (numeric_scale if numeric_scale is not None else 1) > 0
    if data_type == "money":
        return True
    return None if numeric_scale is None else numeric_scale > 0


def logical_type_is_integer(data_type: str | None, numeric_scale: int | None) -> bool:
    """The `logical_type` CASE's first arm, the only one the profile reads:

        WHEN LOWER(c.data_type) IN ('bigint', 'integer', 'smallint')
            OR (c.data_type = 'numeric' AND c.numeric_scale = 0) THEN 'integer'"""
    return (data_type or "").lower() in ("bigint", "integer", "smallint") or (
        data_type == "numeric" and numeric_scale == 0)


def exact_sum(general_type_: str, data_type: str | None, numeric_scale: int | None) -> bool:
    """Upstream's `is_N_exact_sum` (ProfilingSQL.run_column_profiling): a number whose logical type is integer or that
    is decimal, and whose type the flavour can cast to DECIMAL (Postgres excludes none). A double or real column is
    neither, so its sum is not taken exactly (Match_Sum then baselines on avg_value * value_ct, as upstream)."""
    return general_type_ == "N" and (logical_type_is_integer(data_type, numeric_scale)
                                     or bool(is_decimal(data_type, numeric_scale)))


def is_n_decimal(general_type_: str, data_type: str | None, numeric_scale: int | None) -> bool:
    """Upstream's `is_N_decimal`: `general_type == "N" and column_chars.is_decimal` (a NULL is_decimal is false)."""
    return general_type_ == "N" and bool(is_decimal(data_type, numeric_scale))
