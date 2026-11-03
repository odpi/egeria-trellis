# DataQualityRule: pyegeria convenience API spec

**Status: specification only, not implemented.** Written 2026-10-19 as the companion to
`DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md`, following the owner's 2026-08-13 direction for
Automate: "define what we want pyegeria/egeria to do and then I can add it". This is
egeria-python's ground. RE states what it needs; the pyegeria owner decides the shape.

## Why this exists

The design publishes each check a person accepts as one Egeria `DataQualityRule` (model
0450), linked by `GovernedBy` to the column or to the database Asset. Each run also has to
read those rules back, because a rule edited in Egeria overrides RE's copy. RE should call
named methods for this, not hand-build request bodies for a type that pyegeria has never
been exercised against.

## What is already real (confirmed by reading source on 2026-10-19)

- **The type exists on the running platform.** `OpenMetadataTypesArchive6_2.java`
  (`getDataQualityRuleEntity`, about line 1697) adds it, with supertype `GovernanceRule`.
  The type was added by `571034df11` (2026-09-06), which is an ancestor of the quickstart's
  `85d6c48a`.
  - Attributes: `qualityDimension`, `checkType`, `metric`, `severity`, `businessImpact`,
    `method`, `units`, `schedule`, `scheduler`, `expression`, `qualityEngine` and
    `comparisonOperator`, all strings, plus `thresholdValues` (an array of strings).
  - None of these is a closed enum. The ODCS vocabularies are Java enums under
    `frameworks/integration/bitol/odcs/` (`DataContractQualityType`, `…Metric`,
    `…Severity`, `…ComparisonOperator` and `…QualityDimension`), and the Core Content Pack
    registers some of them as valid metadata values.
- **The server deserialises the class.** `GovernanceRuleProperties` lists
  `DataQualityRuleProperties` in `@JsonSubTypes`, and `GovernanceControlProperties` and
  `GovernanceDefinitionProperties` chain to it.
- **The Governance Officer view service has the endpoints**
  (`GovernanceOfficerResource.java`):
  - `POST …/governance-officer/governance-definitions` creates a rule (body
    `NewElementRequestBody`);
  - `…/{guid}/update`, `…/{guid}/retrieve`, `…/by-name` and `…/by-search-string`;
  - `POST …/elements/{elementGUID}/governed-by/definition/{definitionGUID}/attach` and
    `…/detach` handle the link.
- **pyegeria has the generic methods** (`pyegeria/omvs/governance_officer.py`):
  - `create_governance_definition(body)` (line 429);
  - `update_governance_definition(guid, body)`;
  - `get_governance_definition_by_guid` and `get_governance_definitions_by_name`;
  - `find_governance_definitions`;
  - `attach_governed_by_definition(element_guid, definition_guid, body)` (line 1603) and
    `detach_governed_by_definition`;
  - `ClassificationExplorer.get_governed_by_definitions` (`classification_explorer.py:1362`).
- **Egeria's own Bitol (ODCS) mapper is the reference usage.** `DataContractMapper`
  (`linkQualityRules`, `getDataQualityRuleProperties`, `getComparison`) does three things:
  - it creates one rule per ODCS check, with qualified name `<elementQN>::Quality::<id>`;
  - it finds an existing rule by qualified name and updates it instead of creating a
    duplicate;
  - it calls `addGovernanceDefinitionToElement` with an empty `GovernedByProperties`.

## The gap

**No pyegeria method names this type.** "DataQualityRule" appears only in
`md_processing/md_processing_utils/element_status_fields.py:173`, as a status-field map
entry. There is also no Dr.Egeria "Create Data Quality Rule" command; "Create Governance
Rule" (`commands_governance_officer_compact.json:4135`) creates the base `GovernanceRule`.

The generic path *probably* works today:
- the server registers the class;
- `GOV_DEF_PROPERTIES_LIST` (lines 37-44) omits `DataQualityRuleProperties`, but its check in
  `validate_new_element_request` is commented out;
- `NewElementRequestBody.properties` is a plain dict.

So a hand-built body passes through. But **nothing has exercised it**. Dr.Egeria's processor
already has a fallback that retries with the base `GovernanceDefinitionProperties` when the
server rejects a subtype class (`md_processing/v2/governance.py:87-130`, error
`OMAG-COMMON-400-029`). That fallback would silently create a plain `GovernanceDefinition`
and lose every quality attribute. It is exactly the kind of quiet downgrade a dedicated
method should refuse to make.

## Proposed API

All methods go on the Governance Officer client, with async twins following the module's
existing pattern.

```python
def create_data_quality_rule(
    self,
    qualified_name: str,
    display_name: str,
    *,
    quality_dimension: str | None = None,
    check_type: str | None = None,          # text | library | sql | custom
    metric: str | None = None,              # nullValues | missingValues | invalidValues | duplicateValues | rowCount | <other>
    comparison_operator: str | None = None, # mustBe | mustNotBe | mustBeGreaterThan | ... | mustNotBeBetween
    threshold_values: list[str] | None = None,
    severity: str | None = None,            # info | warning | error
    business_impact: str | None = None,
    method: str | None = None,
    units: str | None = None,
    quality_engine: str | None = None,
    expression: str | None = None,
    schedule: str | None = None,
    scheduler: str | None = None,
    summary: str | None = None,
    description: str | None = None,
    scope: str | None = None,
    content_status: str | None = None,      # e.g. ACTIVE | DRAFT
    additional_properties: dict[str, str] | None = None,
    anchor_guid: str | None = None,         # None -> isOwnAnchor=True
) -> str:
    """Create one DataQualityRule and return its GUID.

    Sends properties class "DataQualityRuleProperties" with typeName "DataQualityRule".
    Must NOT fall back to a base GovernanceDefinition if the server rejects the subtype:
    raise instead, because a fallback silently loses every quality attribute.
    Validates locally: between-operators take exactly two threshold values, all others one.
    Values outside the ODCS vocabularies are allowed and sent as written (the server's sets
    are open), but a debug log names them.

    NEEDS LIVE VERIFICATION before shipping: create against a disposable qualifiedName
    (prefix pyeg_rt_*), read back by GUID, and compare every attribute, including
    thresholdValues order. Then delete and verify that it is gone by GUID.
    """

def update_data_quality_rule(self, rule_guid: str, *, merge_update: bool = True, **attributes) -> None:
    """Update the attributes named, with the same validation as create. merge_update=False replaces."""

def get_data_quality_rule(self, rule_guid: str) -> dict | str:
    """Retrieve one rule and return a flat dict of its properties (thresholdValues as a list),
    plus a 'governedElements' list of {guid, typeName, qualifiedName} from its GovernedBy links.
    Returns "No elements found" in the module's usual way when the GUID does not resolve.
    Never raises NotFound for a GUID that resolves under another type: report the type instead."""

def find_data_quality_rules(
    self, search_string: str = "*", *, starts_with: bool = True, ends_with: bool = False,
    ignore_case: bool = False, start_from: int = 0, page_size: int = 0,
) -> list[dict] | str:
    """find_governance_definitions restricted to metadataElementTypeName "DataQualityRule"
    (and its subtypes). Same flat dict shape as get_data_quality_rule."""

def get_data_quality_rules_for_element(self, element_guid: str) -> list[dict] | str:
    """The DataQualityRules linked to an element by GovernedBy: get_governed_by_definitions
    filtered to this type. RE calls it on the database Asset and on each column element."""

def link_data_quality_rule(self, element_guid: str, rule_guid: str,
                           label: str | None = None, description: str | None = None) -> None:
    """attach_governed_by_definition with a GovernedByProperties body. Idempotent: if the
    link already exists, do nothing and return, rather than creating a second edge.
    (Link commands elsewhere in Egeria are not idempotent; this method checks first.)"""

def unlink_data_quality_rule(self, element_guid: str, rule_guid: str) -> None:
    """detach_governed_by_definition."""
```

**Optional, but valuable for Dr.Egeria parity:** add a compact command, "Create Data Quality
Rule", whose attributes match `create_data_quality_rule`, and a "Link Data Quality Rule"
that reuses "Link Governed By". These go through the `dr-egeria-command-sync` flow in
egeria-python.

## What RE does once this lands

- **Publish.** RE runs `find_data_quality_rules` on the planned qualified name
  (`<databaseAssetQN>::Quality::<schema>.<table>[.<column>]::<Test_Type>`). It creates or
  updates the rule, calls `link_data_quality_rule` for the column element, or for the
  database Asset when no column element exists, and then reads the rule back with
  `get_data_quality_rule` before it records the cache row. This is the same
  find-or-create-then-link pattern `EgeriaPublisher` uses, with the Bitol mapper's
  qualified-name shape.
- **Run checks.** RE calls `get_data_quality_rule` for each cached `rule_guid`. Egeria's
  `thresholdValues`, `comparisonOperator` and `severity` override RE's local copy. RE does
  not execute any rule whose `checkType` is `sql` or whose `expression` RE did not write.
- **Until then.** RE wraps the generic `create_governance_definition`,
  `attach_governed_by_definition` and `get_governance_definition_by_guid` calls in one
  module (`surveyors/database/quality/egeria_rules.py`) with the same signatures. Switching
  to this API is then a change in one file. That module must not have the subtype-to-base
  fallback.
