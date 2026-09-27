"""Nori CLI - command line interface for the Sushi package manager."""
import argparse
import traceback
from typing import Callable

from sushi_lang.internals.styling import COLOUR_CHOICES, set_colour_override
from sushi_lang.internals.version import print_banner
from sushi_lang.packager.commands.build import cmd_build
from sushi_lang.packager.commands.info import cmd_info
from sushi_lang.packager.commands.init import cmd_init
from sushi_lang.packager.commands.install import cmd_install
from sushi_lang.packager.commands.list_cmd import cmd_list
from sushi_lang.packager.commands.login import cmd_login
from sushi_lang.packager.commands.publish import cmd_publish
from sushi_lang.packager.commands.remove import cmd_remove
from sushi_lang.packager.commands.search import cmd_search
from sushi_lang.packager.commands.status import cmd_status
from sushi_lang.packager.errors import NoriError, from_os_error, report, report_internal


NORI_TITLE = "\U0001f96c Nori (\u6d77\u82d4) Package Manager"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nori",
        description="Nori - Sushi Lang Package Manager",
    )
    parser.add_argument(
        "--version", action="store_true", help="Show version and exit",
    )
    parser.add_argument(
        "--traceback", action="store_true",
        help="Append the Python traceback to an error (for debugging)",
    )
    parser.add_argument(
        "--color", choices=list(COLOUR_CHOICES), default="auto",
        help="When to use ANSI colour. 'auto' reads NO_COLOR, CLICOLOR_FORCE, TERM and "
             "whether the stream is a terminal.",
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

    # nori publish
    publish_parser = subparsers.add_parser("publish", help="Publish a package to an Omakase repository")
    publish_parser.add_argument(
        "--repository", default=None,
        help="Package repository URL (default: omakase.lubica.net)",
    )
    publish_parser.add_argument(
        "--namespace", default="stable", choices=["stable", "testing"],
        help="Package namespace (default: stable)",
    )
    publish_parser.add_argument(
        "--platform", default=None,
        choices=["darwin", "linux", "windows", "any"],
        help="Target platform (default: auto-detect from OS)",
    )

    # nori help
    subparsers.add_parser("help", help="Show this help message")

    # nori login  (the key is read from the terminal or stdin, never argv --
    # an argv key leaks into shell history and `ps` output)
    login_parser = subparsers.add_parser("login", help="Authenticate with an Omakase repository")
    login_parser.add_argument(
        "--repository", default=None,
        help="Package repository URL (default: omakase.lubica.net)",
    )

    # nori status
    status_parser = subparsers.add_parser("status", help="Show login status and published packages")
    status_parser.add_argument(
        "--repository", default=None,
        help="Package repository URL (default: omakase.lubica.net)",
    )

    return parser


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "init": cmd_init,
    "build": cmd_build,
    "install": cmd_install,
    "search": cmd_search,
    "list": cmd_list,
    "info": cmd_info,
    "remove": cmd_remove,
    "publish": cmd_publish,
    "login": cmd_login,
    "status": cmd_status,
}

def run(args: argparse.Namespace) -> int:
    set_colour_override(args.color)
    print_banner(NORI_TITLE)

    if args.version:
        return 0

    handler = COMMANDS.get(args.command)
    if handler is None:
        build_parser().print_help()
        return 0
    return handler(args)


def cli_main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except NoriError as e:
        report(e)
        _print_traceback(args, e)
        return 1
    except OSError as e:
        report(from_os_error(e))
        _print_traceback(args, e)
        return 1
    except Exception as e:
        report_internal(e, show_help=not args.traceback)
        _print_traceback(args, e)
        return 2


def _print_traceback(args: argparse.Namespace, exc: BaseException) -> None:
    if args.traceback:
        traceback.print_exception(exc)
