"""The words a declaration says, derived in one place (brief 2026-10-30, Parts A and B).

Every reader of a declared result (the headline, a step's drill-down row, the publish table, the Chat compile) takes
its sentence from here, so no reader writes an "of", an "at least" or a "lower bound" of its own:

- **basis** (`MeasureDeclaration.basis`): "456 of 1,236 columns" is the measure's value, the declared denominator and the
  denominator's declared words. The sentence changes when the denominator does; nothing in a reader says "of".
- **capped** (`MeasureDeclaration.capped_field`): a value that is a lower bound reads "at least N <unit>", and every figure
  a reader would derive from it (a share, a difference) is withheld with `LOWER_BOUND_WORDS`, never computed from the cut
  count.
- **categories** (`Declaration.categories`): categories that overlap are never summed, so a headline never says "of N" for
  them and a stated total is read from its field, not added up.
- **the non-numeric kinds** (`Declaration.finding / proposal / listing / table`): the headline of a finding, a proposal, a
  list or a table, from the declared block and the rows the result holds. A kind whose key field is not in the result at
  all says nothing (""), because "0 findings" would be a measurement nobody made.
"""
from __future__ import annotations

import re

from trellis_analytics.declaration import LOWER_BOUND_WORDS

from resource_explorer.publish_table import _MISSING, filled_unit, resolve

#: The words a categories declaration that overlaps says wherever its categories would be summed or stacked.
OVERLAP_WORDS = "overlapping categories (not summable)"
#: The chart legend note for the same (a renderer's own words, so the screen and the chat say it alike).
OVERLAP_NOTE = "categories overlap"
#: Prefix for a lower bound.
AT_LEAST = "at least"


#: Words stored before the US-spelling pass (a step run on 2026-10-29 or earlier wrote "never analysed" into its summary).
#: Person-facing text is US spelling everywhere, so stored prose is read through this map; a new run writes US already.
_UK = re.compile(r"\b([Aa])nalys(ed|ing|e)\b")


def us_spelling(text):
    """`text` with the known British spellings of stored step prose in US spelling (whole words only: the plural noun
    "analyses" is already US); anything else unchanged."""
    if not isinstance(text, str) or "nalys" not in text:
        return text
    return _UK.sub(lambda m: f"{m.group(1)}nalyz{m.group(2)}", text)


def truthy(v) -> bool:
    """Is a result field's value 'true'? A library step's properties are strings ("true"), RE's own results booleans."""
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return bool(v) and v is not _MISSING


def number(v) -> int | float | None:
    """A value as a number, or None where it is not one. Numbers written as text count; an integral float is an int."""
    if isinstance(v, bool) or v is _MISSING or v is None:
        return None
    if isinstance(v, (int, float)):
        n = v
    else:
        try:
            n = float(str(v).replace(",", "").strip())
        except ValueError:
            return None
    return int(n) if float(n).is_integer() else round(n, 4)


def count_words(n: int | float) -> str:
    return f"{n:,}"


def plural_by(n, words: str) -> str:
    """`words` (a plural noun phrase, as declared) in the number's form: "1 column", "2 columns". Only the head word
    changes, taken as the first word ending in a plain plural 's'; a phrase that does not start that way is left."""
    if n != 1:
        return words
    parts = words.split(" ", 1)
    head = parts[0]
    if head.endswith("ies") and len(head) > 4:
        head = head[:-3] + "y"
    elif head.endswith("s") and not head.endswith(("ss", "us", "is")) and len(head) > 2:
        head = head[:-1]
    else:
        return words
    return " ".join([head, *parts[1:]])


def measure_words(m, source: dict, run: dict | None = None, *, allow_of: bool = True) -> dict | None:
    """One declared measure's words from its result, or None when the result holds no value for it.

    {"text": "at least 456 of 1,236 columns", "value": 456, "denominator": 1236 | None, "capped": bool,
     "share": "37%" | None, "derived_note": "" | LOWER_BOUND_WORDS}.
    `allow_of` False (overlapping categories) drops the "of N". A declared denominator the result does not hold leaves
    the plain number: the whole is not invented."""
    run = run or {}
    v = resolve(source, m.value_field, run)
    if v is _MISSING:
        return None
    unit = filled_unit(m.unit, source)
    n = number(v)
    capped = bool(getattr(m, "capped_field", "")) and truthy(resolve(source, m.capped_field, run))
    if n is None:
        text = f"{v} {unit}".strip()
        return {"text": f"{AT_LEAST} {text}" if capped else text, "value": v, "denominator": None, "capped": capped,
                "share": None, "derived_note": LOWER_BOUND_WORDS if capped else ""}
    basis = getattr(m, "basis", None)
    denominator = None
    if basis is not None and allow_of:
        d = number(resolve(source, basis.denominator_field, run))
        denominator = d if d is not None and d >= n else None
    # A numerator that counts a part of the whole in its own words (statistics_freshness: "tables never analyzed or
    # stale" of "tables") says what it counts: "215 of 266 tables" read as 215 tables measured (W57). A numerator in the
    # whole's own words ("rows" of "rows in the table") keeps the short form.
    subset = denominator is not None and bool(unit) and not basis.denominator_label.startswith(unit)
    if denominator is not None:
        whole = f"{count_words(denominator)} {plural_by(denominator, basis.denominator_label)}"
        text = f"{count_words(n)} {plural_by(n, unit)} of {whole}" if subset else f"{count_words(n)} of {whole}"
    else:
        text = f"{count_words(n)} {plural_by(n, unit)}"
    share = None
    if denominator and not capped:
        share = f"{round(100 * n / denominator):d}%" + (f" of the {basis.denominator_label}" if subset else "")
    return {"text": f"{AT_LEAST} {text}" if capped else text, "value": n, "denominator": denominator,
            "capped": capped, "share": share, "derived_note": LOWER_BOUND_WORDS if capped else ""}


def measure_sentence(m, source: dict, run: dict | None = None, *, allow_of: bool = True) -> tuple[str, bool]:
    """(the sentence a headline or a drill-down row says for this measure, whether it is a lower bound), or ("", False)
    where the result holds no value. A share is added only to an uncapped count with a basis; a capped one says the
    figure is not derived instead."""
    w = measure_words(m, source, run, allow_of=allow_of)
    if w is None:
        return "", False
    out = w["text"]
    if w["share"]:
        out += f" ({w['share']})"
    if w["capped"]:
        out += f" · {w['derived_note']}"
    return out, w["capped"]


def allows_of(dec) -> bool:
    """May a headline say "of N" for this declaration? Not when its categories overlap."""
    cats = getattr(dec, "categories", None)
    return cats is None or cats.exclusive


def stated_total(dec, value: dict, run: dict | None = None):
    """The total the result states for its categories (`sums_to`), read from that field and never summed; None where
    none is declared or the result does not hold it."""
    cats = getattr(dec, "categories", None)
    if cats is None or not cats.sums_to:
        return None
    got = resolve(value, cats.sums_to, run or {})
    return None if got is _MISSING else got


def categories_legend(dec, value: dict, run: dict | None = None) -> str:
    """The legend a step line shows for a declaration's categories, in the words a person reads, or "" where it declares
    none. The declaration names no field per category, so the legend says how the categories relate (and the stated total, if
    one is declared and the result holds it); it draws no bar."""
    cats = getattr(dec, "categories", None)
    if cats is None:
        return ""
    if not cats.exclusive:
        return f"{OVERLAP_NOTE} · not stacked or added up"
    total = stated_total(dec, value, run)
    n = number(total) if total is not None else None
    if n is not None:
        return f"each item is in one category · stated total {count_words(n)}"
    return "each item is in one category"


def categories_line(dec, value: dict, run: dict | None = None) -> str:
    """The line the Chat compile says about a declaration's categories, or "" where it declares none."""
    cats = getattr(dec, "categories", None)
    if cats is None:
        return ""
    if not cats.exclusive:
        return f"categories: {OVERLAP_WORDS}"
    total = stated_total(dec, value, run)
    if total is not None:
        n = number(total)
        return f"categories: exclusive; stated total {count_words(n) if n is not None else total} (read from the result, not summed)"
    return "categories: exclusive" + ("" if not cats.sums_to else "; the result states no total")


# ── the non-numeric kinds ───────────────────────────────────────────────────────────────────────────────────────

def _humanize(field: str) -> str:
    return " ".join(str(field).replace("_", " ").replace(".", " ").split())


def kind_rows(dec, value: dict, field: str) -> list | None:
    """The rows a kind's headline counts: the first extent that carries `field` (else the first extent), in the
    declared order. None where the result holds no rows for it, or where no row carries the field at all (the kind
    cannot be told from this result)."""
    from resource_explorer.declared_context import extent_rows, sorted_rows

    extents = sorted(dec.extents, key=lambda e: field not in e.fields)
    for extent in extents:
        rows = extent_rows(value, extent)
        if rows is None:
            continue
        rows = sorted_rows(rows, extent)
        if rows and not any(isinstance(r, dict) and field in r for r in rows):
            continue
        return rows
    return None


def _plural_noun(n: int, singular: str, plural: str) -> str:
    return f"{count_words(n)} {singular if n == 1 else plural}"


def kind_headline(dec, value: dict, run: dict | None = None) -> str:
    """The headline of a finding, proposal, list or table from its declared block; "" for any other kind or where the
    result cannot be read for it."""
    kind = dec.result_kind
    if kind == "finding" and dec.finding:
        f = dec.finding
        rows = kind_rows(dec, value, f.what_field)
        if rows is None:
            return ""
        if not rows:
            return dec.absence.sentence("measured_nothing")
        lead = _plural_noun(len(rows), "finding", "findings")
        first = rows[0]
        if f.severity_field and f.severities:
            order = {s: i for i, s in enumerate(f.severities)}
            ranked = sorted((r for r in rows if isinstance(r, dict) and r.get(f.severity_field) in order),
                            key=lambda r: -order[r[f.severity_field]])
            if ranked:
                first = ranked[0]
                lead += f" · {first[f.severity_field]}"
        what = first.get(f.what_field) if isinstance(first, dict) else None
        return f"{lead}: {' '.join(str(what).split())}" if what not in (None, "") else lead
    if kind == "proposal" and dec.proposal:
        p = dec.proposal
        rows = kind_rows(dec, value, p.candidate_field)
        if rows is None:
            return ""
        if not rows:
            return dec.absence.sentence("measured_nothing")
        if not p.state_field:
            return f"{count_words(len(rows))} proposed"
        counts: dict[str, int] = {}
        for r in rows:
            s = " ".join(str(r.get(p.state_field) or "state not recorded").split()) if isinstance(r, dict) \
                else "state not recorded"
            counts[s] = counts.get(s, 0) + 1
        known = [f"{count_words(counts.pop(s))} {s}" for s in p.decision_states if s in counts]
        known += [f"{count_words(n)} {s}" for s, n in counts.items()]
        return " · ".join(known)
    if kind == "list" and dec.listing:
        lst = dec.listing
        rows = kind_rows(dec, value, lst.key_field)
        if rows is None:
            return ""
        n = len(rows)
        if not rows:
            return dec.absence.sentence("measured_nothing")
        if lst.item_label:
            label = plural_by(n, " ".join(lst.item_label.split()))
        else:
            label = _humanize(lst.key_field) + ("" if n == 1 else "s")
        return f"{count_words(n)} {label}, {lst.order}"
    if kind == "table" and dec.table:
        t = dec.table
        rows = kind_rows(dec, value, t.column_names()[0])
        if rows is None:
            return ""
        return f"{_plural_noun(len(rows), 'row', 'rows')} × {_plural_noun(len(t.columns), 'column', 'columns')}"
    return ""
