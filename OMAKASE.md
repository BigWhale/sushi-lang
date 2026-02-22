# Omakase - Sushi Package Repository

API contract and architecture specification for the Omakase online package repository, the remote backend for the Nori package manager.

## Package Metadata Model

Every package version is uniquely identified by the composite key `(name, version, namespace, platform)`.

### Package Record

| Field         | Type     | Description                                      |
|---------------|----------|--------------------------------------------------|
| `name`        | string   | Package name (validated by `NAME_PATTERN`)        |
| `description` | string   | Short description (max 500 chars)                |
| `author`      | string   | Author name or handle                            |
| `license`     | string   | SPDX license identifier                          |
| `created_at`  | datetime | When the package was first published             |

### Version Record

| Field         | Type     | Description                                          |
|---------------|----------|------------------------------------------------------|
| `version`     | string   | Semver string (validated by `VERSION_PATTERN`)        |
| `namespace`   | string   | `stable` or `testing`                                |
| `platform`    | string   | Target platform: `darwin`, `linux`, `windows`, `any`  |
| `sha256`      | string   | Hex-encoded SHA-256 of the `.nori` archive           |
| `size`        | integer  | Archive size in bytes                                |
| `libraries`   | string[] | Library files included                               |
| `executables` | string[] | Executable files included                            |
| `data`        | string[] | Data files/directories included                      |
| `published_at`| datetime | When this version was published                      |

### Validation Rules

Reuses existing patterns from `sushi_lang/packager/manifest.py`:

- **Name**: `^[a-z][a-z0-9\-]{0,63}$` - lowercase alphanumeric + hyphens, 1-64 chars, starts with a letter
- **Version**: `^\d+\.\d+\.\d+$` - strict `major.minor.patch` semver
- **Namespace**: `stable` (default) or `testing`
- **Platform**: one of `darwin`, `linux`, `windows`, `any`
- **Archive size limit**: 50 MB

## REST API

Base URL: `/api/v1`

All responses use `Content-Type: application/json` unless otherwise noted. Timestamps are ISO 8601 UTC.

### Error Response Format

All error responses follow this structure:

```json
{
  "error": {
    "code": "PACKAGE_NOT_FOUND",
    "message": "Package 'foo-bar' not found"
  }
}
```

Error codes:

| Code                    | HTTP Status | Description                              |
|-------------------------|-------------|------------------------------------------|
| `PACKAGE_NOT_FOUND`     | 404         | Package name does not exist              |
| `VERSION_NOT_FOUND`     | 404         | Specific version does not exist          |
| `DUPLICATE_VERSION`     | 409         | Version already exists for this key      |
| `VALIDATION_ERROR`      | 422         | Invalid name, version, or metadata       |
| `ARCHIVE_TOO_LARGE`    | 413         | Archive exceeds 50 MB limit              |
| `CHECKSUM_MISMATCH`     | 422         | SHA-256 does not match archive content   |
| `MANIFEST_MISMATCH`     | 422         | Archive nori.toml does not match metadata|
| `INTERNAL_ERROR`        | 500         | Unexpected server error                  |

---

### GET /packages

Search and list packages with pagination.

**Query Parameters:**

| Parameter   | Type    | Default  | Description                          |
|-------------|---------|----------|--------------------------------------|
| `q`         | string  | (none)   | Search query (matches name, description) |
| `namespace` | string  | `stable` | Filter by namespace                  |
| `platform`  | string  | (none)   | Filter by platform                   |
| `page`      | integer | 1        | Page number (1-indexed)              |
| `per_page`  | integer | 20       | Results per page (max 100)           |

**Response** `200 OK`:

```json
{
  "packages": [
    {
      "name": "sushi-utils",
      "description": "Common utilities for Sushi programs",
      "author": "whale",
      "latest_version": "1.2.0",
      "updated_at": "2026-02-20T15:30:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 1
  }
}
```

---

### GET /packages/{name}

Package detail with all versions.

**Query Parameters:**

| Parameter   | Type   | Default  | Description             |
|-------------|--------|----------|-------------------------|
| `namespace` | string | `stable` | Filter versions by namespace |

**Response** `200 OK`:

```json
{
  "name": "sushi-utils",
  "description": "Common utilities for Sushi programs",
  "author": "whale",
  "license": "MIT",
  "created_at": "2026-01-15T10:00:00Z",
  "versions": [
    {
      "version": "1.2.0",
      "namespace": "stable",
      "platforms": ["darwin", "linux"],
      "published_at": "2026-02-20T15:30:00Z"
    },
    {
      "version": "1.1.0",
      "namespace": "stable",
      "platforms": ["darwin", "linux"],
      "published_at": "2026-02-01T12:00:00Z"
    }
  ]
}
```

Versions are sorted newest first (descending semver order).

---

### GET /packages/{name}/{version}/metadata

Version-specific metadata for a single platform.

**Query Parameters:**

| Parameter   | Type   | Default            | Description               |
|-------------|--------|--------------------|---------------------------|
| `namespace` | string | `stable`           | Namespace to query        |
| `platform`  | string | (caller's platform)| Target platform           |

**Response** `200 OK`:

```json
{
  "name": "sushi-utils",
  "version": "1.2.0",
  "namespace": "stable",
  "platform": "darwin",
  "description": "Common utilities for Sushi programs",
  "author": "whale",
  "license": "MIT",
  "sha256": "a1b2c3d4e5f6...",
  "size": 24576,
  "libraries": ["libsushi_utils.slib"],
  "executables": [],
  "data": [],
  "published_at": "2026-02-20T15:30:00Z"
}
```

---

### GET /packages/{name}/{version}/download

Download the `.nori` archive.

**Query Parameters:**

| Parameter   | Type   | Default            | Description               |
|-------------|--------|--------------------|---------------------------|
| `namespace` | string | `stable`           | Namespace to download from|
| `platform`  | string | (caller's platform)| Target platform           |

**Response** `200 OK`:
- `Content-Type: application/octet-stream`
- `Content-Disposition: attachment; filename="{name}-{version}.nori"`
- `X-Sha256: {hex-encoded hash}`
- Body: raw `.nori` archive bytes (gzipped tarball)

---

### POST /packages/{name}/{version}/publish

Publish a new package version. Atomic operation: metadata and archive are committed together or not at all.

**Request**: `Content-Type: multipart/form-data`

| Part       | Type               | Description                     |
|------------|--------------------|---------------------------------|
| `metadata` | `application/json` | Version metadata (see below)    |
| `archive`  | `application/octet-stream` | The `.nori` archive file |

**Metadata JSON:**

```json
{
  "namespace": "stable",
  "platform": "darwin",
  "description": "Common utilities for Sushi programs",
  "author": "whale",
  "license": "MIT",
  "sha256": "a1b2c3d4e5f6..."
}
```

The `sha256` field is the hex-encoded SHA-256 hash of the archive, computed by the client before upload.

**Server-side validation (in order):**

1. URL `{name}` and `{version}` match `NAME_PATTERN` and `VERSION_PATTERN`
2. `namespace` is `stable` or `testing`
3. `platform` is one of `darwin`, `linux`, `windows`, `any`
4. Archive size does not exceed 50 MB
5. SHA-256 of received archive matches the `sha256` field in metadata
6. Archive is a valid gzipped tarball containing a `nori.toml`
7. `nori.toml` inside archive: `name` matches URL `{name}`, `version` matches URL `{version}`
8. No existing record for `(name, version, namespace, platform)` composite key

If any check fails, the entire publish is rejected. Nothing is stored.

**Response** `201 Created`:

```json
{
  "name": "sushi-utils",
  "version": "1.2.0",
  "namespace": "stable",
  "platform": "darwin",
  "published_at": "2026-02-20T15:30:00Z"
}
```

## Namespace System

Two namespaces: `stable` and `testing`.

- **`stable`** (default) - production-ready packages. All CLI commands default to this namespace.
- **`testing`** - pre-release or experimental packages. Must be explicitly requested.

Namespaces are independent. The same `(name, version, platform)` tuple can exist in both `stable` and `testing` as separate records. There is no promotion mechanism; to move a package from `testing` to `stable`, publish it again to `stable`.

CLI flag: `--namespace testing` on `search`, `install`, and `publish` commands.

## Versioning Rules

- **Immutable**: once published, a version cannot be overwritten or modified. The `(name, version, namespace, platform)` key is permanent.
- **Semver ordering**: versions are sorted by `major.minor.patch` numeric comparison, not lexicographic.
- **Latest resolution**: when no version is specified in `nori install`, the server resolves to the highest semver in the requested namespace and platform.
- **Platform fallback**: if no platform-specific build exists, the client checks for `platform=any` as a fallback.

## Service Architecture

Logical components (deployment details are out of scope):

```
                  +------------------+
                  |    CDN / Edge    |
                  | (archive cache)  |
                  +--------+---------+
                           |
                  +--------+---------+
                  |   API Service    |
                  | (REST endpoints) |
                  +--+------+-----+-+
                     |      |     |
           +---------+  +--+--+  +----------+
           |            |     |             |
   +-------+------+ +--+---+ +------+------+
   | Metadata Store| |Search| |Archive Store|
   | (pkg records) | |Index | | (.nori files)|
   +---------------+ +------+ +-------------+
```

- **API Service**: stateless HTTP server handling all REST endpoints. Validates requests, orchestrates storage, returns responses.
- **Metadata Store**: persistent storage for package and version records. Indexed by `(name, version, namespace, platform)`.
- **Archive Store**: blob storage for `.nori` archive files. Keyed by `{name}/{version}/{namespace}/{platform}/{name}-{version}.nori`.
- **Search Index**: text search over package names and descriptions. Updated on publish.
- **CDN**: optional cache layer for archive downloads. Keyed by the same path as archive store.

## CLI Integration

### nori search

New command. Queries `GET /packages`.

```
nori search <query> [--namespace testing] [--platform linux]
```

Displays results in a table: name, latest version, description (truncated).

### nori install (remote)

Extends the existing install command. When the package argument is not a local path, the client queries the remote repository.

```
nori install <package-name> [--namespace testing] [--version 1.2.0]
```

Flow:
1. `GET /packages/{name}/{version}/metadata` (or latest if no version specified)
2. `GET /packages/{name}/{version}/download`
3. Verify SHA-256 of downloaded archive matches `X-Sha256` header
4. Save to `~/.sushi/cache/{name}-{version}.nori`
5. Install via existing `PackageInstaller.install_from_archive()`

### nori publish

New command. Publishes to `POST /packages/{name}/{version}/publish`.

```
nori publish [--namespace testing]
```

Flow:
1. Load and validate local `nori.toml`
2. Run `nori build` to produce the `.nori` archive
3. Compute SHA-256 of archive
4. Detect current platform
5. `POST /packages/{name}/{version}/publish` with multipart body
6. Report success or server-side validation error

## Edge Cases

- **Duplicate publish**: rejected with `409 DUPLICATE_VERSION`. The client should inform the user that this exact `(name, version, namespace, platform)` already exists.
- **Platform-specific packages**: a library compiled on macOS produces a `darwin` archive. The same version must be separately published for `linux`. Platform-agnostic packages (pure data, scripts) use `platform=any`.
- **Archive size limit**: 50 MB hard limit enforced server-side. The client should also check before uploading and warn the user.
- **Concurrent publishes**: the server must ensure atomicity per composite key. Two simultaneous publishes of the same key: one succeeds, one gets `409`.
- **Partial upload failure**: if the connection drops mid-upload, nothing is committed. The client can retry the full publish.
- **Malformed archives**: rejected at validation step 6 with `VALIDATION_ERROR`.
- **Name squatting**: no reservation mechanism. First to publish a name owns it (until auth/ownership is implemented).

## Future Considerations (Out of Scope)

These features are explicitly not part of the initial implementation:

- **Authentication and authorization**: API keys, token-based auth, package ownership
- **Dependency resolution**: transitive dependency tracking and resolution
- **Download counts and statistics**: popularity metrics
- **Version yanking**: marking a version as deprecated/withdrawn without deletion
- **Signed packages**: cryptographic signatures for archive integrity beyond SHA-256
- **Rate limiting**: per-client request throttling
- **Webhooks**: notifications on publish events
