"""Darwin/macOS-specific stdio handle declarations."""


def get_stderr_handle_name() -> str:
    """Get the platform-specific name for stderr handle."""
    return "__stderrp"
