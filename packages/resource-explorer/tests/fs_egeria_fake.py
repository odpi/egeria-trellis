"""A stateful fake of the Egeria boundary a file-system publish crosses (C3): the report lookup by qualifiedName, the
report create, the annotation create and the read of a report's annotations. Everything else is the REAL publisher
(`EgeriaFileSystemSurveyor`, its `publish_annotations` loop, its read-back), so a test sees exactly which creates the
real code decided to make. Nothing contacts Egeria.

`FakeEgeria.report_creates` and `.annotation_creates` are what the publisher asked Egeria to create; `fail_after` makes
the annotation create raise once that many have been created (an interrupted publish)."""
from __future__ import annotations


class FakeEgeria:
    def __init__(self, *, fail_after: int | None = None, report_readable: bool = True):
        self.reports: dict[str, str] = {}            # qualifiedName -> GUID
        self.annotations: dict[str, dict] = {}       # qualifiedName -> {"guid", "report", "type", "body"}
        self.report_creates: list[str] = []
        self.annotation_creates: list[str] = []
        self.fail_after = fail_after
        self.report_readable = report_readable
        self.reads_of_report: int = 0

    # the boundary ----------------------------------------------------------------------------------------------
    def find(self, name: str, type_name=None, *, qualified_name: bool = False) -> str:
        if name in self.reports:
            return self.reports[name]
        return (self.annotations.get(name) or {}).get("guid", "")

    def create_asset(self, body=None, **kw) -> str:
        qn = body["properties"]["qualifiedName"]
        guid = f"report-{len(self.reports) + 1}"
        self.reports[qn] = guid
        self.report_creates.append(qn)
        return guid

    def create_annotation(self, body=None, **kw) -> str:
        if self.fail_after is not None and len(self.annotation_creates) >= self.fail_after:
            raise RuntimeError("Egeria went away")
        qn = body["properties"]["qualifiedName"]
        guid = f"ann-{len(self.annotations) + 1}"
        self.annotations[qn] = {"guid": guid, "report": body["parentGUID"], "type": body["properties"].get("class", ""),
                                "body": body}
        self.annotation_creates.append(qn)
        return guid

    def report_annotations(self, report_guid: str):
        self.reads_of_report += 1
        if not self.report_readable:
            return None
        return [{"qualified_name": qn, "guid": a["guid"]} for qn, a in self.annotations.items() if a["report"] == report_guid]

    def get_asset_by_guid(self, guid, **kw):
        return {"elementHeader": {"guid": guid, "type": {"typeName": "SurveyReport"}}, "properties": {"qualifiedName": "q"}}


def install(monkeypatch, egeria: FakeEgeria):
    """Replace the publisher class with the real one over `egeria`. Returns the fake."""
    from resource_explorer.surveyors.filesystem import egeria_filesystem_surveyor as mod

    real = mod.EgeriaFileSystemSurveyor

    class _Maker:
        create_asset = staticmethod(egeria.create_asset)
        get_asset_by_guid = staticmethod(egeria.get_asset_by_guid)

    class _Discovery:
        create_annotation = staticmethod(egeria.create_annotation)

    class Fake(real):
        def __init__(self, **kw):
            super().__init__()
            self.connect = lambda: None
            self._asset_maker = _Maker()
            self._discovery = _Discovery()

        def _find_element_guid(self, name, type_name=None, *, qualified_name=False):
            return egeria.find(name, type_name, qualified_name=qualified_name)

        def _report_annotations(self, report_guid):
            return egeria.report_annotations(report_guid)

    monkeypatch.setattr(mod, "EgeriaFileSystemSurveyor", Fake)
    return egeria
