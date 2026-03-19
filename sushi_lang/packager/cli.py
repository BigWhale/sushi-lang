"""Nori CLI - command line interface for the Sushi package manager."""
import argparse
import sys


def _print_banner() -> None:
    from sushi_lang.internals.version import _ensure_utf8_stdout, _get_versions
    from sushi_lang import __dev__ as is_dev
    import datetime

    _ensure_utf8_stdout()
    v = _get_versions()
    today = datetime.date.today().isoformat()

    use_ansi = sys.stdout.isatty()
    if use_ansi:
        BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"
    else:
        BOLD, DIM, RESET = "", "", ""

    dev_marker = " (dev)" if is_dev else ""
    print(
        f"{BOLD} \U0001f96c Nori (\u6d77\u82d4) Package Manager{RESET} \u2022 {v['app']}{dev_marker}\n"
        f"{DIM}Python {v['python']} \u2022 llvmlite {v['llvmlite']} \u2022 LLVM {v['llvm']} \u2022 {today}{RESET}\n"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nori",
        description="Nori - Sushi Lang Package Manager",
    )
    parser.add_argument(
        "--version", action="store_true", help="Show version and exit",
    )
    subparsers = parser.add_subparsers(dest="command")

    # nori init
    subparsers.add_parser("init", help="Create a new nori.toml manifest")

    # nori build
    subparsers.add_parser("build", help="Build a .nori package archive")

    # nori install [pkg] [from <source>] [--global]
    install_parser = subparsers.add_parser("install", help="Install a package")
    install_parser.add_argument(
        "--repository", default=None,
        help="Package repository URL (default: omakase.lubica.net)",
    )
    install_parser.add_argument(
        "--global", dest="is_global", action="store_true", default=False,
        help="Install globally (to ~/.sushi/bento/) even when in a project",
    )
    install_parser.add_argument("package", nargs="?", default=None, help="Package name or .nori file path")
    install_parser.add_argument("from_keyword", nargs="?", metavar="from", help=argparse.SUPPRESS)
    install_parser.add_argument("source", nargs="?", default=None, help="Source path (local directory or .nori file)")

    # nori search <query>
    search_parser = subparsers.add_parser("search", help="Search for packages")
    search_parser.add_argument(
        "--repository", default=None,
        help="Package repository URL (default: omakase.lubica.net)",
    )
    search_parser.add_argument(
        "--namespace", default="stable", choices=["stable", "testing"],
        help="Package namespace (default: stable)",
    )
    search_parser.add_argument(
        "--platform", default=None,
        choices=["darwin", "linux", "windows", "any"],
        help="Filter by platform",
    )
    search_parser.add_argument(
        "--sort", default="relevance",
        choices=["relevance", "name", "downloads", "updated"],
        help="Sort order (default: relevance)",
    )
    search_parser.add_argument(
        "--page", type=int, default=1, help="Page number (default: 1)",
    )
    search_parser.add_argument(
        "--per-page", type=int, default=20, dest="per_page",
        help="Results per page (default: 20)",
    )
    search_parser.add_argument("query", help="Search query")

    # nori list [--global]
    list_parser = subparsers.add_parser("list", help="List installed packages")
    list_parser.add_argument(
        "--global", dest="is_global", action="store_true", default=False,
        help="List global packages (ignore project context)",
    )

    # nori info <pkg>
    info_parser = subparsers.add_parser("info", help="Show package details")
    info_parser.add_argument("package", help="Package name")

    # nori remove <pkg> [--global]
    remove_parser = subparsers.add_parser("remove", help="Remove an installed package")
    remove_parser.add_argument(
        "--global", dest="is_global", action="store_true", default=False,
        help="Remove from global packages (ignore project context)",
    )
    remove_parser.add_argument("package", help="Package name")

    # nori help
    subparsers.add_parser("help", help="Show this help message")

    # nori login <api-key>
    login_parser = subparsers.add_parser("login", help="Authenticate with an Omakase repository")
    login_parser.add_argument(
        "--repository", default=None,
        help="Package repository URL (default: omakase.lubica.net)",
    )
    login_parser.add_argument("api_key", help="API key (starts with nori_)")

    # nori status
    status_parser = subparsers.add_parser("status", help="Show login status and published packages")
    status_parser.add_argument(
        "--repository", default=None,
        help="Package repository URL (default: omakase.lubica.net)",
    )

    return parser


def run(args: argparse.Namespace) -> int:
    if args.version:
        _print_banner()
        return 0

    if args.command is None or args.command == "help":
        build_parser().print_help()
        return 0

    if args.command == "init":
        from sushi_lang.packager.commands.init import cmd_init
        return cmd_init(args)

    if args.command == "build":
        from sushi_lang.packager.commands.build import cmd_build
        return cmd_build(args)

    if args.command == "install":
        from sushi_lang.packager.commands.install import cmd_install
        return cmd_install(args)

    if args.command == "search":
        from sushi_lang.packager.commands.search import cmd_search
        return cmd_search(args)

    if args.command == "list":
        from sushi_lang.packager.commands.list_cmd import cmd_list
        return cmd_list(args)

    if args.command == "info":
        from sushi_lang.packager.commands.info import cmd_info
        return cmd_info(args)

    if args.command == "remove":
        from sushi_lang.packager.commands.remove import cmd_remove
        return cmd_remove(args)

    if args.command == "login":
        from sushi_lang.packager.commands.login import cmd_login
        return cmd_login(args)

    if args.command == "status":
        from sushi_lang.packager.commands.status import cmd_status
        return cmd_status(args)

    return 0


def cli_main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
