<!-- SPDX-License-Identifier: Apache-2.0 -->
# Dependencies and generation provenance

Project code, specification and tests are original work for this contract.
`tools/build_spec.py` is the project-owned Apache-2.0 generator; its output embeds
the project-owned schema definitions. There are no SDK outputs, downloaded code
templates or imported legacy application implementations. Python uses its standard
library HTTP/SQLite/filesystem interfaces. JSON Schema validation uses jsonschema;
OpenAPI structural validation uses openapi-spec-validator.

Pinned versions are in requirements.lock. The Windows CPython 3.13 wheel lock
records exact artifact hashes. The owning workspace's Phase 07 dependency manifest
records each installed wheel, metadata, declared license and bundled license files.
Third-party package license terms apply to those packages independently of this
project's scope labels. Python and SQLite remain under their upstream terms.

The Apache license text was obtained from
https://www.apache.org/licenses/LICENSE-2.0.txt and the GPL-3.0-only text from the
SPDX license-list-data GPL-3.0-only entry. The stored full texts and their hashes
are included in the workspace provenance record. Naming a license or separating
processes is not a determination about downstream combinations. Application
adapters retain their own source licenses; independently developed implementations
can consume the permissive contract without importing this GPL reference.
