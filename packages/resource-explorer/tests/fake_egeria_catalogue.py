"""A stateful stand-in for Egeria, behind `CatalogueGateway`, for the slice B tests.

Built from what the scratch runs RECORDED (`evidence/SCRATCH-CATALOGUER-TEST-2-2026-10-05.md`),
not from what would be convenient:

* a schema-kind target makes the cataloguer create exactly that schema's tables and
  columns on its next refresh, and nothing under the database;
* the cataloguer hands names to JDBC as patterns, so a schema `a_b` also pulls in
  `aXb`'s tables and a table `x_y` also gets `xZy`'s columns;
* an archive RENAMES the element to `<qn>_archivedOn_<Java Date>` and keeps it as a Memento, exactly as
  Egeria 6.2 does (captured 2026-10-14, `tests/fixtures/egeria_6_2/*memento*`): a read by the ORIGINAL name
  answers "No elements found" even with `for_lineage`, the name is free, and a create under it makes a NEW
  element. The archived copy is found by its new name (`archived_siblings`), in the captured 6.2 shape. The
  old shape (an archived element still holding its name) is refused by `by_qn` (S19, owner ruling 2026-10-14);
* a soft delete of a parent that still has visible children is refused (the strict
  reading, so a parent-first delete order fails in the test, never silently passes);
* a removed target is still refreshed by the running connector until it is
  restarted (S17), and the connector never recreates an archived or deleted schema;
* creating a schema element whose qualified name already exists returns the same GUID.

* **Reads speak the LIVE wire shapes.** `read_element`, `elements_under` and `relationships`
  build the raw payloads Egeria really answers with (`live_catalogue_payloads.py`, recorded in
  the 2026-10-05 rehearsal) and parse them with the real gateway's own parsers, so a parser that
  reads the wrong shape fails every commit test instead of reading "nothing there" (D2).

Nothing here touches a network, a file or a registry.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import live_catalogue_payloads as _live  # noqa: E402
from live_catalogue_payloads import raw_element as _raw_element, raw_related as _raw_related  # noqa: E402

import copy  # noqa: E402
import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import resource_explorer.catalogue_gateway as _gw  # noqa: E402  (new parsers are looked up at call time)
from resource_explorer.catalogue_gateway import (  # noqa: E402
    ARCHIVE, CatalogTarget, ConnectorStatus, ElementRead, GatewayError, PublishedDatabase,
    Relationship, SurveyOutcome, like_matches, parse_element_answer, parse_elements_answer, parse_related_answer)

PROCESS_QN = "PostgreSQLSurvey::survey-postgres-database"
#: The captured 6.2 Memento element (a starts-with, forLineage find), the template every archived copy is drawn from.
_MEMENTO_FIXTURE = Path(__file__).parent / "fixtures" / "egeria_6_2" / "metadata_expert_find_with_string_for_lineage__memento.json"
#: Egeria's suffix is Java's `Date.toString()` in the platform's zone (GMT on the quickstart).
JAVA_DATE = "%a %b %d %H:%M:%S GMT %Y"


def captured_memento(guid: str, qn: str, type_name: str, archived: datetime | None = None) -> dict:
    """The captured 6.2 Memento answer with this element's GUID, archived name and type, and (given) the time it
    was archived in the Memento's `archiveDate` and the element's `versions.updateTime`, as Egeria stamps them."""
    el = copy.deepcopy(json.loads(_MEMENTO_FIXTURE.read_text())["answer"][0])
    el["elementGUID"] = guid
    el["type"]["typeName"] = type_name
    ep = el["elementProperties"]
    ep["propertiesAsStrings"]["qualifiedName"] = qn
    ep["propertyValueMap"]["qualifiedName"]["primitiveValue"] = qn
    if archived is not None:
        ms = int(archived.replace(tzinfo=timezone.utc).timestamp() * 1000)
        m = next(c for c in el["classifications"] if c["classificationName"] == "Memento")
        m["classificationProperties"]["propertiesAsStrings"]["archiveDate"] = str(ms)
        m["classificationProperties"]["propertyValueMap"]["archiveDate"]["primitiveValue"] = ms
        el["versions"]["updateTime"] = archived.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    return el
#: The job component the OpenLineage cataloguer made for Egeria's own survey action (READBACK note, last section).
CONNECTOR_JOB_QN = "DeployedSoftwareComponent::GovernanceActions::PostgreSQLSurvey::survey-postgres-database"


class FakeEgeria:
    def __init__(self, source: dict[str, dict[str, list[str]]] | None = None):
        # schema -> table -> [columns]; what the real database holds
        self.source = source or {}
        self.elements: dict[str, dict] = {}
        self.targets: list[CatalogTarget] = []
        self.calls: list[tuple] = []
        self.rels: dict[str, list[Relationship]] = {}
        self.deleted_order: list[tuple[str, str, str]] = []   # (qn, type, form)
        self.surveys: list[tuple[str, dict]] = []
        self.refreshes = 0
        self.restarts = 0
        self.connector_time = "2026-10-05T09:05:00"
        self.connector_errors: list[str] = []
        self.lingering: set[str] = set()
        self.fail: dict[str, str] = {}
        self.zones_ok = True
        self.owner_policy = "set"          # set | refuse | fail
        self.zones: dict[str, list[str]] = {}
        self.owners: dict[str, str] = {}
        self._n = 0
        self.db_guid = ""
        self.server_guid = ""
        # What Egeria itself puts on a new database element. On the 2026-10-05 build: NOTHING (the
        # read-back found no ZoneMembership on any element it or its engines created).
        self.default_zones: list[str] = []
        # The 2026-10-05 rehearsal: once the commit's own identity has written a ZoneMembership on
        # the database element, Egeria's security connector refuses the same identity (Classify,
        # anchored creates), the survey engine and the cataloguer every further write anchored to it.
        self.zone_lockout = True
        self.re_zone_writes: set[str] = set()
        self.zone_error = ("OPEN-METADATA-SECURITY-0011 User erinoverview is not authorized to issue "
                           "operation Classify on RelationalDatabase anchor element")
        # The attach mechanism (read-back 2026-10-05): Egeria's GovernanceActionType
        # `PostgreSQLGovernance::catalog-postgres-schema` attaches an EXISTING element given as action
        # target `newAsset`. accept | refuse (the action FAILS, no target) | error (initiate raises) |
        # pending (the action is IN_PROGRESS and attaches nothing until `finish_pending_actions`).
        self.catalog_action = "accept"
        self.actions: dict[str, dict] = {}          # engine action guid -> {status, message, schema}
        # Read-back lag: the first N `list_catalog_targets` reads after an initiation do not show the target
        # (the live attach takes about 4 s and the poll window is shorter than a slow one).
        self.target_lag_reads = 0
        self._lag_left = 0
        self.initiations: dict[str, int] = {}           # schema guid -> times the action type was initiated
        self.create_error_after_creating = False        # Egeria 500s on the create but the element exists (rehearsal 2)
        # 2026-10-06: the create 500s halfway. The element exists but has NO DataSetContent link to the
        # database and NO ResourceConnection (`create_error_half_built`), and a read by NAME can still land on
        # the OLD schema's tree (`stale_under`: extra elements the prefix search returns).
        self.create_error_half_built = False
        self.create_error_text = "SERVER_ERROR_500 => Egeria detected error: `https://localhost:9443/x/new-element`."
        self.stale_under: list[tuple[str, str]] = []
        self.survey_behaviour = "pending"         # pending | annotated | failed | empty (a survey takes time)
        self.survey_failure = ("OMES-SURVEY-ACTION-0018 The survey service threw an exception. "
                               "Details are in the audit log.")
        self.refresh_moves_time = True
        self.last_wire: tuple | None = None       # (kind, raw payload) of the newest read
        # The platform clock an archive stamps into the new name (a test moves it to make a newer archived copy).
        self.archive_clock = datetime(2026, 10, 9, 1, 28, 8)

    # -- helpers ---------------------------------------------------------
    def _guid(self, prefix: str = "g") -> str:
        self._n += 1
        return f"{prefix}{self._n:04d}-0000-0000-0000-000000000000"

    def _locked(self, guid: str, op: str) -> None:
        if self.zone_lockout and guid in self.re_zone_writes:
            raise GatewayError(f"{self.zone_error} (operation {op})")

    def _boom(self, op: str) -> None:
        if op in self.fail:
            raise GatewayError(self.fail[op])

    def _visible(self, e: dict) -> bool:
        return not e["deleted"] and not e["archived"]

    def by_qn(self, qn: str) -> dict | None:
        for g, e in self.elements.items():
            # S19: Egeria never leaves an archived element under its own name. A test that builds that shape
            # (`archived` set without the rename) is testing an Egeria that does not exist: refuse it loudly.
            assert not (e["archived"] and _gw.ARCHIVED_ON not in e["qn"]), \
                f"old fake shape: archived element {g} still holds its qualifiedName {e['qn']!r}; use archive()"
            if e["qn"] == qn and not e["deleted"]:        # a soft-deleted element is gone; its name is free
                return {**e, "guid": g}
        return None

    def add_element(self, qn: str, type_name: str, parent: str = "") -> str:
        g = self._guid("e")
        self.elements[g] = {"qn": qn, "type": type_name, "parent": parent, "archived": False, "deleted": False}
        return g

    def archive(self, guid: str) -> str:
        """What Egeria does on an archive: Memento AND the rename `<qn>_archivedOn_<date>`. Returns the new name."""
        e = self.elements[guid]
        e["original_qn"] = e["qn"]
        e["qn"] = f"{e['qn']}{_gw.ARCHIVED_ON}{self.archive_clock.strftime(JAVA_DATE)}"
        e["archived"] = True                # the clock does not move by itself: one leave-out is one moment
        e["archived_time"] = self.archive_clock
        return e["qn"]

    def reset(self) -> None:
        """An Egeria reset: everything RE ever wrote is gone."""
        self.elements.clear()
        self.targets.clear()
        self.lingering.clear()
        self.db_guid = self.server_guid = ""

    def ops(self, name: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == name]

    # -- step 1 ----------------------------------------------------------
    def publish_database(self, db_entity, db_user, db_pwd, *, registry=None, submitted_by="",
                         reinclude_guid="") -> PublishedDatabase:
        self.calls.append(("publish_database",))
        self._boom("publish_database")
        server = f"{getattr(db_entity, 'egeria_host', '') or db_entity.host}:{db_entity.port}"
        qn = f"PostgreSQL Relational Database::{server}::{db_entity.database_name}"
        if self.by_qn(qn) is None:
            # The surveyor's own S19 guard (`_refuse_unconfirmed_reinclude`): the same decision, before any create.
            got = _gw.decide_reinclude(None, self.archived_siblings(qn), reinclude_guid)
            if got["state"] in (_gw.NEEDS_CONFIRMATION, "changed"):
                raise GatewayError(f"the database is {got['text']} · nothing created")
        if not self.db_guid or self.by_qn(qn) is None:
            # By name, as the surveyor resolves it: an archived database no longer holds the name, so a publish
            # creates a NEW database element beside the archived one.
            if not self.server_guid or self.by_qn(f"PostgreSQL Server::{server}") is None:
                # The server is an element too (shared by host:port), so a later read finds it.
                self.server_guid = self._guid("s")
                self.elements[self.server_guid] = {"qn": f"PostgreSQL Server::{server}", "type": "SoftwareServer",
                                                   "parent": "", "archived": False, "deleted": False}
            self.db_guid = self._guid("d")
            self.elements[self.db_guid] = {"qn": qn, "type": "RelationalDatabase", "parent": "", "archived": False, "deleted": False}
            self.zones[self.db_guid] = list(self.default_zones)
        if registry is not None:
            # as the real surveyor does (egeria_database_surveyor.py ~:599): it records the database GUID on RE's
            # row itself while publishing (Brief Z2 round 4 models it, so "never recorded" is tested for real)
            registry.set_database_egeria_guid(db_entity.slug, self.db_guid)
        return PublishedDatabase(self.server_guid, self.db_guid, server, qn)

    def publish_local_report(self, db_entity, db_user, db_pwd, measured, *, registry=None, submitted_by="") -> dict:
        self.calls.append(("publish_local_report", measured.get("surveyed_at")))
        self._boom("publish_local_report")
        return {"report_guid": self._guid("r"), "annotation_count": 3, "annotations_in_egeria": 3}

    def set_zone_membership(self, guid, zones) -> bool:
        self.calls.append(("set_zone_membership", guid, tuple(zones)))
        if not self.zones_ok:
            raise GatewayError(self.zone_error.replace("Classify", "Classify (ZoneMembership)"))
        self.zones[guid] = list(zones)
        self.re_zone_writes.add(guid)
        return True

    def read_zones(self, guid):
        self.calls.append(("read_zones", guid))
        self._boom("read_zones")
        return list(self.zones.get(guid, []))

    def set_owner(self, guid, owner):
        self.calls.append(("set_owner", guid, owner))
        self._locked(guid, "Classify")
        if self.owner_policy == "fail":
            raise GatewayError("500 Egeria failed")
        if self.owner_policy == "refuse":
            return "refused", "LOCAL_CANNOT_CHANGE_EXTERNAL"
        self.owners[guid] = owner
        return "set", f"Ownership set to {owner}"

    # -- reads and schema elements --------------------------------------
    def raw_element(self, guid: str) -> dict:
        """The element as Egeria answers (live shape): `elementGUID`, `elementProperties`, ...; an archived one
        is the captured 6.2 Memento answer."""
        e = self.elements[guid]
        if e["archived"]:
            return captured_memento(guid, e["qn"], e["type"], e.get("archived_time"))
        return _raw_element(guid, e["qn"], e["type"], archived=e["archived"], zones=self.zones.get(guid))

    def raw_related(self, guid: str) -> dict:
        """`get_all_related_elements`' answer (live shape): a dict with `elementList`."""
        items = []
        for r in self._relationship_list(guid):
            if r.type_name == "ActionTarget":
                other = _live.raw_engine_action(r.other_guid or "act", r.activity_status,
                                                message=getattr(r, "completion_message", ""),
                                                completion_ms=getattr(r, "completion_time", ""),
                                                request_type=getattr(r, "action_kind", ""))
                props = _live.relationship_properties("serverToSurvey", r.activity_status,
                                                      getattr(r, "completion_time", ""))
                items.append((r.type_name, r.guid or f"rel-{len(items)}", other, props))
                continue
            other = _raw_element(r.other_guid or "other", getattr(r, "other_qualified_name", "") or f"other::{r.other_guid}",
                                 r.other_type or "Referenceable",
                                 props=({"displayName": r.other_name} if getattr(r, "other_name", "") else None))
            items.append((r.type_name, r.guid or f"rel-{len(items)}", other))
        return _raw_related(self.raw_element(guid) if guid in self.elements else _raw_element(guid, "", ""), items)

    def read_element(self, qualified_name, *, for_lineage=False):
        self.calls.append(("read_element", qualified_name, for_lineage))
        self._boom("read_element")
        e = self.by_qn(qualified_name)
        if e is None or (e["archived"] and not for_lineage):
            self.last_wire = ("element", "No elements found")
            return parse_element_answer("No elements found")
        self.last_wire = ("element", self.raw_element(e["guid"]))
        return parse_element_answer(self.last_wire[1])

    def qualified_name_of(self, guid):
        self.calls.append(("qualified_name_of", guid))
        return self.elements[guid]["qn"]

    def mark_on_behalf(self, guid, requester, owner):
        self.calls.append(("mark_on_behalf", guid, requester, owner))
        return getattr(self, "mark_on_behalf_says", "")

    def create_schema_element(self, db_entity, schema, database_guid, *, description=""):
        self.calls.append(("create_schema_element", schema, database_guid))
        self._boom("create_schema_element")
        self._locked(database_guid, "Create")
        server = f"{getattr(db_entity, 'egeria_host', '') or db_entity.host}:{db_entity.port}"
        qn = f"PostgreSQL Relational Database Schema::{server}::{db_entity.database_name}.{schema}"
        e = self.by_qn(qn)          # an archived schema no longer holds this name: a create here is a NEW element
        if e is not None and not e["deleted"]:
            return e["guid"]
        g = self.add_element(qn, "DeployedDatabaseSchema", parent=database_guid)
        if self.create_error_half_built:
            self.elements[g]["linked"] = self.elements[g]["connected"] = False
            self.create_error_half_built = False
            raise GatewayError(self.create_error_text)
        for suffix, typ in (("Connection", "VirtualConnection"), ("Endpoint", "Endpoint"),
                            ("SecretsStoreConnection", "Connection"), ("SecretStoreEndpoint", "Endpoint")):
            self.add_element(f"{qn}::{suffix}", typ, parent=g)        # the template's own connection graph
        if self.create_error_after_creating:
            self.create_error_after_creating = False
            raise GatewayError(self.create_error_text)
        return g

    # -- targets -----------------------------------------------------------
    def list_catalog_targets(self):
        self.calls.append(("list_catalog_targets",))
        self._boom("list_catalog_targets")
        if self._lag_left > 0:                          # the attach exists on Egeria's side, not yet readable
            self._lag_left -= 1
            raw = "No elements found"
        else:
            raw = [_live.raw_catalog_target(t.relationship_guid, t.element_guid, t.name) for t in self.targets] \
                or "No elements found"
        self.last_wire = ("targets", raw)
        return _gw.parse_catalog_targets_answer(raw)

    def add_catalog_target(self, element_guid, name):
        self.calls.append(("add_catalog_target", element_guid, name))
        self._boom("add_catalog_target")
        el = self.elements.get(element_guid)
        assert el is not None and el["type"] == "DeployedDatabaseSchema", \
            "a catalog target must be a SCHEMA-kind element, never the database or the server"
        assert not any(t.element_guid == element_guid for t in self.targets), "attached the same schema twice"
        rel = self._guid("t")
        self.targets.append(CatalogTarget(rel, element_guid, name))
        return rel

    def initiate_catalog_action(self, schema_guid, request_parameters):
        """Egeria's `catalog-postgres-schema` action type, as answered live: a GUIDResponse whose guid
        is the engine action; the attach happens afterwards, on Egeria's side."""
        self.calls.append(("initiate_catalog_action", schema_guid, dict(request_parameters)))
        self._boom("initiate_catalog_action")
        if self.catalog_action == "error":
            raise GatewayError("OMAG-GOVERNANCE-ACTION-400-001 the action type does not accept an existing element")
        self.initiations[schema_guid] = self.initiations.get(schema_guid, 0) + 1
        assert self.initiations[schema_guid] == 1 and not any(t.element_guid == schema_guid for t in self.targets), \
            "Egeria creates ANOTHER CatalogTarget on every initiation: RE initiated an attach for a schema that has one"
        self._lag_left = self.target_lag_reads
        guid = self._guid("a")
        resp = dict(_live.LIVE_INITIATE_RESPONSE, guid=guid)
        self.last_wire = ("initiate", resp)
        el = self.elements.get(schema_guid)
        if self.catalog_action == "refuse":
            self.actions[guid] = {"status": "FAILED", "schema": schema_guid,
                                  "message": "GOVERNANCE-ACTION-CONNECTORS-0099 the new asset is not acceptable. Details follow."}
        elif self.catalog_action == "pending":
            self.actions[guid] = {"status": "IN_PROGRESS", "schema": schema_guid, "message": ""}
        else:
            assert el is not None and el["type"] == "DeployedDatabaseSchema"
            self.targets.append(CatalogTarget(self._guid("t"), schema_guid, el["qn"].split("::", 2)[2]))
            self.actions[guid] = {"status": "COMPLETED", "schema": schema_guid, "message": "attached"}
        return _gw.parse_initiate_answer(resp)

    def finish_pending_actions(self):
        for g, a in self.actions.items():
            if a["status"] == "IN_PROGRESS":
                a["status"] = "COMPLETED"
                el = self.elements[a["schema"]]
                self.targets.append(CatalogTarget(self._guid("t"), a["schema"], el["qn"].split("::", 2)[2]))

    def engine_action_status(self, guid):
        self.calls.append(("engine_action_status", guid))
        self._boom("engine_action_status")
        a = self.actions[guid]
        raw = _live.raw_engine_action(guid, a["status"], message=a["message"])
        self.last_wire = ("engine_action", raw)
        return _gw.parse_engine_action_answer(raw)

    def remove_catalog_target(self, relationship_guid):
        self.calls.append(("remove_catalog_target", relationship_guid))
        self._boom("remove_catalog_target")
        for t in list(self.targets):
            if t.relationship_guid == relationship_guid:
                self.targets.remove(t)
                self.lingering.add(t.element_guid)      # S17: the running connector keeps it

    # -- under a schema ----------------------------------------------------
    def elements_under(self, prefix):
        self.calls.append(("elements_under", prefix))
        self._boom("elements_under")
        raw = [self.raw_element(g) for g, e in self.elements.items()
               if self._visible(e) and e["qn"].startswith(prefix)]
        raw += [_raw_element(f"stale-{i}", qn, typ) for i, (qn, typ) in enumerate(self.stale_under) if qn.startswith(prefix)]
        self.last_wire = ("elements", raw or "No elements found")
        return parse_elements_answer(self.last_wire[1])

    def archived_siblings(self, qualified_name):
        """The starts-with, forLineage find for `<qn>_archivedOn_`, answered in the captured 6.2 shape and
        parsed by the real gateway's parser."""
        self.calls.append(("archived_siblings", qualified_name))
        self._boom("archived_siblings")
        prefix = qualified_name + _gw.ARCHIVED_ON
        raw = [self.raw_element(g) for g, e in self.elements.items() if not e["deleted"] and e["qn"].startswith(prefix)]
        self.last_wire = ("elements", raw or "No elements found")
        return _gw.parse_archived_siblings(self.last_wire[1], qualified_name)

    def schema_elements(self, prefix):
        """The one paged schema find (live and archived DeployedDatabaseSchema under `prefix`), as raw 6.2 answers
        parsed by the real parser. One call however many schemas the preview asks about."""
        self.calls.append(("schema_elements", prefix))
        self._boom("schema_elements")
        raw = [self.raw_element(g) for g, e in self.elements.items()
               if not e["deleted"] and e["type"] == "DeployedDatabaseSchema" and e["qn"].startswith(prefix)]
        self.last_wire = ("elements", raw or "No elements found")
        return parse_elements_answer(self.last_wire[1])

    def relationships(self, guid):
        self.calls.append(("relationships", guid))
        self._boom("relationships")
        self.last_wire = ("related", self.raw_related(guid))
        return parse_related_answer(self.last_wire[1])

    def _relationship_list(self, guid):
        out = list(self.rels.get(guid, []))
        e = self.elements.get(guid)
        typ = e["type"] if e is not None else ""
        if typ == "DeployedDatabaseSchema":
            for t in self.targets:                      # read by the element's own guid: only ITS target shows
                if t.element_guid == guid:
                    out.append(Relationship("CatalogTarget", other_guid="cat", guid=t.relationship_guid))
            if e.get("linked", True) and e["parent"]:
                out.append(Relationship("DataSetContent", other_guid=e["parent"], other_type="RelationalDatabase"))
            # what the template and Egeria's own engines put on every schema (rehearsal 2, step 4)
            out.append(Relationship("SourcedFrom", other_guid="own"))
            if e.get("connected", True):
                out.append(Relationship("ResourceConnection", other_guid="own"))
            # Egeria's OpenLineage cataloguer records the governance action that touched it (rehearsal 2,
            # READBACK note): a DataFlow to the action's job component, `DeployedSoftwareComponent::GovernanceActions::...`
            out.append(Relationship("DataFlow", other_guid="job", other_type="DeployedSoftwareComponent",
                                    other_qualified_name=CONNECTOR_JOB_QN, other_name="survey-postgres-database"))
        elif typ == "VirtualConnection":
            for t in ("ConnectToEndpoint", "ConnectionConnectorType", "EmbeddedConnection"):
                out.append(Relationship(t, other_guid="own"))
        # the graph the cataloguer wired: schema --Schema--> schema type --AttributeForSchema--> table --Nested...--> column
        kid_rel = {"DeployedDatabaseSchema": ("Schema", "RelationalDBSchemaType"),
                   "RelationalDBSchemaType": ("AttributeForSchema", "RelationalTable"),
                   "RelationalTable": ("NestedSchemaAttribute", "RelationalColumn")}.get(typ)
        if kid_rel:
            for c, x in self.elements.items():
                if x["parent"] == guid and x["type"] == kid_rel[1] and self._visible(x):
                    out.append(Relationship(kid_rel[0], other_guid=c, other_type=x["type"], other_qualified_name=x["qn"]))
        if typ == "RelationalDBSchemaType" and e["parent"]:
            out.append(Relationship("Schema", other_guid=e["parent"], other_type="DeployedDatabaseSchema"))
        return out

    def add_engine_action(self, schema_qn: str, status: str, *, completion_time: str = "", message: str = "",
                          guid: str = "", kind: str = "") -> str:
        """An engine action targeting a schema (a survey, or the process's own step): the live
        ActionTarget relationship carries `activityStatus` and `completionTime`."""
        g = self.by_qn(schema_qn)["guid"]
        ag = guid or self._guid("ea")
        self.rels.setdefault(g, []).append(Relationship(
            "ActionTarget", other_guid=ag, other_type="EngineAction", guid=self._guid("rl"),
            activity_status=status, completion_time=completion_time, completion_message=message,
            action_kind=kind))
        return ag

    def add_term_assignment(self, qn_suffix: str, schema_qn: str, n: int = 1) -> None:
        g = self.by_qn(f"{schema_qn}::{qn_suffix}")["guid"]
        self.rels.setdefault(g, []).extend(Relationship("SemanticAssignment", other_guid="term") for _ in range(n))

    def delete_element(self, guid, form):
        self.calls.append(("delete_element", guid, form))
        self._boom("delete_element")
        e = self.elements[guid]
        kids = [c for c, x in self.elements.items() if x["parent"] == guid and self._visible(x)]
        if form == ARCHIVE:
            # NO cascade (the content is anchored to the database): an element still holding visible content is
            # refused, so a parent-first archive order fails in the test, never silently passes.
            if kids:
                raise GatewayError(f"400 OMAG-REPOSITORY-HANDLER-400-010 an archive of {e['type']} element {guid} would "
                                   f"leave its dependent element {kids[0]} behind")
            self.deleted_order.append((e["qn"], e["type"], form))
            self.archive(guid)
            return
        if kids:
            raise GatewayError(f"401 OMAG-GENERIC-HANDLERS-403-005 A delete of {e['type']} element {guid} is not "
                               f"permitted because it still has a dependent element {kids[0]}")
        e["deleted"] = True
        self.deleted_order.append((e["qn"], e["type"], form))

    # -- survey and connector ---------------------------------------------
    def initiate_survey(self, database_guid, request_parameters):
        self.calls.append(("initiate_survey", database_guid, dict(request_parameters)))
        self._boom("initiate_survey")
        self._locked(database_guid, "UpdateProperties")
        self.surveys.append((database_guid, dict(request_parameters)))
        return self._guid("a"), PROCESS_QN

    def survey_outcome(self, database_guid, engine_action_guid, since):
        self.calls.append(("survey_outcome", database_guid, engine_action_guid))
        self._boom("survey_outcome")
        b = self.survey_behaviour
        if b == "failed":
            return SurveyOutcome("FAILED", self.survey_failure, self._guid("r"), 0)
        if b == "pending":
            return SurveyOutcome("IN_PROGRESS", "", "", None)
        if b == "partial":                              # rehearsal 2: a report with some annotations, action running
            return SurveyOutcome("IN_PROGRESS", "", self._guid("r"), 9)
        if b == "empty":
            return SurveyOutcome("COMPLETED", "", self._guid("r"), 0)
        return SurveyOutcome("COMPLETED", "", self._guid("r"), 23)

    def connector_status(self):
        self.calls.append(("connector_status",))
        self._boom("connector_status")
        return ConnectorStatus("JDBCDatabaseCataloguer", getattr(self, "connector_state", "WAITING"), self.connector_time)

    def refresh_connector(self, timeout=120):
        self.calls.append(("refresh_connector", timeout))
        self._boom("refresh_connector")
        self.refreshes += 1
        if self.refresh_moves_time:
            self.connector_time = f"2026-10-05T09:{10 + self.refreshes:02d}:00"
        self.run_cataloguer()

    def restart_connector(self):          # RE must never call this; a test asserts it stays 0
        self.restarts += 1

    def run_cataloguer(self) -> None:
        """One cataloguer cycle: every attached (and, per S17, every lingering) schema."""
        seen: set[str] = set()
        for t in list(self.targets):
            self._cycle(t.element_guid)
            seen.add(t.element_guid)
        for g in sorted(self.lingering - seen):
            self._cycle(g)

    def _cycle(self, schema_guid: str) -> None:
        e = self.elements.get(schema_guid)
        if e is None or e["deleted"] or e["archived"]:
            self.connector_errors.append(f"JDBC-INTEGRATION-CONNECTOR-0006 {schema_guid} not visible: nothing recreated")
            return
        # the schema name is everything after `<db>.`; qualified names are `...::<db>.<schema>`
        pattern = e["qn"].split("::", 2)[2].split(".", 1)[1]
        # The cataloguer makes the schema type itself (`<schemaQN>_schemaType`, `Schema` link) when it catalogues the
        # target; tables hang off the schema TYPE (AttributeForSchema), columns off the table (NestedSchemaAttribute),
        # and all of them are anchored to the DATABASE, so nothing cascades from the schema (live read 2026-10-06).
        stqn = f"{e['qn']}_schemaType"
        st = (self.by_qn(stqn) or {}).get("guid") or self.add_element(stqn, "RelationalDBSchemaType", parent=schema_guid)
        for sname, tables in self.source.items():
            if not like_matches(pattern, sname):        # JDBC pattern: a_b also returns aXb
                continue
            for tname, cols in tables.items():
                tqn = f"{e['qn']}::{tname}"
                tg = (self.by_qn(tqn) or {}).get("guid") or self.add_element(tqn, "RelationalTable", parent=st)
                allcols = list(cols)
                for t2, c2 in tables.items():              # column pattern: x_y also returns xZy's columns
                    if t2 != tname and like_matches(tname, t2):
                        allcols += c2
                for c in dict.fromkeys(allcols):
                    cqn = f"{tqn}::{c}"
                    if self.by_qn(cqn) is None:
                        self.add_element(cqn, "RelationalColumn", parent=tg)
