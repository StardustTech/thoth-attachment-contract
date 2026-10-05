<!-- SPDX-License-Identifier: Apache-2.0 -->
# Neutral Attachment Resource Lifecycle 0.2.0

This specification, openapi.json and schemas.json jointly define v0.2.0. Normative
MUST/SHOULD refer to observable behavior. The reference program is replaceable.

## Ownership and identifiers

A tenant is an isolated resource set, independent of any application workspace.
All identifiers are opaque strings matching the schema; resource IDs are chosen
by the caller, upload IDs by the service. A resource ID is reserved by Put or Begin
and stays reserved after deletion. Resources are immutable byte versions: replace
content by allocating a new resource ID. No global deduplication is observable.
The service owns sessions, directory, committed operation results and byte
lifecycle. Application identities, authorization rules, publication references,
external URLs and document references remain at the caller's adapter.

## Request and authorization

Every operation requires a trusted service Bearer credential bound to the service
audience `neutral-attachment-v0`, tenant allowlist and read/write/admin actions.
An admin can read/write its permitted tenants. This is service authorization;
the adapter MUST perform current end-user/resource/publication authorization for
every request, including retries and parts. The reference uses opaque random
credentials in a private reloadable policy file, with revocation checked per
request. TLS and deployment identity provisioning are deployment responsibilities;
the local reference defaults to loopback, with an explicit private container
interface profile. End-user and presigned URLs remain the adapter's responsibility.

Each tenant request carries X-Tenant-Epoch, a positive integer equal to its active
placement epoch; stale values fail conflict. Provisioning uses an administrator
PUT with matching header/body epoch. An existing tenant cannot be reprovisioned or
reopened. The external coordinator owns mapping and monotonic epoch allocation;
freeze/export support a later verified handoff, not an automatic backend fallback.

Authenticate/authorize before returning results, including idempotent results.
Missing credentials/wrong audience return 401 unauthenticated; action/tenant
denial 403 denied. Missing resource/session/operation returns 404 missing. A
directory denial never returns stored bytes. Calls already authorized and admitted
may finish; revocation applies to later requests. Confidential data already
downloaded cannot be revoked. Read responses use Cache-Control: private, no-store.

## Input, negotiation and limits

Accept only defined /v0 operations, JSON objects matching their schema and raw
binary bodies. Requests with unknown JSON properties fail 400 invalid; consumers
ignore additional optional response properties. Required new semantics need a
new advertised capability/version. X-Contract-Version defaults to 0.2.0; any other
value fails 422 unsupported. X-Required-Capabilities is a comma-separated list
whose members MUST all be advertised, otherwise 422 unsupported. Invalid JSON,
duplicate JSON keys, invalid digest/identifier/epoch and undeclared query parameters
fail 400. JSON control requests use application/json. Zero-length resources are
valid: an empty upload completes with an empty parts list.

Capabilities declare limits. This v0 profile supports resource bytes up to 64 MiB,
parts up to 16 MiB, at most 256 parts, session TTL 1..86400 seconds (default 3600).
Single Put uses up to 16 MiB; larger content uses parts. 413 invalid means limit
exceeded; 415 invalid means media type mismatch. Content-Length is required for
mutating requests; the reference rejects chunked/ambiguous transfer framing.
The complete list MUST be consecutive ascending part numbers from 1, match every
stored part, and match the declared full length and SHA-256. Part SHA-256 is an
integrity value, distinct from an opaque provider ETag. A part is immutable:
same number/same bytes succeeds, same number/different bytes conflicts.

## Operations, concurrency and visibility

Version 0.2.0 advertises `server-measured-content`. Begin may explicitly supply
null byte_length and/or sha256 when the caller cannot know these before upload.
Part digests remain mandatory and verified. Unknown length uses the declared
max_resource_bytes hard cap; Complete computes full length and SHA-256 and fills
them atomically with READY. Non-null expectations are still strictly verified.
UPLOADING and its terminal tombstone may retain null expectations; READY always
contains measured integers/digests. This supports streaming producers without
placing session/byte staging authority in an application adapter.

All mutations require Idempotency-Key, scoped to tenant (not to HTTP route).
The fingerprint includes method, path, canonical JSON or raw bytes, media type and
integrity header. Same key/same input replays the original committed HTTP 200 JSON
result; different input returns 409 conflict. Keys and successful results are
retained for the tenant lifetime, including frozen/closed states. The server
checks authorization and epoch before replay. A replay never resurrects state;
the original result is historical and current state is read separately.
Errors are not successful operation records. GET operations/{key} returns the
committed result. 404 means no committed success was observed, not permission to
retry under a new key. On connection loss or retryable 503, query or retry using
the same key. Retryable unavailable can represent an unknown commit outcome.

The exported request_fingerprint is the lowercase SHA-256 of UTF-8 canonical JSON
with these string-valued fields: `method` (uppercase), `path` (absolute /v0 path,
without query), `body` (canonical JSON text for a JSON request, otherwise SHA-256
of body bytes, including empty bodies), `media_type` (exact Content-Type or empty),
and `sha256` (exact X-Content-SHA256 or empty). Canonical JSON sorts object keys by
Unicode code point, uses no insignificant whitespace, writes integral numbers as
base-10 integers, and emits Unicode directly. It escapes quote/backslash and
control characters using JSON short escapes for backspace/tab/newline/formfeed/
return and lowercase four-digit unicode escapes for other U+0000..001F characters.
Request numeric fields are integral, so floating-point rounding is not part of
this profile. Backfill preserves operation results and their fingerprints as well
as resource/session state. An independent vector test defines the byte encoding.

Each mutation serializes against its resource/session/tenant state. READY and
DELETED directory commits are linearization points. A successful response MUST
survive service-process restart, including operation replay. Put/Begin reserve a
resource atomically; concurrent contenders with different keys produce one winner.
Delete can tombstone UPLOADING or READY, aborting an open upload. IDs stay reserved.
READY bytes are read only if their length and digest match committed metadata;
missing/corrupt storage produces retryable 503 integrity_error, never partial data.

Session states: OPEN -> COMPLETED or ABORTED or EXPIRED. Successful part storage
survives restart. Complete atomically publishes READY and commits its operation
result. Abort/expiry tombstone the reserved resource. Delete/Close wins against
late Complete according to committed order. Duplicate Complete with its original
key replays success even after later Delete, while fresh Complete cannot revive it.
Original retry of Begin can return an older OPEN result; GET upload reports current
state. Callers MUST distinguish operation history from current directory state.

Only READY resources appear in list and ready_count/ready_bytes. List sorts by
resource_id with after/limit keyset pagination; this is a live traversal, not a
cross-request snapshot. Use freeze/export for a stable complete snapshot. Open
upload count is separate; physical byte accounting/quota/billing are out of scope.
Logical delete is immediate; physical cleanup is durable/recoverable and eventual.
It may retain bytes shared by another live resource/session. On restart the
implementation MUST reconcile abandoned bytes against the authoritative directory
and sessions. Temporary failures remain retryable without switching write owner.

## Tenant lifecycle, export and recovery

Tenant states: ACTIVE -> FROZEN or CLOSED; FROZEN -> CLOSED. Freeze serializes with
mutations, closes admission and waits for admitted operations to commit/abort.
Frozen tenant permits reads, export and prior operation replay. Fresh writes fail
409; its open sessions are preserved and TTL aging is paused for export. Close
denies ordinary data reads, tombstones resources and aborts sessions, scheduling
byte cleanup. Authorized services can inspect tenant state/operation history. Close success
means logical unavailability; it does not claim immediate physical erasure.

Export requires admin + FROZEN state. The coordinator keeps lifecycle administration
quiescent throughout export (no concurrent Close or fresh Freeze operation keys).
It returns a stable directory, sessions and
part descriptors, operation history, and the hashes/lengths of reachable bytes.
GET export/blobs/{sha256} exposes only blobs reachable from that tenant export.
The caller downloads all listed blobs, verifies their full hashes and lengths and
preserves tombstones and external mappings before any backfill. Service credentials
are outside the export. Export is not a generic arbitrary JSON business model.

After successful writes, application rollback follows freeze -> export -> verify
-> backfill (including readback verification) -> epoch switch. The coordinator
implements backfill to its selected target; this v0 defines no generic legacy
database importer and performs no automatic reopen or fallback. Full instance
backup includes directory, operations, sessions, bytes and epoch metadata, taken
at a coordinated consistent point. Restore to an isolated instance and test
independent reads and pending-operation recovery before switching routing.

## Reference profile and conformance

Reference storage is real SQLite (FULL synchronization, transactions) and immutable
filesystem blobs, not an in-memory mock. It is single-node, uses bounded in-memory
request buffers and serializes file/catalog transitions. It proves process-crash
recovery on the tested filesystem. S3-provider failures, host/power-loss guarantees,
distributed fencing/HA, AppFlowy adapter authorization and full production limits
require separate integration tests. HTTP server hardening/TLS are outside this
loopback reference profile. Failpoints are private local test controls, not contract
operations or behavior exposed to ordinary clients.

The Apache-2.0 conformance client uses only HTTP and the contract assets. GPL
reference code is launched as a separate process and is never imported by the
conformance suite. A non-AppFlowy Markdown attachment scenario validates the same
resource lifecycle while its path, Git history and document link semantics remain
outside this contract. No SDK is required.
