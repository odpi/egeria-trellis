import pytest
from advisor.report_spec_parser import (
    parse_report_spec_markdown,
    validate_report_spec,
    register_report_spec
)
from pyegeria.view._output_format_models import FormatSet

# Sample Report Spec Document (RSD) content
SAMPLE_RSD = """
# Custom User Report

## Create Report Spec
### Target Type
My-User

### Heading
My Custom User Overview

### Description
A report listing users with roles and project detail links

### Action Function
MyProfile.get_my_profile

### Required Params
guid, name

### Optional Params
server_name

### Spec Params
verbosity=high, format_type=pretty, active=True, max_items=5, items_list=['A', 'B']

### Perspectives
developer, admin

### Questions
What is my profile?, Who am I?

## Create Column
### Name
Full Name

### Key
full_name

## Create Column
### Name
GUID

### Key
guid

### Format
True

## Create Column
### Name
Roles

### Key
roles

### Detail Spec
My-User-Roles-Detail

### Formats
LIST, REPORT
"""

def test_parse_report_spec_markdown():
    spec = parse_report_spec_markdown(SAMPLE_RSD)
    
    assert isinstance(spec, FormatSet)
    assert spec.target_type == "My-User"
    assert spec.heading == "My Custom User Overview"
    assert spec.description == "A report listing users with roles and project detail links"
    
    # Action Parameter validation
    assert spec.action is not None
    assert spec.action.function == "MyProfile.get_my_profile"
    assert spec.action.required_params == ["guid", "name"]
    assert spec.action.optional_params == ["server_name"]
    
    # Spec Params type verification
    params = spec.action.spec_params
    assert params["verbosity"] == "high"
    assert params["format_type"] == "pretty"
    assert params["active"] is True
    assert params["max_items"] == 5
    assert params["items_list"] == ["A", "B"]
    
    # Question spec (Perspectives and Questions)
    assert spec.question_spec is not None
    assert len(spec.question_spec) == 1
    qspec = spec.question_spec[0]
    assert qspec.perspectives == ["developer", "admin"]
    assert qspec.questions == ["What is my profile?", "Who am I?"]
    
    # Format & Column verification
    # Columns map to different formats:
    # "Full Name" and "GUID" should map to "ALL" since no "Formats" key is specified
    # "Roles" should map to "LIST" and "REPORT"
    formats_dict = {fmt.types[0]: fmt.attributes for fmt in spec.formats}
    
    assert "ALL" in formats_dict
    assert "LIST" in formats_dict
    assert "REPORT" in formats_dict
    
    # "Full Name" column check (default Format value: False, detail_spec: None)
    col_full_name = next(c for c in formats_dict["ALL"] if c.name == "Full Name")
    assert col_full_name.key == "full_name"
    assert col_full_name.format is False
    assert col_full_name.detail_spec is None
    
    # "GUID" column check (Format: True)
    col_guid = next(c for c in formats_dict["ALL"] if c.name == "GUID")
    assert col_guid.key == "guid"
    assert col_guid.format is True
    
    # "Roles" column check (Formats: LIST, REPORT; Detail Spec: My-User-Roles-Detail)
    col_roles_list = next(c for c in formats_dict["LIST"] if c.name == "Roles")
    assert col_roles_list.key == "roles"
    assert col_roles_list.detail_spec == "My-User-Roles-Detail"
    
    col_roles_report = next(c for c in formats_dict["REPORT"] if c.name == "Roles")
    assert col_roles_report.key == "roles"
    assert col_roles_report.detail_spec == "My-User-Roles-Detail"


def test_validate_report_spec(monkeypatch):
    # Invalid spec test (Action Function not Client.method)
    spec_invalid = parse_report_spec_markdown("""
## Create Report Spec
### Action Function
invalid_function
""")
    warnings = validate_report_spec(spec_invalid)
    assert len(warnings) > 0
    assert "Invalid Action Function format" in warnings[0]
    
    # Validate with non-existent method
    spec_not_found = parse_report_spec_markdown("""
## Create Report Spec
### Action Function
EgeriaTech.non_existent_method_xyz
""")
    warnings = validate_report_spec(spec_not_found)
    assert len(warnings) > 0
    assert "not found on" in warnings[0]

    # Validate with real/mocked method
    # EgeriaTech is imported dynamically in the validator, let's mock it
    class DummyEgeriaTech:
        def my_valid_method(self):
            pass

    monkeypatch.setattr("pyegeria.EgeriaTech", DummyEgeriaTech, raising=False)
    
    spec_valid = parse_report_spec_markdown("""
## Create Report Spec
### Action Function
EgeriaTech.my_valid_method
""")
    warnings = validate_report_spec(spec_valid)
    assert len(warnings) == 0


def test_register_report_spec():
    from pyegeria.view.base_report_formats import report_specs
    
    spec = parse_report_spec_markdown(SAMPLE_RSD)
    register_report_spec("test_spec_123", spec)
    
    assert "test_spec_123" in report_specs
    assert report_specs["test_spec_123"] == spec


def test_load_outbox_fallback(tmp_path, monkeypatch):
    from advisor.report_spec_docs import ReportSpecDocumentManager
    
    dm = ReportSpecDocumentManager()
    monkeypatch.setattr(dm, "_paths", {
        "inbox": tmp_path / "inbox",
        "outbox": tmp_path / "outbox",
        "trash": tmp_path / "trash",
        "versions": tmp_path / "versions",
    })
    for p in dm._paths.values():
        p.mkdir(parents=True, exist_ok=True)
        
    doc_id = "test_fallback_report"
    
    outbox_file = dm._paths["outbox"] / f"{doc_id}_executed_20260626_074923.md"
    outbox_file.write_text(SAMPLE_RSD + "\n\n---\n\n## Outcome\n**Status:** Completed\n", encoding="utf-8")
    
    content = dm.load(doc_id)
    
    assert content is not None
    assert "## Outcome" not in content
    assert "## Create Report Spec" in content


def test_move_to_outbox_fallback_and_folder_of(tmp_path, monkeypatch):
    from advisor.report_spec_docs import ReportSpecDocumentManager
    
    dm = ReportSpecDocumentManager()
    monkeypatch.setattr(dm, "_paths", {
        "inbox": tmp_path / "inbox",
        "outbox": tmp_path / "outbox",
        "trash": tmp_path / "trash",
        "versions": tmp_path / "versions",
    })
    for p in dm._paths.values():
        p.mkdir(parents=True, exist_ok=True)
        
    doc_id = "test_fallback_report"
    
    # 1. Place an executed file in outbox
    outbox_file = dm._paths["outbox"] / f"{doc_id}_executed_20260626_074923.md"
    outbox_file.write_text(SAMPLE_RSD + "\n\n---\n\n## Outcome\n**Status:** Completed\n", encoding="utf-8")
    
    # 2. Check folder_of with base doc_id and executed doc_id
    assert dm.folder_of(doc_id) == "outbox"
    assert dm.folder_of(f"{doc_id}_executed_20260626_074923") == "outbox"
    
    # 3. Call move_to_outbox (when not in inbox)
    outcome = "## Outcome\n**Status:** Completed\n**Run:** 2\n"
    outbox_doc_id = dm.move_to_outbox(doc_id, outcome)
    
    assert outbox_doc_id is not None
    assert f"{doc_id}_executed_" in outbox_doc_id
    
    # Verify the new file in outbox contains the outcome and is not in inbox
    new_outbox_file = dm._paths["outbox"] / f"{outbox_doc_id}.md"
    assert new_outbox_file.exists()
    
    content = new_outbox_file.read_text(encoding="utf-8")
    assert "## Outcome" in content
    assert "**Run:** 2" in content
    
    inbox_file = dm._paths["inbox"] / f"{doc_id}.md"
    assert not inbox_file.exists()



def test_parse_analytic_only_spec_with_output_types():
    rsd = """
## Create Report Spec
### Heading
Tile
### Description
An analytic tile
### Analytic Function
pkg.mod.func
### Analytic Spec Params
resource_type=database, limit=30
### Output Types
DICT, BAR
"""
    spec = parse_report_spec_markdown(rsd)
    # pyegeria 6.2.2 (ISSUE-141): no action function at all, not ""
    assert spec.action.function is None and spec.action.analytic_function == "pkg.mod.func"
    assert spec.action.analytic_spec_params == {"resource_type": "database", "limit": 30}
    assert [f.types for f in spec.formats] == [["DICT", "BAR"]]
    # and the validator does not warn "No Action Function" for an analytic-only spec
    assert validate_report_spec(spec) == []


def test_validator_still_warns_when_a_spec_has_no_function_of_either_kind():
    spec = FormatSet(heading="h", description="d", formats=[], action=None)
    assert validate_report_spec(spec) == ["No Action Function defined."]
