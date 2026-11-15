/* TR-10: when the page refreshes its session JWT. Pure (no DOM, no fetch), so tests run it under plain node.
 *
 * refreshDue(payload, nowSec, lastActiveSec) says what to do with the current JWT:
 *   'none'    no JWT, or it has expired by its own exp: the session is over
 *   'wait'    not near its end yet
 *   'idle'    near its end, but nobody has used the page since it was issued: let it lapse (a quiet tab ends)
 *   'refresh' near its end, and the page was used while it was live: ask POST /api/auth/refresh
 * "Near its end" is the last third of its lifetime, between 30 s and 5 min before exp.
 */
export function refreshDue(p, nowSec, lastActiveSec) {
  if (!p || typeof p.exp !== 'number' || p.exp <= nowSec) return 'none';
  const issued = typeof p.iat === 'number' ? p.iat : nowSec;
  const lead = Math.min(300, Math.max(30, (p.exp - issued) / 3));
  if (p.exp - nowSec > lead) return 'wait';
  return typeof lastActiveSec === 'number' && lastActiveSec >= issued ? 'refresh' : 'idle';
}
