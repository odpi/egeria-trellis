"""The `dr_egeria` child for every Dr.Egeria run: `python -m resource_explorer.dr_egeria_child ... <doc.md>`.

It does what pyegeria's `dr_egeria` CLI does (`--process --summary-only`) with a client the RE factory builds, so the
document is applied as the explicit identity the parent chose. The CLI itself is not used: its password has to be on
argv (visible in `ps`). Here the secret arrives in this child's ENVIRONMENT only, exactly one of:

* `RE_DR_EGERIA_CHILD_TOKEN` — a signed-in person's Egeria bearer token (the admin route);
* `RE_DR_EGERIA_CHILD_PASSWORD` — a named user's password (`--as`, or the start-up heal's service account); the
  factory mints the token from it, as the CLI's `create_egeria_bearer_token()` does.

The user, server and url are explicit arguments: there is no default identity here. A missing user, no secret, or both
secrets exits 2.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

TOKEN_ENV = "RE_DR_EGERIA_CHILD_TOKEN"
PASSWORD_ENV = "RE_DR_EGERIA_CHILD_PASSWORD"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dr_egeria_child")
    ap.add_argument("--userid", required=True)
    ap.add_argument("--server", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("input_file")
    args = ap.parse_args(argv)
    token = os.environ.get(TOKEN_ENV, "")
    password = os.environ.get(PASSWORD_ENV, "")
    if not args.userid.strip() or not (token or password):
        print("no Egeria identity set; pass --as or sign in", file=sys.stderr)
        return 2
    if token and password:
        print("both a token and a password were supplied; exactly one is allowed", file=sys.stderr)
        return 2

    from md_processing.dr_egeria import process_md_file_v2
    from pyegeria import EgeriaTech

    from resource_explorer.egeria_clients import DaemonReason, KIND_CALLER, Named, egeria_client
    from resource_explorer.egeria_identity import EgeriaIdentity

    if token:
        identity = EgeriaIdentity(user_id=args.userid, token=token, kind=KIND_CALLER)
        purpose = "Dr.Egeria document run (signed-in person)"
    else:
        identity = Named(args.userid, password, DaemonReason.DR_EGERIA)
        purpose = "Dr.Egeria document run (named account)"
    clients = egeria_client(identity, purpose=purpose, view_server=args.server, platform_url=args.url, shared=False)
    client = clients.of(EgeriaTech)
    asyncio.run(process_md_file_v2(input_file=args.input_file, output_folder="", directive="process",
                                   client=client, parse_summary="none", attribute_logs="info",
                                   usage_level="Basic", summary_only=True, debug=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
