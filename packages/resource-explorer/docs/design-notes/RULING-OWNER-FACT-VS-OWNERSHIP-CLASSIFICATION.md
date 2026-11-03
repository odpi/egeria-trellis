# RULING — The Context owner and Egeria's `Ownership` classification (2026-10-08)

From the design session, answering the open point the designer left for
the architecture session in `RULING-PUBLISH-NOT-CATALOG.md` ("Owner:
Egeria's current default stands; the mapping is open for the architect").
For the owner's word before anything is built.

## The collision

RE writes Egeria's `Ownership` classification (model 0445) on everything
it publishes, with `owner` = the requesting user's id and
`ownerTypeName = UserIdentity` (`egeria_identity.py` §Ownership). RE's own
curate authorization reads it: the owner may accept, reject, promote and
delete without a further grant. The Context **owner** fact is a different
thing: who is accountable for the resource, as declared and signed on the
Context tab. Egeria gives an element one `Ownership`. Publishing the
Context owner there overwrites what the authorization reads; keeping RE's
makes Egeria name the wrong owner.

## The ruling

1. **Egeria's `Ownership` means the accountable owner, and that is the
   Context owner fact.** Egeria readers, the Portal and the context packs
   take `Ownership` as "who is accountable", which is what the fact says.
   RE's "who published it" is provenance, not ownership, and Egeria already
   records it: the element's provenance header names the publishing user,
   and RE's own `additionalProperties` carry `re_published_by` once the
   provenance slice writes it.
2. **RE's curate authorization moves off `Ownership`.** The right to
   accept, reject, promote and delete an element RE published rests on RE's
   provenance (`re_published_by` on the element, with the registry's
   publish-state row as the second witness), never on who Egeria says is
   accountable. A person who published an element keeps every right they
   have today; a person named accountable gains none of RE's rights by
   being named.
3. **Until (2) is built, the owner fact is kept in RE**, exactly as the
   designer's ruling has it: selector disabled, "kept in RE until the owner
   mapping is decided" becomes "kept in RE until RE's authorization reads
   its own provenance", and the manifest row says the same.
4. **When the fact publishes**, `ownerTypeName` follows the fact's kind:
   `UserIdentity` for a person (ownerPropertyName `userId`), `Team` or
   `ActorProfile` for a team, as Egeria's 0445 allows. A free-text owner
   that matches no Egeria actor publishes as `Ownership` with
   `ownerTypeName` absent and the text in `owner`, and the fact row says
   "published as text · no Egeria actor matched".
5. **Elements published before the change keep RE's requesting user in
   `Ownership` until a person publishes the owner fact on them.** No sweep
   rewrites classifications; the fact row on each resource reads "Egeria
   names the publisher as owner · publish the owner fact to correct it".
   RE rolls forward, never undoes.

## The order of work

1. The provenance slice writes `re_published_by` on every element RE
   creates (one line in the properties RE already writes; the adoption-by-
   provenance identity work writes its siblings).
2. The authorization reads `re_published_by` first, falling back to
   `Ownership` only for elements that predate (1), and logs which path it
   took; a committed test proves a named-accountable user is refused the
   publisher's rights.
3. The owner fact's selector turns on; its proof row is the classification
   read back by GUID with Egeria's sentence, like every other fact.

## What this does not change

Zone membership stays as it is: the publishing user's private zone and the
configured publish zones. Access to read an element is a zone question;
the right to curate it is a provenance question; who is accountable is
`Ownership`. Three questions, three answers, none borrowed from another.
