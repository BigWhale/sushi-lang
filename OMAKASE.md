# Omakase - Sushi Package Repository

API contract and architecture specification for the Omakase online package repository, the remote backend for the Nori package manager.

## Package Metadata Model

Every package version is uniquely identified by the composite key `(name, version, namespace, platform)`.

### Package Record

| Field         | Type     | Description                                      |
|---------------|----------|--------------------------------------------------|
| `name`        | string   | Package name (validated by `NAME_PATTERN`)       |
| `description` | string   | Short description (max 500 chars)                |
| `author`      | string   | Author name or handle                            |
| `license`     | string   | SPDX license identifier                          |
| `created_at`  | datetime | When the package was first published             |

### Version Record

| Field         | Type     | Description                                          |
|---------------|----------|------------------------------------------------------|
| `version`     | string   | Semver string (validated by `VERSION_PATTERN`)       |
| `namespace`   | string   | `stable` or `testing`                                |
| `platform`    | string   | Target platform: `darwin`, `linux`, `windows`, `any` |
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

## User Model

| Field            | Type     | Description                                                                     |
|------------------|----------|---------------------------------------------------------------------------------|
| `username`       | string   | Unique identifier (validated by `NAME_PATTERN`)                                 |
| `email`          | string   | Unique email address                                                            |
| `password`       | string   | bcrypt hash (never returned in API responses)                                   |
| `is_superadmin`  | boolean  | Grants full administrative privileges                                           |
| `is_active`      | boolean  | Account status (default: `true`). Inactive users cannot log in or authenticate. |
| `created_at`     | datetime | Account creation timestamp                                                      |
| `updated_at`     | datetime | Last modification timestamp                                                     |

- Usernames follow the same `NAME_PATTERN` as package names: lowercase alphanumeric + hyphens, 1-64 chars, starts with a letter.
- Email is visible only to the user themselves and superadmins.
- Usernames, group names, and package names share a single flat namespace (no collisions allowed).
- The first registered user automatically becomes a superadmin.
- Inactive users (`is_active: false`) are rejected at authentication: login returns `FORBIDDEN`, and existing sessions/tokens stop working.

## Group Model

| Field       | Type     | Description                                        |
|-------------|----------|----------------------------------------------------|
| `name`      | string   | Unique identifier (validated by `NAME_PATTERN`)    |
| `owner`     | string   | Username of the group owner                        |
| `members`   | string[] | List of member usernames (always includes `owner`) |
| `created_at`| datetime | Group creation timestamp                           |
| `updated_at`| datetime | Last modification timestamp                        |

- Group names follow the same `NAME_PATTERN` as package names.
- Only the group owner (or a superadmin) can add/remove members.
- The owner is always a member and cannot be removed from the members list.
- Groups occupy the same flat namespace as users and packages.

## API Token Model

| Field          | Type              | Description                                         |
|----------------|-------------------|-----------------------------------------------------|
| `id`           | UUID              | Unique token identifier                             |
| `name`         | string            | Human-readable label (e.g. "CI deploy")             |
| `token_prefix` | string            | First 8 characters of the token (for display)       |
| `token_hash`   | string            | SHA-256 hash of the full token (stored server-side) |
| `user`         | string            | Username that owns this token                       |
| `created_at`   | datetime          | Token creation timestamp                            |
| `last_used_at` | datetime          | Last time the token was used for authentication     |
| `expires_at`   | datetime or null  | Expiration timestamp (null = never expires)         |

- Token format: `nori_` prefix followed by 48 cryptographically random URL-safe characters (e.g. `nori_a3Bf9x...`).
- The full token is returned only once at creation. The server stores only `token_hash`.
- Maximum 10 active tokens per user.
- Tokens authenticate via `Authorization: Bearer <token>` header.

## Package Ownership

| Field          | Type   | Description                         |
|----------------|--------|-------------------------------------|
| `package_name` | string | The package this record applies to  |
| `owner_kind`   | string | `user` or `group`                   |
| `owner_name`   | string | Username or group name              |

- First publish of a new package name claims ownership for the publishing user.
- Group ownership: any group member can publish new versions of the package.
- Ownership transfer is available via a dedicated endpoint (owner or superadmin only).
- Ownership is per-package, not per-version.

## REST API

Base URL: `/api/v1`

All responses use `Content-Type: application/json` unless otherwise noted. Timestamps are ISO 8601 UTC.

Authenticated endpoints require either:
- `Authorization: Bearer <token>` header (API token), or
- A valid session cookie (from login)

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

| Code                      | HTTP Status | Description                                                   |
|---------------------------|-------------|---------------------------------------------------------------|
| `PACKAGE_NOT_FOUND`       | 404         | Package name does not exist                                   |
| `VERSION_NOT_FOUND`       | 404         | Specific version does not exist                               |
| `USER_NOT_FOUND`          | 404         | Username does not exist                                       |
| `GROUP_NOT_FOUND`         | 404         | Group name does not exist                                     |
| `TOKEN_NOT_FOUND`         | 404         | Token ID does not exist                                       |
| `MEMBER_NOT_FOUND`        | 404         | Username is not a member of the group                         |
| `DUPLICATE_VERSION`       | 409         | Version already exists for this key                           |
| `DUPLICATE_USER`          | 409         | Username or email already registered                          |
| `DUPLICATE_GROUP`         | 409         | Group name already taken                                      |
| `NAME_CONFLICT`           | 409         | Name already exists in another namespace (user/group/package) |
| `UNAUTHORIZED`            | 401         | Missing or invalid authentication                             |
| `INVALID_CREDENTIALS`     | 401         | Wrong username or password                                    |
| `FORBIDDEN`               | 403         | Authenticated but insufficient permissions                    |
| `VALIDATION_ERROR`        | 422         | Invalid name, version, or metadata                            |
| `OWNER_CANNOT_BE_REMOVED` | 422         | Cannot remove the group owner from members                    |
| `OWNERSHIP_REQUIRED`      | 422         | Group cannot be deleted while it owns packages                |
| `ARCHIVE_TOO_LARGE`       | 413         | Archive exceeds 50 MB limit                                   |
| `CHECKSUM_MISMATCH`       | 422         | SHA-256 does not match archive content                        |
| `MANIFEST_MISMATCH`       | 422         | Archive nori.toml does not match metadata                     |
| `TOKEN_LIMIT_REACHED`     | 429         | User already has 10 active tokens                             |
| `INTERNAL_ERROR`          | 500         | Unexpected server error                                       |

---

### Authentication

#### POST /auth/register

Create a new user account. Public endpoint. The first registered user automatically becomes a superadmin.

**Request**:

```json
{
  "username": "arthur",
  "email": "whale@example.com",
  "password": "trillian123"
}
```

**Validation:**

1. `username` matches `NAME_PATTERN` (or `VALIDATION_ERROR`). If the input contains uppercase letters, the error message specifically states "Username must be lowercase".
2. `username` does not collide with any existing user, group, or package name (`DUPLICATE_USER` if username taken, `NAME_CONFLICT` if name exists as group or package)
3. `email` is a valid email format and not already registered (`DUPLICATE_USER`)
4. `password` is at least 8 characters (or `VALIDATION_ERROR`)

**Response** `201 Created`:

```json
{
  "username": "whale",
  "created_at": "2026-02-23T10:00:00Z"
}
```

---

#### POST /auth/login

Authenticate a user. Returns an API token if `token_name` is provided, otherwise sets a session cookie.

**Request**:

```json
{
  "username": "whale",
  "password": "s3cret",
  "token_name": "my-laptop"
}
```

The `token_name` field is optional. When present, the response includes a newly created API token instead of setting a session cookie. Returns `INVALID_CREDENTIALS` if username or password is wrong. Returns `FORBIDDEN` if the user account is inactive. Returns `TOKEN_LIMIT_REACHED` if the user already has 10 active tokens and `token_name` is provided.

**Response** `200 OK` (with `token_name`):

```json
{
  "token": "nori_a3Bf9xK2mP7qR4sT8uV1wY6zA0bC5dE3fG9hJ2kL4nN7pQ",
  "token_id": "550e8400-e29b-41d4-a716-446655440000",
  "expires_at": null
}
```

The `token` value is shown only in this response. Store it securely.

**Response** `200 OK` (without `token_name`):

Sets `Set-Cookie` header with a session cookie. Response body:

```json
{
  "username": "whale"
}
```

---

#### POST /auth/logout

Invalidate the current session. Authenticated endpoint.

**Response** `204 No Content`

---

### Token Management

#### GET /tokens

List the authenticated user's API tokens. Does not return token values.

**Response** `200 OK`:

```json
{
  "tokens": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "my-laptop",
      "token_prefix": "nori_a3B",
      "created_at": "2026-02-23T10:00:00Z",
      "last_used_at": "2026-02-23T14:30:00Z",
      "expires_at": null
    }
  ]
}
```

---

#### DELETE /tokens/{id}

Revoke a specific API token. Authenticated endpoint. Users can only revoke their own tokens. Returns `TOKEN_NOT_FOUND` if the token ID does not exist or belongs to another user.

**Response** `204 No Content`

---

### User Management

#### GET /users/me

Get the authenticated user's profile, including email.

**Response** `200 OK`:

```json
{
  "username": "whale",
  "email": "whale@example.com",
  "is_superadmin": false,
  "packages": ["sushi-utils", "sushi-json"],
  "created_at": "2026-02-23T10:00:00Z"
}
```

---

#### GET /users/{username}

Get a user's public profile. Public endpoint. Returns `USER_NOT_FOUND` if the username does not exist.

**Response** `200 OK`:

```json
{
  "username": "whale",
  "packages": ["sushi-utils", "sushi-json"],
  "created_at": "2026-02-23T10:00:00Z"
}
```

---

### Group Management

#### POST /groups

Create a new group. Authenticated endpoint. The authenticated user becomes the group owner.

**Request**:

```json
{
  "name": "sushi-team"
}
```

**Validation:**

1. `name` matches `NAME_PATTERN` (or `VALIDATION_ERROR`)
2. `name` does not collide with any existing user, group, or package name (`DUPLICATE_GROUP` if group name taken, `NAME_CONFLICT` if name exists as user or package)

**Response** `201 Created`:

```json
{
  "name": "sushi-team",
  "owner": "whale",
  "members": ["whale"],
  "created_at": "2026-02-23T10:00:00Z"
}
```

---

#### GET /groups/{name}

Get a group's profile with members and owned packages. Public endpoint. Returns `GROUP_NOT_FOUND` if the group does not exist.

**Response** `200 OK`:

```json
{
  "name": "sushi-team",
  "owner": "whale",
  "members": ["whale", "dolphin"],
  "packages": ["sushi-core"],
  "created_at": "2026-02-23T10:00:00Z"
}
```

---

#### PUT /groups/{name}/members/{username}

Add a member to a group. Requires group owner or superadmin.

**Validation:**

1. `{username}` is an existing user (or `USER_NOT_FOUND`)
2. `{username}` is not already a member (or `VALIDATION_ERROR`)

**Response** `200 OK`:

```json
{
  "name": "sushi-team",
  "members": ["whale", "dolphin"]
}
```

---

#### DELETE /groups/{name}/members/{username}

Remove a member from a group. Requires group owner or superadmin.

**Validation:**

1. `{username}` is a current member (or `MEMBER_NOT_FOUND`)
2. `{username}` is not the group owner (or `OWNER_CANNOT_BE_REMOVED`)

**Response** `200 OK`:

```json
{
  "name": "sushi-team",
  "members": ["whale"]
}
```

---

#### DELETE /groups/{name}

Delete a group. Requires group owner or superadmin. The group must not own any packages (returns `OWNERSHIP_REQUIRED` otherwise).

**Response** `204 No Content`

---

### Package Ownership

#### GET /packages/{name}/owner

Get the owner of a package. Public endpoint.

**Response** `200 OK`:

```json
{
  "package_name": "sushi-utils",
  "owner_kind": "user",
  "owner_name": "whale"
}
```

---

#### PUT /packages/{name}/owner

Transfer package ownership. Requires current owner or superadmin.

**Request**:

```json
{
  "owner_kind": "group",
  "owner_name": "sushi-team"
}
```

**Validation:**

1. `owner_kind` is `user` or `group`
2. `owner_name` references an existing user or group
3. Requester is the current package owner or a superadmin

**Response** `200 OK`:

```json
{
  "package_name": "sushi-utils",
  "owner_kind": "group",
  "owner_name": "sushi-team"
}
```

---

### Download Statistics

#### GET /packages/{name}/stats

Download counts per version and platform. Public endpoint.

**Query Parameters:**

| Parameter | Type    | Default | Description                              |
|-----------|---------|---------|------------------------------------------|
| `days`    | integer | 30      | Time window in days (max 365)            |

**Response** `200 OK`:

```json
{
  "package_name": "sushi-utils",
  "total_downloads": 1542,
  "period_days": 30,
  "versions": [
    {
      "version": "1.2.0",
      "downloads": 1023,
      "platforms": {
        "darwin": 612,
        "linux": 411
      }
    },
    {
      "version": "1.1.0",
      "downloads": 519,
      "platforms": {
        "darwin": 300,
        "linux": 219
      }
    }
  ]
}
```

---

### Package Endpoints

#### GET /packages

Search and list packages with pagination.

**Query Parameters:**

| Parameter   | Type    | Default  | Description                              |
|-------------|---------|----------|------------------------------------------|
| `q`         | string  | (none)   | Search query (matches name, description) |
| `namespace` | string  | `stable` | Filter by namespace                      |
| `platform`  | string  | (none)   | Filter by platform                       |
| `page`      | integer | 1        | Page number (1-indexed)                  |
| `per_page`  | integer | 20       | Results per page (max 100)               |

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

#### GET /packages/{name}

Package detail with all versions.

**Query Parameters:**

| Parameter   | Type   | Default  | Description                  |
|-------------|--------|----------|------------------------------|
| `namespace` | string | `stable` | Filter versions by namespace |

**Response** `200 OK`:

```json
{
  "name": "sushi-utils",
  "description": "Common utilities for Sushi programs",
  "author": "whale",
  "license": "MIT",
  "created_at": "2026-01-15T10:00:00Z",
  "owner": {
    "kind": "user",
    "name": "whale"
  },
  "total_downloads": 1542,
  "versions": [
    {
      "version": "1.2.0",
      "namespace": "stable",
      "platforms": ["darwin", "linux"],
      "downloads": 1023,
      "published_at": "2026-02-20T15:30:00Z"
    },
    {
      "version": "1.1.0",
      "namespace": "stable",
      "platforms": ["darwin", "linux"],
      "downloads": 519,
      "published_at": "2026-02-01T12:00:00Z"
    }
  ]
}
```

Versions are sorted newest first (descending semver order).

---

#### GET /packages/{name}/{version}/metadata

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

#### GET /packages/{name}/{version}/download

Download the `.nori` archive.

**Query Parameters:**

| Parameter   | Type   | Default            | Description                 |
|-------------|--------|--------------------|-----------------------------|
| `namespace` | string | `stable`           | Namespace to download from  |
| `platform`  | string | (caller's platform)| Target platform             |

**Response** `200 OK`:
- `Content-Type: application/octet-stream`
- `Content-Disposition: attachment; filename="{name}-{version}.nori"`
- `X-Sha256: {hex-encoded hash}`
- Body: raw `.nori` archive bytes (gzipped tarball)

Each successful download increments the download counter for this `(name, version, namespace, platform)` record.

---

#### POST /packages/{name}/{version}/publish

Publish a new package version. Atomic operation: metadata and archive are committed together or not at all.

**Authentication required.** The authenticated user must be:
- The package owner (for existing packages), or
- A member of the owning group (for group-owned packages), or
- A superadmin

For new package names (first publish), any authenticated user can publish. The publishing user automatically becomes the package owner.

**Request**: `Content-Type: multipart/form-data`

| Part       | Type                        | Description                      |
|------------|-----------------------------|----------------------------------|
| `metadata` | `application/json`          | Version metadata (see below)     |
| `archive`  | `application/octet-stream`  | The `.nori` archive file         |

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

0. Request carries valid authentication (returns `UNAUTHORIZED` if missing, `FORBIDDEN` if insufficient)
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

---

### Administration

All administration endpoints require superadmin privileges. Returns `UNAUTHORIZED` if not authenticated or `FORBIDDEN` if authenticated but not a superadmin.

Superadmins cannot deactivate, demote, or delete their own account (returns `VALIDATION_ERROR`).

#### GET /admin/users

List all users with optional search and filtering.

**Query Parameters:**

| Parameter   | Type    | Default  | Description                                  |
|-------------|---------|----------|----------------------------------------------|
| `q`         | string  | (none)   | Search query (matches username or email)     |
| `is_active` | boolean | (none)   | Filter by account status                     |
| `page`      | integer | 1        | Page number (1-indexed)                      |
| `per_page`  | integer | 20       | Results per page (max 100)                   |

**Response** `200 OK`:

```json
{
  "users": [
    {
      "username": "arthur",
      "email": "arthur@example.com",
      "is_superadmin": true,
      "is_active": true,
      "created_at": "2026-02-23T10:00:00Z",
      "updated_at": "2026-02-23T10:00:00Z"
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

#### GET /admin/users/{username}

Get detailed administrative view of a user account.

**Response** `200 OK`:

```json
{
  "username": "arthur",
  "email": "arthur@example.com",
  "is_superadmin": true,
  "is_active": true,
  "token_count": 2,
  "group_count": 1,
  "package_count": 5,
  "created_at": "2026-02-23T10:00:00Z",
  "updated_at": "2026-02-23T10:00:00Z"
}
```

---

#### PATCH /admin/users/{username}

Update a user's administrative flags. Supports partial updates.

**Request**:

```json
{
  "is_active": false,
  "is_superadmin": true
}
```

Both fields are optional. Only provided fields are updated.

**Self-protection rules:**
- A superadmin cannot set `is_active: false` on their own account (`VALIDATION_ERROR`)
- A superadmin cannot set `is_superadmin: false` on their own account (`VALIDATION_ERROR`)

**Response** `200 OK`:

```json
{
  "username": "whale",
  "email": "whale@example.com",
  "is_superadmin": true,
  "is_active": true,
  "updated_at": "2026-02-23T12:00:00Z"
}
```

---

#### DELETE /admin/users/{username}

Delete a user account. Removes the user from the name registry, deletes all associated API tokens and group memberships.

A superadmin cannot delete their own account (`VALIDATION_ERROR`).

**Note:** Packages owned by the deleted user become unowned. Transfer package ownership before deletion if needed.

**Response** `204 No Content`

---

## Authorization Matrix

Every endpoint mapped to the required access level.

| Endpoint                                       | Public | Authenticated | Owner/Member | Superadmin |
|------------------------------------------------|--------|---------------|--------------|------------|
| `POST /auth/register`                          | x      |               |              |            |
| `POST /auth/login`                             | x      |               |              |            |
| `POST /auth/logout`                            |        | x             |              |            |
| `GET /tokens`                                  |        | x             |              |            |
| `DELETE /tokens/{id}`                          |        | x             |              |            |
| `GET /users/me`                                |        | x             |              |            |
| `GET /users/{username}`                        | x      |               |              |            |
| `POST /groups`                                 |        | x             |              |            |
| `GET /groups/{name}`                           | x      |               |              |            |
| `PUT /groups/{name}/members/{username}`        |        |               | group owner  | x          |
| `DELETE /groups/{name}/members/{username}`     |        |               | group owner  | x          |
| `DELETE /groups/{name}`                        |        |               | group owner  | x          |
| `GET /packages`                                | x      |               |              |            |
| `GET /packages/{name}`                         | x      |               |              |            |
| `GET /packages/{name}/{version}/metadata`      | x      |               |              |            |
| `GET /packages/{name}/{version}/download`      | x      |               |              |            |
| `POST /packages/{name}/{version}/publish`      |        | x (new pkg)   | owner/member | x          |
| `GET /packages/{name}/owner`                   | x      |               |              |            |
| `PUT /packages/{name}/owner`                   |        |               | pkg owner    | x          |
| `GET /packages/{name}/stats`                   | x      |               |              |            |
| `GET /admin/users`                             |        |               |              | x          |
| `GET /admin/users/{username}`                  |        |               |              | x          |
| `PATCH /admin/users/{username}`                |        |               |              | x          |
| `DELETE /admin/users/{username}`               |        |               |              | x          |

**Legend:**
- **Public**: no authentication required
- **Authenticated**: any valid session or token
- **Owner/Member**: package owner, group member (for group-owned packages), or group owner (for group management)
- **Superadmin**: overrides all ownership checks

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
                  +--+-+----+-----+-+
                     | |    |     |
           +---------+ | +--+--+  +----------+
           |           | |     |             |
   +-------+------+ +--+-+ +--+---+ +------+------+
   | Metadata Store| |Auth | |Search| |Archive Store|
   | (pkg records) | |Store| |Index | | (.nori files)|
   +---------------+ +-----+ +------+ +-------------+
                      |
              +-------+--------+
              | users, tokens, |
              | groups, owners |
              +----------------+
```

- **API Service**: stateless HTTP server handling all REST endpoints. Validates requests, orchestrates storage, returns responses.
- **Metadata Store**: persistent storage for package and version records. Indexed by `(name, version, namespace, platform)`.
- **Auth Store**: persistent storage for user accounts, API tokens, groups, and package ownership records. Enforces the shared namespace uniqueness constraint across users, groups, and packages.
- **Archive Store**: blob storage for `.nori` archive files. Keyed by `{name}/{version}/{namespace}/{platform}/{name}-{version}.nori`.
- **Search Index**: text search over package names and descriptions. Updated on publish.
- **CDN**: optional cache layer for archive downloads. Keyed by the same path as archive store.

## CLI Integration

### Token Storage

API tokens are stored locally at `~/.sushi/credentials.toml` with `0600` file permissions (owner read/write only).

```toml
[registry]
token = "nori_a3Bf9xK2mP7qR4sT8uV1wY6zA0bC5dE3fG9hJ2kL4nN7pQ"
```

The `~/.sushi/` directory matches the existing `SUSHI_HOME` path defined in `sushi_lang/packager/constants.py`.

### nori login

New command. Authenticates the user and stores a token locally.

```
nori login
```

Flow:
1. Prompt for username and password (interactive)
2. `POST /auth/login` with `token_name` set to `nori-cli-{hostname}`
3. Store returned token in `~/.sushi/credentials.toml` with `0600` permissions
4. Print confirmation

### nori logout

New command. Removes local credentials.

```
nori logout
```

Flow:
1. Read token from `~/.sushi/credentials.toml`
2. Delete the credentials file
3. Print confirmation

### nori token list

New command. List the user's active API tokens.

```
nori token list
```

Displays a table: name, prefix, created, last used, expires.

### nori token revoke

New command. Revoke a specific token.

```
nori token revoke <token-id>
```

Flow:
1. `DELETE /tokens/{id}` with authentication
2. Print confirmation

### nori search

Queries `GET /packages`.

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

Publishes to `POST /packages/{name}/{version}/publish`. Requires authentication.

```
nori publish [--namespace testing]
```

Flow:
1. Read token from `~/.sushi/credentials.toml` (fail with helpful message if missing)
2. Load and validate local `nori.toml`
3. Run `nori build` to produce the `.nori` archive
4. Compute SHA-256 of archive
5. Detect current platform
6. `POST /packages/{name}/{version}/publish` with multipart body and `Authorization: Bearer <token>` header
7. Handle `401 UNAUTHORIZED` (prompt to run `nori login`) or `403 FORBIDDEN` (not an owner)
8. Report success or server-side validation error

## Edge Cases

- **Duplicate publish**: rejected with `409 DUPLICATE_VERSION`. The client should inform the user that this exact `(name, version, namespace, platform)` already exists.
- **Platform-specific packages**: a library compiled on macOS produces a `darwin` archive. The same version must be separately published for `linux`. Platform-agnostic packages (pure data, scripts) use `platform=any`.
- **Archive size limit**: 50 MB hard limit enforced server-side. The client should also check before uploading and warn the user.
- **Concurrent publishes**: the server must ensure atomicity per composite key. Two simultaneous publishes of the same key: one succeeds, one gets `409`.
- **Partial upload failure**: if the connection drops mid-upload, nothing is committed. The client can retry the full publish.
- **Malformed archives**: rejected at validation step 6 with `VALIDATION_ERROR`.
- **Name collisions**: usernames, group names, and package names share one flat namespace. Registering a user `foo` when a package `foo` exists returns `NAME_CONFLICT`. The server must check all three tables atomically.
- **First-publish ownership race**: two users simultaneously publishing a new package name. The server must serialize first-publish operations per name. One succeeds and becomes owner; the other gets `FORBIDDEN`.
- **Token revocation during upload**: if a token is revoked while a publish request is in-flight, the server should reject the request with `UNAUTHORIZED`. Validation step 0 (authentication) happens before any storage writes.
- **Group deletion with owned packages**: a group that owns packages cannot be deleted. Transfer or remove ownership first (`OWNERSHIP_REQUIRED`).
- **User deletion and package ownership**: when a user is deleted via the admin API, packages they own become unowned. Superadmins should transfer ownership before deleting users if continuity is needed.
- **Inactive user authentication**: deactivated users are rejected at all authentication points — login returns `FORBIDDEN`, existing session cookies and API tokens silently stop working (treated as unauthenticated).

## Future Considerations (Out of Scope)

These features are explicitly not part of the current specification:

- **Dependency resolution**: transitive dependency tracking and resolution
- **Version yanking**: marking a version as deprecated/withdrawn without deletion
- **Signed packages**: cryptographic signatures for archive integrity beyond SHA-256
- **Rate limiting**: per-client request throttling
- **Webhooks**: notifications on publish events
- **Two-factor authentication (2FA)**: TOTP or WebAuthn second factor
- **Organization hierarchy**: nested groups, roles, inherited permissions
- **Scoped packages**: `@org/package-name` style namespacing
- **Issue and bug reporting**: per-package issue tracker
- **Admin group management**: list, search, and manage groups via admin endpoints
- **Admin package management**: delete packages, manage versions via admin endpoints
