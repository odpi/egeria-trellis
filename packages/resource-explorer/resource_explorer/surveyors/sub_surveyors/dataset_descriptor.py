"""Sub-surveyor: does this repository describe itself as a dataset, and what does the description say?

Discovery-tier, like `repo_classification`, and as cheap: it reads the files already on disk (the ingestion pipeline
calls it while the checkout exists) and makes no network call. It reads, in this order, and keeps every one found:

    datapackage.json     Frictionless Data Package: name, title, licenses[], resources[] (path, format, bytes)
    croissant.json /     MLCommons Croissant / schema.org: name, license, distribution[], citeAs
      metadata.json      (metadata.json counts only when its `@type` is Dataset: the name is used by other tools)
    CITATION.cff         title, doi, license, authors, type
    README.md            a DOI badge or link (`doi.org/10.`), and a Hugging Face dataset card (front matter with
                         `license:` and `dataset_info:`)

and returns `dataset_descriptor` rows: kind, path, identifier, identifier_kind (doi | datapackage_name |
croissant_name | hf_repo | none), publisher, licence_id and its three flags (`licences.py`), and the declared data
files. Every field the descriptor does not state is `NOT_REPORTED`, never an empty string.

What a descriptor row does NOT do: it does not decide the repository's role. A README DOI badge is on a great many
software repositories (Zenodo archives releases), so it is kept as a row and carries the identity, but it does not
make a repository "a dataset" on its own; neither does a CITATION.cff unless it says `type: dataset`
(`NAMES_A_DATASET`). No score is emitted, deliberately (the rule of `repo_classification`).

The DOI is NOT resolved here: ingestion must not spend a network call per repository. The import
(`open_data/service.py::import_from_repository`) resolves it (one DataCite call, priced before the press) and keeps
the record beside the descriptor.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from resource_explorer.open_data.absence import NOT_REPORTED, reported_or_absent
from resource_explorer.open_data.ckan import normalise_doi
from resource_explorer.open_data.licences import licence_flags, licence_id_from_iri
from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.base_surveyor import BaseSurveyor
from resource_explorer.surveyors.survey_report import Annotation

log = logging.getLogger(__name__)

STEP = "DatasetDescriptor"
FINDINGS_KIND = "dataset_descriptor"

KIND_DATAPACKAGE, KIND_CROISSANT, KIND_CFF, KIND_README_DOI, KIND_HF_CARD = (
    "datapackage", "croissant", "citation_cff", "readme_doi", "hf_card")
#: The kinds, in the order they are read and listed.
KINDS = (KIND_DATAPACKAGE, KIND_CROISSANT, KIND_CFF, KIND_README_DOI, KIND_HF_CARD)
#: The descriptors that say, in their own words, that this is a dataset. A README DOI does not (see the module doc).
NAMES_A_DATASET = (KIND_DATAPACKAGE, KIND_CROISSANT, KIND_CFF, KIND_HF_CARD)

ID_DOI, ID_DATAPACKAGE, ID_CROISSANT, ID_HF, ID_NONE = "doi", "datapackage_name", "croissant_name", "hf_repo", "none"

#: Never look inside these (somebody else's checked-in code, not this repository's description).
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "site-packages", "vendor", "__pycache__"}
_MAX_DEPTH = 3
_MAX_BYTES = 2 * 1024 * 1024                    # a descriptor larger than this is not read; said, not guessed
_README_NAMES = ("README.md", "readme.md", "README.MD", "Readme.md")

_DOI_RE = re.compile(r"(?:doi\.org/|zenodo\.org/badge/DOI/)(10\.\d{4,9}/[^\s)\]\">'<]+?)(?:\.svg)?(?=[\s)\]\">'<,;]|$)",
                     re.IGNORECASE)
_HF_RE = re.compile(r"huggingface\.co/datasets/([\w.-]+/[\w.-]+)", re.IGNORECASE)
_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*$", re.IGNORECASE)
_UNITS = {"b": 1, "kb": 10 ** 3, "mb": 10 ** 6, "gb": 10 ** 9, "tb": 10 ** 12,
          "kib": 2 ** 10, "mib": 2 ** 20, "gib": 2 ** 30, "tib": 2 ** 40}


def _row(kind: str, path: str, **fields) -> dict:
    licence_id = fields.pop("licence_id", NOT_REPORTED)
    licence_id = reported_or_absent(licence_id)
    return {
        "kind": kind, "path": path,
        "title": reported_or_absent(fields.pop("title", None)),
        "identifier": fields.pop("identifier", NOT_REPORTED),
        "identifier_kind": fields.pop("identifier_kind", ID_NONE),
        "doi": fields.pop("doi", NOT_REPORTED),
        "publisher": reported_or_absent(fields.pop("publisher", None)),
        "licence_id": licence_id,
        "licence": licence_flags(None if licence_id == NOT_REPORTED else licence_id),
        "data_files": fields.pop("data_files", []),
        "names_a_dataset": kind in NAMES_A_DATASET and fields.pop("names_a_dataset", True),
        **fields,
    }


def _licence(raw):
    """The licence as the descriptor spells it ("CC-BY-4.0"); a licence IRI is mapped to the id RE knows when it can."""
    if isinstance(raw, str) and raw.strip().lower().startswith(("http://", "https://")):
        return licence_id_from_iri(raw)
    return raw


def _doi_in(*values) -> str:
    """The first bare DOI found in any of the values (a string, or a list/dict of them), else NOT_REPORTED."""
    for v in values:
        if isinstance(v, (list, tuple)):
            found = _doi_in(*v)
        elif isinstance(v, dict):
            found = _doi_in(*v.values())
        elif isinstance(v, str):
            found = normalise_doi(v)
            if found == NOT_REPORTED:
                m = _DOI_RE.search(v)
                found = m.group(1).rstrip(".,;").lower() if m else NOT_REPORTED
        else:
            continue
        if found != NOT_REPORTED:
            return found
    return NOT_REPORTED


def _identity(doi: str, name, name_kind: str, hf: str | None = None) -> tuple[str, str]:
    if doi != NOT_REPORTED:
        return doi, ID_DOI
    if isinstance(name, str) and name.strip():
        return name.strip(), name_kind
    if hf:
        return hf, ID_HF
    return NOT_REPORTED, ID_NONE


def _bytes(value) -> object:
    if isinstance(value, bool):
        return NOT_REPORTED
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    if isinstance(value, str):
        if value.strip().isdigit():
            return int(value.strip())
        m = _SIZE_RE.match(value)
        if m:
            return int(float(m.group(1)) * _UNITS[m.group(2).lower()])
    return NOT_REPORTED


def _format_of(path: str, stated=None) -> object:
    if isinstance(stated, str) and stated.strip():
        text = stated.strip()
        return text.rsplit("/", 1)[-1].upper() if "/" in text else text.upper()      # "text/csv" -> CSV
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", path or "")
    return m.group(1).upper() if m else NOT_REPORTED


# ── the parsers: text in, a row (or None) out ───────────────────────────────────────

def parse_datapackage(text: str, path: str = "datapackage.json") -> dict | None:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    files = []
    for r in doc.get("resources") or []:
        if not isinstance(r, dict):
            continue
        paths = r.get("path")
        for p in (paths if isinstance(paths, list) else [paths]):
            if isinstance(p, str) and p.strip():
                files.append({"path": p.strip(), "format": _format_of(p, r.get("format")),
                              "bytes": _bytes(r.get("bytes")), "name": reported_or_absent(r.get("name"))})
    lic = doc.get("licenses") or ([doc["license"]] if doc.get("license") else [])
    first = next((l for l in lic if isinstance(l, dict) or isinstance(l, str)), None)
    licence = (first.get("name") or first.get("path")) if isinstance(first, dict) else first
    doi = _doi_in(doc.get("id"), doc.get("doi"), doc.get("citation"), doc.get("homepage"))
    identifier, kind = _identity(doi, doc.get("name"), ID_DATAPACKAGE)
    return _row(KIND_DATAPACKAGE, path, title=doc.get("title") or doc.get("name"), identifier=identifier,
                identifier_kind=kind, doi=doi, publisher=_first_text(doc.get("publisher"), doc.get("contributors")),
                licence_id=_licence(licence), data_files=files, name=reported_or_absent(doc.get("name")))


def _first_text(*values) -> object:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict) and str(v.get("name") or v.get("title") or "").strip():
            return str(v.get("name") or v.get("title")).strip()
        if isinstance(v, list):
            got = _first_text(*v)
            if got != NOT_REPORTED:
                return got
    return NOT_REPORTED


def _is_dataset_type(value) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(isinstance(v, str) and v.rsplit(":", 1)[-1].rsplit("/", 1)[-1].lower() == "dataset" for v in values)


def parse_croissant(text: str, path: str = "croissant.json") -> dict | None:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict) or not _is_dataset_type(doc.get("@type")):
        return None
    files = []
    for d in doc.get("distribution") or []:
        if not isinstance(d, dict):
            continue
        where = d.get("contentUrl") or d.get("name") or d.get("@id")
        if isinstance(where, str) and where.strip():
            files.append({"path": where.strip(), "format": _format_of(where, d.get("encodingFormat")),
                          "bytes": _bytes(d.get("contentSize")), "name": reported_or_absent(d.get("name"))})
    lic = doc.get("license")
    lic = lic[0] if isinstance(lic, list) and lic else lic
    lic = (lic.get("name") or lic.get("@id") or lic.get("url")) if isinstance(lic, dict) else lic
    doi = _doi_in(doc.get("identifier"), doc.get("citeAs"), doc.get("sameAs"), doc.get("url"))
    identifier, kind = _identity(doi, doc.get("name"), ID_CROISSANT)
    return _row(KIND_CROISSANT, path, title=doc.get("name"), identifier=identifier, identifier_kind=kind, doi=doi,
                publisher=_first_text(doc.get("publisher"), doc.get("creator")), licence_id=_licence(lic),
                data_files=files, name=reported_or_absent(doc.get("name")),
                cite_as=reported_or_absent(doc.get("citeAs")))


def parse_citation_cff(text: str, path: str = "CITATION.cff") -> dict | None:
    import yaml

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    ids = [i.get("value") for i in doc.get("identifiers") or [] if isinstance(i, dict) and i.get("type") == "doi"]
    doi = _doi_in(doc.get("doi"), ids)
    authors = [
        " ".join(str(a.get(k)) for k in ("given-names", "family-names") if a.get(k)) or str(a.get("name") or "")
        for a in doc.get("authors") or [] if isinstance(a, dict)]
    lic = doc.get("license")
    lic = lic[0] if isinstance(lic, list) and lic else lic
    identifier, kind = _identity(doi, None, ID_NONE)
    return _row(KIND_CFF, path, title=doc.get("title"), identifier=identifier, identifier_kind=kind, doi=doi,
                licence_id=lic if isinstance(lic, str) else NOT_REPORTED,
                authors=[a for a in authors if a] or NOT_REPORTED,
                names_a_dataset=str(doc.get("type") or "software").lower() == "dataset",
                cff_type=reported_or_absent(doc.get("type")))


def _front_matter(text: str) -> dict | None:
    import yaml

    m = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def parse_hf_card(text: str, path: str = "README.md") -> dict | None:
    """A Hugging Face dataset card: README front matter with BOTH `license:` and `dataset_info:`."""
    meta = _front_matter(text)
    if not meta or not meta.get("license") or "dataset_info" not in meta:
        return None
    info = meta.get("dataset_info")
    info = info[0] if isinstance(info, list) and info else info
    lic = meta.get("license")
    lic = lic[0] if isinstance(lic, list) and lic else lic
    m = _HF_RE.search(text)
    hf = m.group(1) if m else None
    identifier, kind = _identity(NOT_REPORTED, None, ID_NONE, hf)
    files = []
    for cfg in meta.get("configs") or []:
        if not isinstance(cfg, dict):
            continue
        declared = cfg.get("data_files")
        for item in declared if isinstance(declared, list) else [declared]:
            where = item.get("path") if isinstance(item, dict) else item
            if isinstance(where, str) and where.strip():
                files.append({"path": where.strip(), "format": _format_of(where, None), "bytes": NOT_REPORTED,
                              "name": reported_or_absent(cfg.get("config_name"))})
    return _row(KIND_HF_CARD, path, title=meta.get("pretty_name") or (info.get("config_name") if isinstance(info, dict) else None),
                identifier=identifier, identifier_kind=kind, licence_id=str(lic), data_files=files)


def parse_readme_doi(text: str, path: str = "README.md") -> dict | None:
    m = _DOI_RE.search(text or "")
    if not m:
        return None
    doi = normalise_doi(m.group(1))
    if doi == NOT_REPORTED:
        return None
    return _row(KIND_README_DOI, path, identifier=doi, identifier_kind=ID_DOI, doi=doi)


# ── reading the tree on disk ───────────────────────────────────────────────────────

def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _walk(root: Path):
    """(relative posix path, absolute Path) of files up to `_MAX_DEPTH`, skipping vendored and VCS directories."""
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if len(rel.parts) > _MAX_DEPTH or any(part in _SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if p.is_file():
            yield rel.as_posix(), p


def read_descriptors(root: str | Path) -> list[dict]:
    """Every dataset descriptor found under `root`, in `KINDS` order (and path order within a kind). [] = none found;
    that is a measured zero when `root` is the repository's tree."""
    root = Path(root)
    found: dict[str, list[dict]] = {k: [] for k in KINDS}
    for rel, p in _walk(root):
        name = rel.rsplit("/", 1)[-1]
        row = None
        if name == "datapackage.json":
            row = parse_datapackage(_read(p) or "", rel)
        elif name in ("croissant.json", "metadata.json"):
            row = parse_croissant(_read(p) or "", rel)
        elif name == "CITATION.cff":
            row = parse_citation_cff(_read(p) or "", rel)
        if row:
            found[row["kind"]].append(row)
    for readme in _README_NAMES:
        text = _read(root / readme) if (root / readme).is_file() else None
        if text:
            for parser in (parse_readme_doi, parse_hf_card):
                row = parser(text, readme)
                if row:
                    found[row["kind"]].append(row)
            break
    return [r for k in KINDS for r in found[k]]


# ── what the declared files say against the tree ───────────────────────────────────────

def is_remote(path: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", str(path or ""), re.IGNORECASE))


def cross_check(descriptor: dict, tree_paths: list[str]) -> dict:
    """The declared data files against the tier-1 inventory. Declared paths are relative to the descriptor's own
    directory. Remote (URL) declarations cannot be in the tree: counted apart, never as missing."""
    base = descriptor["path"].rsplit("/", 1)[0] + "/" if "/" in descriptor["path"] else ""
    held = set(tree_paths)
    local = [f for f in descriptor.get("data_files") or [] if not is_remote(f["path"])]
    remote = [f for f in descriptor.get("data_files") or [] if is_remote(f["path"])]
    present = [f for f in local if (base + f["path"].lstrip("./")) in held or f["path"] in held]
    declared = len(local)
    if not descriptor.get("data_files"):
        words = NOT_REPORTED
    else:
        words = f"{len(present)} of {declared} declared files present in the tree" if declared else \
            "every declared file is remote; none can be checked against the tree"
        if remote and declared:
            words += f" (+{len(remote)} remote)"
    return {"declared": declared, "present": len(present), "remote": len(remote), "words": words,
            "missing": [f["path"] for f in local if f not in present]}


def chosen_identity(descriptors: list[dict], repo_owner_repo: str = "") -> dict | None:
    """The identity an import would use: the first DOI any descriptor holds, else the first datapackage/croissant
    name qualified by the repository ("<owner>/<repo>:<name>"), else a Hugging Face repo. None = nothing names one."""
    for d in descriptors:
        if d["doi"] != NOT_REPORTED:
            return {"identifier": d["doi"], "identifier_kind": ID_DOI, "from": d["path"], "descriptor": d}
    for d in descriptors:
        if d["identifier_kind"] in (ID_DATAPACKAGE, ID_CROISSANT):
            qualified = f"{repo_owner_repo}:{d['identifier']}" if repo_owner_repo else d["identifier"]
            return {"identifier": qualified, "identifier_kind": d["identifier_kind"], "from": d["path"], "descriptor": d}
    for d in descriptors:
        if d["identifier_kind"] == ID_HF:
            return {"identifier": d["identifier"], "identifier_kind": ID_HF, "from": d["path"], "descriptor": d}
    return None


# ── the words on the repository page ──────────────────────────────────────────────────────

NO_DESCRIPTOR_SENTENCE = ("a repository with data files, not an open dataset by Resource Explorer's rule "
                          "(no descriptor names it)")
IMPORT_PRICE_WORDS = "no network · reads the descriptor already on disk (+1 DataCite call if a DOI)"
IMPORT_HOVER_HELP = "Writes one open-data row to this app's registry and nothing to Egeria."
NO_IDENTIFIER_SENTENCE = ("No descriptor in this repository carries an identifier (a DOI, or a datapackage or "
                          "Croissant name), so there is nothing to register it under.")


def dataset_sentence(descriptors: list[dict]) -> str:
    """"a dataset repository · identity DOI 10.…/… · licence CC-BY-4.0 (from datapackage.json)". Built from the
    descriptors that name a dataset; the first of each fact wins, and each says where it came from."""
    naming = [d for d in descriptors if d["names_a_dataset"]]
    ident = chosen_identity(descriptors)
    if ident is None:
        identity = "no identity stated"
    elif ident["identifier_kind"] == ID_DOI:
        identity = f"identity DOI {ident['identifier']}"
    else:
        identity = f"identity {ident['identifier_kind'].replace('_', ' ')} {ident['identifier']} (from {ident['from']})"
    lic = next((d for d in naming if d["licence_id"] != NOT_REPORTED), None)
    licence = f"licence {lic['licence_id']} (from {lic['path']})" if lic else "licence not reported"
    return f"a dataset repository · {identity} · {licence}"


STATE_DATASET, STATE_DATA_FILES = "dataset", "data_files_no_descriptor"


def kind_finding(registry: ProjectRegistry, slug: str, role_evidence: list[str] | None = None) -> dict | None:
    """The finding behind the repository page's "what kind of thing" row, from what is stored: the descriptors of the
    last ingestion and the tier-1 file inventory. None = nothing to say (no descriptor and no data files).

      descriptor that names a dataset -> "a dataset repository · identity DOI 10.…/… · licence CC-BY-4.0 (from …)"
      data files, no such descriptor  -> "a repository with data files, not an open dataset by Resource Explorer's rule
                                          (no descriptor names it)"
    A descriptor stored before this was read (ingested earlier) is not "no descriptor": the sentence then says so."""
    from resource_explorer.surveyors.sub_surveyors.data_profiler import _DATA_EXTENSIONS

    stored = registry.query_findings(slug, FINDINGS_KIND)
    descriptors = descriptors_from(stored)
    naming = [d for d in descriptors if d["names_a_dataset"]]
    inventory = registry.get_file_inventory_with_sizes(slug)
    data_files = [r for r in inventory if "." in r["file_path"].rsplit("/", 1)[-1]
                  and r["file_path"].rsplit(".", 1)[-1].lower() in _DATA_EXTENSIONS]
    if naming:
        state, sentence = STATE_DATASET, dataset_sentence(descriptors)
    elif role_evidence and any(e.startswith(("dataset-descriptor", "huggingface-dataset-card")) for e in role_evidence) \
            and not stored:
        state = STATE_DATASET
        sentence = ("a dataset repository · its descriptor was seen on GitHub but its files have not been read here yet "
                    "(a refresh reads them)")
    elif data_files:
        state, sentence = STATE_DATA_FILES, NO_DESCRIPTOR_SENTENCE
    else:
        return None
    return {
        "check_name": "dataset_kind", "label": "dataset repository" if state == STATE_DATASET else "data files only",
        "summary": sentence, "confidence": 60,
        "detail": {"state": state, "descriptors": [{k: d[k] for k in ("kind", "path", "identifier", "identifier_kind", "doi")}
                                                   for d in descriptors],
                   "data_files": len(data_files), "price": IMPORT_PRICE_WORDS, "help": IMPORT_HOVER_HELP,
                   "can_import": state == STATE_DATASET and chosen_identity(descriptors) is not None,
                   "no_identifier": NO_IDENTIFIER_SENTENCE},
    }


# ── the sub-surveyor ───────────────────────────────────────────────────────────────────────

def findings_for(descriptors: list[dict]) -> list[dict]:
    """The rows as findings (kind `dataset_descriptor`): one per descriptor, or the one finding that says none was
    found in a tree that was read."""
    if not descriptors:
        return [{"check_name": "no_descriptor", "label": "none",
                 "summary": "no dataset descriptor in the tree (datapackage.json, croissant.json, CITATION.cff, "
                            "a README DOI or a Hugging Face card)",
                 "confidence": 100, "detail": {"read_tree": True}}]
    return [{"check_name": f"descriptor_{d['kind']}", "label": d["kind"],
             "summary": f"{d['path']}: " + (f"{d['identifier_kind'].replace('_', ' ')} {d['identifier']}"
                                            if d["identifier"] != NOT_REPORTED else "no identifier stated"),
             "confidence": 100, "detail": d} for d in descriptors]


def descriptors_from(findings: list[dict]) -> list[dict]:
    """Back from stored findings (`registry.query_findings`) to descriptor rows."""
    out = []
    for f in findings:
        detail = f.get("detail_json") if f.get("detail_json") is not None else f.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                continue
        if isinstance(detail, dict) and detail.get("kind") in KINDS:
            out.append(detail)
    # stored findings come back ordered by check name; the descriptors are listed in the order they are read
    return sorted(out, key=lambda d: (KINDS.index(d["kind"]), d["path"]))


def store_descriptors(registry: ProjectRegistry, slug: str, root: str | Path,
                      surveyed_at: str | None = None) -> list[dict]:
    """Read the tree at `root` and store what it says for `slug`. A run that finds nothing is stored as the one
    `no_descriptor` finding, superseding the previous run's rows (a descriptor that was removed stops answering)."""
    found = read_descriptors(root)
    registry.upsert_finding(slug, FINDINGS_KIND, findings_for(found),
                            surveyed_at=surveyed_at or datetime.utcnow().isoformat(), supersedes_previous=True)
    return found


class DatasetDescriptorSurveyor(BaseSurveyor):
    """Reads the dataset descriptors from the checkout at `local_path` and stores them as findings. It returns no
    annotations: nothing here is published to Egeria."""

    def __init__(self, project: Project, registry: ProjectRegistry, local_path: str | Path | None = None,
                 surveyed_at: str | None = None) -> None:
        super().__init__(project, registry)
        self.local_path = Path(local_path) if local_path else None
        self._surveyed_at = surveyed_at or datetime.utcnow().isoformat()

    @property
    def step_name(self) -> str:
        return STEP

    def run(self) -> list[Annotation]:
        if not self.local_path or not self.local_path.is_dir():
            log.info("%s: no checkout on disk — dataset descriptors not read", self.project.slug)
            return []
        store_descriptors(self.registry, self.project.slug, self.local_path, self._surveyed_at)
        return []
