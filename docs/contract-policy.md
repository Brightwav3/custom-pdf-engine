# Contract compatibility policy

This is FreeDF's contract. The product is FreeDF and the distribution is
`freedf`, but every identifier this document governs — the Python package, the
console script, command names, operation kinds, error codes — is spelled
`pdfengine` or was fixed at v1, and this policy is exactly what stops those from
being renamed on a whim.

The external contract is versioned by the `apiVersion` field, currently `"v1"`.
Every public surface — the Python façade, the JSONL CLI, and the HTTP service —
speaks the same version, because all three route through one dispatcher. There
is no way for one transport to drift ahead of another, which is why a single
manifest can freeze all three at once.

## What may change inside a version

Additively, without a version bump:

- new commands
- new operation kinds
- new fields on a response
- new capability entries
- new keys inside an error's `details`
- new artifact kinds

## What forces a new version

- removing a response field, command, or operation kind
- changing the type of an existing field
- changing an existing error `code`
- tightening validation so a payload that used to be accepted is rejected

## Exceptions

The rules above are the rules. They have been overruled twice, both times in
v0.2, both times deliberately and on the record:

- **The closed-session error code.** A command naming a closed session used to
  fail with `session_not_found`; it now fails with `session_invalid_state`.
  That is a changed error code for an existing situation.
- **`undo` and `redo` rejecting unknown fields.** They used to accept them
  silently. That is tightening validation so a payload that used to be accepted
  is rejected.

The bar that justified both is the same: each corrected an **inconsistency or
an oversight that no caller could reasonably have depended on**, not an
*intended* behaviour. `session_not_found` meaning "closed" conflated two
situations the engine could always tell apart; every command except `undo` and
`redo` already rejected unknown fields. Changing behaviour a caller could
sensibly have built on still forces a version bump, and always will — that is
the whole point of the distinction, and "it was a mistake" is a claim that has
to survive being written down.

So it must be written down. Any future exception has to be recorded in
`docs/CONTRACT-CHANGELOG.md` under **"Changed (behaviour)"**, with the reasoning
for why the old behaviour was an oversight rather than a design. An exception
that is not in the changelog is not an exception; it is a breaking change.

Exceptions are expected to stay rare, and their number is itself a signal. Two
in one release is already the ceiling of what "we got it wrong" can honestly
explain. If they start accumulating, the correct response is to stop granting
them and bump the version.

## The asymmetry that makes this work

```
Unknown response fields MUST be ignored by clients.
Unknown request fields remain rejected.
```

Responses may grow, so clients must tolerate fields they do not recognize.
Requests may not: every command and every operation rejects unknown fields, so a
typo or a payload built for a newer engine fails loudly instead of being
silently half-applied. A caller that guesses wrong finds out at the boundary,
not three operations into a batch.

## Capability answers, not just capability names

Two fields in the `capabilities` response answer different questions and are not
interchangeable. `filters.decodable` is a flat list of filter names —
`SUPPORTED_FILTERS` is `("FlateDecode",)` — and a flat list of names cannot
express the truth, because Flate *with a predictor* is not decodable by this
version even though "FlateDecode" appears in the list. `document.textContent` is
the field that tells the truth for a specific open document: it surveys the
actual streams and reports `blocked` with the offending filters when any of them
cannot be read. Ask `filters` what the engine knows about in general; ask
`document.textContent` what it can do with the file in your hand.

## How this is enforced

`tests/contracts/golden/v1-surface.json` freezes every command, operation kind,
error code, artifact kind, capability state, and schema name. A test fails if
any of them disappears, and fails with instructions if any of them appears
without the manifest and changelog being updated in the same commit.

The second half is deliberate, and it is not a nuisance to be softened. Growth
is allowed by this policy, but growth that nobody wrote down is how a contract
stops being a contract. Failing on addition is what puts the changelog entry in
the same commit as the change.
