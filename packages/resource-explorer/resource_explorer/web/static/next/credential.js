/** A database's stored credential state, as the REGISTRY reports it.
 *
 *  `credential_status` ("ok" | "none" | "unreadable") and `credential_reason`
 *  come from the response (DatabaseSummary / DiscoveredDatabase); nothing here
 *  infers it. "unreadable" means a credential is stored but cannot be
 *  decrypted (rotated or mismatched key): the row still lists with every other
 *  field, and anything that would connect with it is disabled with this reason.
 *  The wording is fixed and carries no secret and no exception text.
 */
export const CREDENTIAL_UNREADABLE_TEXT = 'credential unreadable · re-enter credentials';

export const isCredentialUnreadable = (row) => !!row && row.credential_status === 'unreadable';

/** The marker, as a short span. `cls` adds layout classes. */
export function credentialMarkHtml(row, cls = '') {
  if (!isCredentialUnreadable(row)) return '';
  return `<span class="text-state-warn ${cls}" data-credential="unreadable">${CREDENTIAL_UNREADABLE_TEXT}</span>`;
}
