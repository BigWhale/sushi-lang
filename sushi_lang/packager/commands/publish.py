"""nori publish - publish a package to an Omakase repository."""
import argparse
import hashlib
import platform
from pathlib import Path

from sushi_lang.packager.api_client import api_upload_multipart, ApiError
from sushi_lang.packager.constants import MANIFEST_NAME
from sushi_lang.packager.credentials import load_token
from sushi_lang.packager.errors import RELOGIN_HELP, NoriError
from sushi_lang.packager.manifest import load_manifest
from sushi_lang.packager.repository import resolve_repository


def _detect_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "linux":
        return "linux"
    return "any"


def cmd_publish(args: argparse.Namespace) -> int:
    repository = resolve_repository(args)
    ns = getattr(args, "namespace", "stable")
    plat = getattr(args, "platform", None) or _detect_platform()

    manifest_path = Path.cwd() / MANIFEST_NAME
    manifest = load_manifest()

    # Locate built archive
    archive_path = Path.cwd() / "dist" / f"{manifest.archive_name}.nori"
    if not archive_path.exists():
        raise NoriError("NE2007", path=archive_path,
                        helps=["run 'nori build' first to create the package archive"])

    # Require authentication
    token = load_token(repository)
    if not token:
        raise NoriError("NE5003", repository=repository,
                        helps=["run 'nori login' to authenticate first"])

    # Read archive and compute SHA-256
    archive_data = archive_path.read_bytes()
    sha256 = hashlib.sha256(archive_data).hexdigest()

    # Build manifest content with [publish] section
    manifest_content = manifest_path.read_text()
    manifest_content += "\n[publish]\n"
    manifest_content += f'namespace = "{ns}"\n'
    manifest_content += f'platform = "{plat}"\n'

    # Upload
    parts = [
        ("manifest", MANIFEST_NAME, "application/toml", manifest_content.encode()),
        ("archive", f"{manifest.archive_name}.nori", "application/octet-stream", archive_data),
    ]

    print(f"Publishing {manifest.name} v{manifest.version} to {repository}...")
    print(f"  Namespace: {ns}, Platform: {plat}")
    print(f"  Archive: {archive_path.name} ({len(archive_data)} bytes)")

    try:
        result = api_upload_multipart(
            repository,
            f"/packages/{manifest.name}/{manifest.version}/publish",
            token,
            parts,
            extra_headers={"X-Sha256": sha256},
        )
    except ApiError as e:
        refusal = _refusal(e, repository, manifest.name, manifest.version)
        if refusal is None:
            raise
        raise refusal from e

    published_at = result.get("published_at", "")
    print(f"Published {manifest.name} v{manifest.version}")
    if published_at:
        print(f"  Published at: {published_at}")
    return 0


def _refusal(e: ApiError, repository: str, name: str, version: str) -> NoriError | None:
    """The code of an HTTP status that publish names; None keeps NE5002."""
    if e.status == 401:
        return NoriError("NE5004", repository=repository, helps=[RELOGIN_HELP])
    if e.status == 403:
        return NoriError("NE5006", name=name)
    if e.status == 409:
        return NoriError("NE5007", version=version, name=name)
    if e.status == 413:
        return NoriError("NE5008")
    if e.status == 422:
        return NoriError("NE5009", detail=e.message)
    return None
