/** A credential typed for ONE run (parity G2, PI-016), held in browser memory.
 *
 *  Session memory only: a module-level Map, gone when the tab reloads. It is
 *  never written to localStorage, sessionStorage, a cookie, a URL, a log line
 *  or the markup (the dialog sets the field's `.value` as a property, never an
 *  attribute). The server does not keep it either: a run carrying it is not
 *  queued (web/routes/survey_definitions.py).
 *
 *  Kept per database slug, so a credential typed for one database is never
 *  offered for another. */
const remembered = new Map();   // slug -> { user, password }

/** The remembered credential for `slug`, or null. */
export function rememberedCredential(slug) {
  const c = remembered.get(slug);
  return c ? { user: c.user, password: c.password } : null;
}

/** Remember a credential for this tab's lifetime, or forget it when `keep` is false. */
export function setRemembered(slug, credential, keep) {
  if (keep && credential && credential.user && credential.password) {
    remembered.set(slug, { user: credential.user, password: credential.password });
  } else {
    remembered.delete(slug);
  }
}

/** Test seam: forget everything. */
export function forgetAllCredentials() { remembered.clear(); }
