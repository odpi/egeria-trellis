# Live Egeria responses (real-shape fixtures)

Read-only captures from the laz quickstart (`qs-view-server`, platform `https://localhost:9443`,
user `erinoverview`) on 2026-11-11, using pyegeria 6.2.33 in the Portal image
(`egeria-quickstart-pyegeria-web`). They were captured for EA Phase 3 A-P (perspectives, zones and
enrichment absence). Nothing was written to Egeria.

Each file is a pyegeria `output_format="JSON"` answer, copied as returned. The only change is
trimming, to keep the files small; no field was renamed or invented.

| File | Call | Trimmed |
|---|---|---|
| `find_perspectives.json` | `actor_manager.find_perspectives(search_string="*")`: all 12 | Each element keeps `class`, `elementHeader` and `properties`. `mermaidGraph`, `memberOfCollections` and `scopedElements` are dropped, except the first 4 `scopedElements` (ScopedBy) of the first 3 perspectives. Those related elements keep `elementHeader.{class,guid,type}` and `properties`. |
| `find_governance_zones.json` | `find_governance_definitions("*", page_size=100, graph_query_depth=0, metadata_element_subtypes=["GovernanceZone"])`: the first 6 of 24 | `class`, `elementHeader`, `properties` |
| `get_actor_profiles_by_name_found.json` | `get_actor_profiles_by_name("Erin Overview")` | `class`, `elementHeader`, `properties` |
| `get_all_technology_types_head.json` | `get_all_technology_types()`: the first 3 | none |
| `refusals.json` | the text of three non-answers | none |
| `consumer_index.json` | `egeria_spec_cache.build_consumer_index(client)` under pyegeria 6.2.34, as `erinoverview`, read on laz itself (`https://localhost:9443`) on 2026-11-11. Only reads are made: the RootCollection and its folders' members. No `consumers.json` is written. Consumers are named by display name ("Egeria Advisor"), each with its `qualifiedName`. | none |
| `sso_token_check.json` | `get_my_profile()` with a bearer token, the Portal SSO check (A-I1, 2026-11-11): a valid erinoverview token; a garbage token and a forged, unsigned JWT (both `PyegeriaUnauthorizedException`, 401); a closed port (`PyegeriaConnectionException`) | only the exception text, whitespace-collapsed to 500 characters |

`refusals.json` holds three entries:
- the string pyegeria returns for a name that matches nothing;
- a `find_perspectives` call with a bearer token Egeria rejects (`PyegeriaUnauthorizedException`, 401);
- a client pointed at a closed port (`PyegeriaConnectionException`).

Re-capture with any read-only script in the Portal image on `egeria_network`, against
`https://host.docker.internal:9443`.
