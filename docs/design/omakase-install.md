# Omakase remote install: verification design

Status: DESIGN (no download implementation exists yet — `nori install <pkg>` without
`from <path>` prints "not yet implemented" and exits 1). This document fixes the
verification contract *before* Omakase serves real packages, while there are zero
compatibility constraints. The consume side must be born verifying; retrofitting
verification onto a live ecosystem is how ecosystems get supply-chain incidents.

## What already exists

- **Publish side**: `nori publish` uploads the archive with an `X-Sha256` header
  computed over the exact bytes sent (`api_upload_multipart`). The server is
  expected to reject a mismatch, so the stored artifact's digest is known-good
  relative to what the author sent.
- **Extraction**: `PackageInstaller` uses `tarfile` with `filter="data"` — no
  absolute paths, no `..` traversal, no device nodes, no symlink escapes. Keep it.
- **Credentials**: `~/.sushi/credentials.toml`, `0600`, token per repository.

## The contract (phase 1 — integrity, server-trusted)

1. **Resolve**: `GET /packages/{namespace}/{name}/{version}` returns metadata:
   `{ "archive_url": ..., "sha256": <hex digest of the archive bytes>, "size": N }`.
   The digest is the one the server verified at publish time (`X-Sha256`).
2. **Download** over TLS (default verification stays ON — it already is) to a
   temporary file in the store, never to the final path.
3. **Verify BEFORE extraction**: stream-hash the downloaded bytes; compare to the
   metadata `sha256` with `hmac.compare_digest`. Mismatch = delete the temp file,
   report `checksum mismatch for <pkg>@<version>` and the two digests, exit 1.
   Nothing from an unverified archive is ever opened by `tarfile`, not even the
   manifest.
4. **Extract** with `filter="data"` into `store_package_dir(name, version)`,
   then stamp `[install]` with `source = "<repository>"` and the verified digest:
   `sha256 = "<hex>"`. The stamp makes a later `nori verify` (re-hash the store
   against the manifest) possible.
5. **Pin**: a project installing a dependency records `{name, version, sha256}`
   in its own manifest. A later install of the same pin verifies against the
   *pinned* digest, not whatever the server currently claims — this is the
   lockfile property: a server compromise after first install cannot silently
   swap bytes for existing consumers.

Threats covered: transit corruption, CDN/cache tampering, server-side artifact
swap after first install (via pins). Threat NOT covered: a server compromised
*before* the first install, or a malicious author — that needs signatures.

## Phase 2 (sketch — deferred until Omakase supports key registration)

Author-held Ed25519 keys; `nori publish` signs the archive digest (detached,
minisign/ssh-sig style); the server stores and serves the signature and the
author's registered public key; `nori install` verifies digest → signature →
author identity, and records the author key in the pin. Key rotation and
revocation go through the account, TOFU on first install of a namespace.
Out of scope until the registry has stable accounts; nothing in phase 1's
contract has to change to add it (the pin gains a `signed_by` field).

## Non-goals

- No unsigned/unverified install path, ever — not even with a `--force` flag.
- No checksum algorithms other than SHA-256 (no negotiation surface).
- No auto-execution of anything from a package at install time (no postinstall
  hooks; `bin/` symlinks are created but nothing is run).
