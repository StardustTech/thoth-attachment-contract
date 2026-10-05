<!-- SPDX-License-Identifier: Apache-2.0 -->
# Neutral Attachment Resource Lifecycle

Version **0.1.0** is a small HTTP contract for an isolated attachment resource set.
It defines immutable byte resources, upload sessions, directory visibility,
operation replay, deletion and coordinated export. Its normative assets are
[OpenAPI](spec/openapi.json), [JSON Schema](spec/schemas.json) and
[semantics](spec/semantics.md). `tools/build_spec.py` deterministically builds the
JSON definitions. Consumers can use ordinary HTTP clients; v0 has no mandatory SDK.

The artifact licenses are explicit: specification, generator and conformance
client/tests use **Apache-2.0**; `reference/` uses **GPL-3.0-only**. The root LICENSE,
NOTICE, reference/LICENSE and LICENSES/ texts define those scopes. The HTTP tests
import the permissive client and schemas, and launch no implementation themselves.
All project code was authored for this contract; application implementation code
and types are outside its dependency graph.

## Local reference

Use Python 3.13 and a private virtual environment. Install `requirements.lock` for
pinned package versions, or `requirements-win-py313.lock` with pip `--require-hashes`
for the tested Windows x64 wheel set. Only jsonschema and its dependency closure
are required by the reference/client. OpenAPI validator dependencies are tooling.
Python's standard-library HTTP server is used for local conformance; the reference
binds 127.0.0.1 and has no public deployment configuration.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install --require-hashes -r requirements-win-py313.lock
.venv/Scripts/python reference/server.py --data <private-data-directory> --policy <private-policy.json> --port 18120
```

The policy JSON maps each securely generated bearer token to
`{"audience":"neutral-attachment-v0","tenants":["example"],"actions":["admin"]}`.
Store it outside the repository, restrict local access, and provision tokens out
of band. Actions are read/write/admin; admin implies read/write. Tenant creation
requires admin. The service reloads the policy for every request. End-user and
application authorization remain the adapter's responsibility.

State lives under the chosen data directory: SQLite catalog plus immutable blobs.
Keep this directory private. Coordinated backups stop requests and the reference
process, copy the complete directory, verify all file digests, then restore into a
fresh directory. This profile validates process crashes on the tested filesystem;
host/power failure, S3 providers, HA and application integration need additional
validation. Buffers are bounded by the 16 MiB part / 64 MiB resource limits; the
single-node catalog serializes transitions. Capacity and hardening are deployment
work, not demonstrated by these tests.

## Independent tests

`python -m conformance.test_contract` reads `NA_CONFIG` (a private JSON path with
`base`, `admin`, `reader`, `outsider`, `wrong_audience`) and writes public results to
`NA_RESULT`. Admin/reader tenant allowlists contain each `test_*` method name;
outsider has a disjoint allowlist, and wrong_audience binds a different service.
The foreign-session case also uses `test_29_foreign_session_and_invalid_unicode-other`.
Use a fresh isolated instance for every run. Recovery assertions in
`conformance/recovery.py` accept an external deployment controller which can
restart/stop the service, arm private test faults, revoke credentials, corrupt
synthetic bytes and perform coordinated restore. These are test controls rather
than protocol operations. The reference enables fault files only when started
with the explicit `--test-controls` argument.

The workspace evaluation harness and captured results are delivered separately
under the owning workspace's `docs/`. For other implementations provide a local
controller with equivalent fault timing. Successful conformance demonstrates the
tested profile and scenarios; the declared production exclusions remain visible.
