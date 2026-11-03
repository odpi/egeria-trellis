"""A set step's code for the tests: one result per member, one roll-up, reading every member in one typed read."""


def run(inputs):
    slugs = [slug for _, slug in inputs.members]
    snapshots = inputs.ods.row_count_snapshots(slugs)          # a list of slugs: answered as {slug: snapshots}
    rows = [{"scope": slug, "state": "measured", "value": {"snapshots": len(snapshots[slug])},
             "summary": f"{len(snapshots[slug])} snapshots"} for slug in slugs]
    rows.append({"scope": inputs.set_slug, "state": "measured", "value": {"members": len(slugs)},
                 "summary": f"{len(slugs)} members read"})
    return rows
