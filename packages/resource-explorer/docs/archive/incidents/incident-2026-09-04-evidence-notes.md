# Evidence notes, captured pre-redeploy, 2026-09-04

Captured immediately before a full Egeria redeploy + repository-store wipe, at dwolfson-b7's
request, to preserve evidence of the 2026-09-04T04:11:33Z OOM-crash/silent-restart before it
became unrecoverable.

## jdbcMaximumPoolSize — answers "did the pool-change announcements become real actions"

Two log lines, same connector startup message, straddling the crash:

- `Thu Sep 03 22:21:40 GMT 2026` (pre-crash): `jdbcMaximumPoolSize=25`
- `Fri Sep 04 04:11:37 GMT 2026` (post-restart, ~4s after the 04:11:33Z crash): `jdbcMaximumPoolSize=25`

**Unchanged, 25 → 25.** Whatever pool-size-change announcements were under discussion did not
result in a different configured value across this restart.

## Files
- `quickstart-egeria-main-inspect-2026-09-04.json` — full `docker inspect` output, 491 lines.
- `quickstart-egeria-main-logs-2026-09-04.txt` — full `docker logs` output, 21067 lines, includes
  the OOM crash and both connector-startup log lines above.
