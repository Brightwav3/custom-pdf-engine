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
