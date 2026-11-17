"""Command line entry point for the client."""

from __future__ import annotations

import argparse
import signal
import sys

from . import __version__
from .client import PortfwdClient, run_list, run_probe
from .config import (
    apply_forward_secrets,
    parse_forward,
    parse_forward_public,
    parse_forward_udp,
    parse_forward_udp_public,
    read_secret_source,
    resolve_list_password,
    resolve_probe_password,
    validate_forwards,
    validate_link_stamp_cost,
)
from .constants import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_STREAMS,
    DEFAULT_PATH_TIMEOUT,
    DEFAULT_UDP_IDLE_TIMEOUT,
    DEFAULT_WINDOW,
    EXIT_OK,
    MAX_CREDIT,
    STREAM_ID_MAX,
)
from .discovery import load_or_create_identity, parse_destination_hash
from .errors import ConfigError, PortfwdError
from .logging import register_secret, setup_logging

DEFAULT_CLIENT_IDENTITY = "~/.config/rns-portfwd/client.id"

EXAMPLES = """\
examples:
  rns-portfwd-client list --server <hash> --list-password listpass
  rns-portfwd-client probe --server <hash> --service ssh --service-password s3cret
  rns-portfwd-client forward --server <hash> --forward ssh:2222:s3cret
  rns-portfwd-client forward --server <hash> --forward-udp dns:5353:s3cret
  rns-portfwd-client forward --server <hash> --forward-public web:8080
  rns-portfwd-client forward --server <hash> --forward ssh:2222:- --secrets-fd 3
  rns-portfwd-client forward --server <hash> --link-stamp-cost 12.5 \\
      --forward ssh:2222:s3cret
  rns-portfwd-client list --server <hash> --no-link-stamp --list-password listpass
  rns-portfwd-client identity --identity ~/.config/rns-portfwd/client.id
  rns-portfwd-client forward --server <hash> --identity ~/.config/rns-portfwd/client.id \\
      --forward ssh:2222:s3cret
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rns-portfwd-client",
        description="Forward local TCP and UDP ports to services exposed over Reticulum.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=None, help="Reticulum configuration directory")
    parser.add_argument(
        "--identity",
        default=None,
        metavar="PATH",
        help="persistent client identity file; identifies on every Link",
    )
    parser.add_argument(
        "--path-timeout",
        type=float,
        default=DEFAULT_PATH_TIMEOUT,
        help="seconds to wait for a path to the server",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT,
        help="seconds to wait for the Link or a free stream slot",
    )
    parser.add_argument(
        "--link-stamp-cost",
        type=float,
        default=None,
        metavar="COST",
        help="override the server's advertised link-request stamp cost",
    )
    parser.add_argument(
        "--no-link-stamp",
        action="store_true",
        default=False,
        help="do not stamp link requests (diagnostic; cannot reach a stamping server)",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read a JSON secret document from stdin",
    )
    parser.add_argument(
        "--secrets-fd",
        type=int,
        default=None,
        metavar="FD",
        help="read a JSON secret document from a file descriptor",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)
    common.add_argument("--identity", default=argparse.SUPPRESS, metavar="PATH")
    common.add_argument("--path-timeout", type=float, default=argparse.SUPPRESS)
    common.add_argument("--connect-timeout", type=float, default=argparse.SUPPRESS)
    common.add_argument(
        "--link-stamp-cost", type=float, default=argparse.SUPPRESS, metavar="COST"
    )
    common.add_argument(
        "--no-link-stamp", action="store_true", default=argparse.SUPPRESS
    )
    common.add_argument("--password-stdin", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--secrets-fd", type=int, default=argparse.SUPPRESS)
    common.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest="command", required=True)

    forward = subparsers.add_parser(
        "forward", parents=[common], help="listen locally and forward to services"
    )
    forward.add_argument("--server", required=True, help="server destination hash")
    forward.add_argument(
        "--forward",
        action="append",
        default=[],
        metavar="NAME:LOCAL_PORT:PASSWORD",
        help="TCP forward definition; may be repeated",
    )
    forward.add_argument(
        "--forward-udp",
        action="append",
        default=[],
        metavar="NAME:LOCAL_PORT:PASSWORD",
        help="UDP forward definition; may be repeated",
    )
    forward.add_argument(
        "--forward-public",
        action="append",
        default=[],
        metavar="NAME:LOCAL_PORT",
        help="TCP forward to a passwordless service; may be repeated",
    )
    forward.add_argument(
        "--forward-udp-public",
        action="append",
        default=[],
        metavar="NAME:LOCAL_PORT",
        help="UDP forward to a passwordless service; may be repeated",
    )
    forward.add_argument("--bind", default="127.0.0.1", help="local bind address")
    forward.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    forward.add_argument("--max-streams", type=int, default=DEFAULT_MAX_STREAMS)
    forward.add_argument(
        "--udp-idle-timeout",
        type=float,
        default=DEFAULT_UDP_IDLE_TIMEOUT,
        help="seconds before an idle UDP session is closed",
    )
    forward.add_argument("--no-compress", action="store_true")

    listing = subparsers.add_parser("list", parents=[common], help="list public services")
    listing.add_argument("--server", required=True, help="server destination hash")
    listing.add_argument("--list-password", default=None)
    listing.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="extra time to wait for a path or announce",
    )
    listing.add_argument(
        "--refresh",
        type=float,
        default=0.0,
        help="repeat the listing every N seconds",
    )

    probe = subparsers.add_parser(
        "probe", parents=[common], help="validate one service credential"
    )
    probe.add_argument("--server", required=True, help="server destination hash")
    probe.add_argument("--service", required=True)
    probe.add_argument("--service-password", default=None)

    identity = subparsers.add_parser(
        "identity",
        help="print the client identity hash to add to a server allow-list",
    )
    identity.add_argument(
        "--identity",
        default=argparse.SUPPRESS,
        metavar="PATH",
        help=f"identity file (default: {DEFAULT_CLIENT_IDENTITY})",
    )

    return parser


def _validate_common(args) -> None:
    if args.path_timeout <= 0:
        raise ConfigError("--path-timeout must be positive")
    if args.connect_timeout <= 0:
        raise ConfigError("--connect-timeout must be positive")
    link_stamp_cost = getattr(args, "link_stamp_cost", None)
    no_link_stamp = getattr(args, "no_link_stamp", False)
    if link_stamp_cost is not None and no_link_stamp:
        raise ConfigError(
            "--link-stamp-cost and --no-link-stamp are mutually exclusive"
        )
    if link_stamp_cost is not None:
        link_stamp_cost = validate_link_stamp_cost(link_stamp_cost)
        args.link_stamp_cost = link_stamp_cost


def _load_client_identity(args, logger):
    path = getattr(args, "identity", None)
    if path is None:
        return None
    if not str(path).strip():
        raise ConfigError("--identity must not be empty")
    identity = load_or_create_identity(path)
    logger.debug("using client identity %s", identity.hash.hex())
    return identity


def _run_identity_command(args) -> int:
    path = getattr(args, "identity", None) or DEFAULT_CLIENT_IDENTITY
    identity = load_or_create_identity(path)
    print(identity.hash.hex(), flush=True)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = setup_logging(args.verbose)
    try:
        _validate_common(args)
        if args.command == "identity":
            return _run_identity_command(args)

        document, source_used = read_secret_source(args.password_stdin, args.secrets_fd)

        if args.command == "forward":
            forwards = [parse_forward(item) for item in args.forward]
            forwards += [parse_forward_udp(item) for item in args.forward_udp]
            forwards += [parse_forward_public(item) for item in args.forward_public]
            forwards += [parse_forward_udp_public(item) for item in args.forward_udp_public]
            validate_forwards(forwards)
            if not 1024 <= args.window <= MAX_CREDIT:
                raise ConfigError(f"--window must be between 1024 and {MAX_CREDIT}")
            if not 1 <= args.max_streams <= STREAM_ID_MAX:
                raise ConfigError(f"--max-streams must be between 1 and {STREAM_ID_MAX}")
            if args.udp_idle_timeout <= 0:
                raise ConfigError("--udp-idle-timeout must be positive")
            forwards = apply_forward_secrets(forwards, document, source_used)
            for forward in forwards:
                register_secret(forward.password)
            server_hash = parse_destination_hash(args.server)
            client_identity = _load_client_identity(args, logger)
            client = PortfwdClient(
                server_hash=server_hash,
                forwards=forwards,
                bind=args.bind,
                logger=logger,
                config_dir=args.config,
                connect_timeout=args.connect_timeout,
                path_timeout=args.path_timeout,
                window=args.window,
                max_streams=args.max_streams,
                compress=not args.no_compress,
                udp_idle_timeout=args.udp_idle_timeout,
                verbosity=args.verbose,
                client_identity=client_identity,
                link_stamp_cost=args.link_stamp_cost,
                no_link_stamp=args.no_link_stamp,
            )

            def on_signal(signum, frame):
                client.stop()

            signal.signal(signal.SIGINT, on_signal)
            signal.signal(signal.SIGTERM, on_signal)
            try:
                client.run()
            finally:
                client.stop()
            return EXIT_OK

        if args.command == "list":
            if args.wait < 0 or args.refresh < 0:
                raise ConfigError("--wait and --refresh must not be negative")
            list_password = resolve_list_password(
                args.list_password, document, source_used
            )
            register_secret(list_password)
            client_identity = _load_client_identity(args, logger)
            run_list(
                server_hash_text=args.server,
                list_password=list_password,
                logger=logger,
                config_dir=args.config,
                verbosity=args.verbose,
                path_timeout=args.path_timeout,
                connect_timeout=args.connect_timeout,
                wait_seconds=args.wait,
                refresh=args.refresh,
                client_identity=client_identity,
                link_stamp_cost=args.link_stamp_cost,
                no_link_stamp=args.no_link_stamp,
            )
            return EXIT_OK

        if args.command == "probe":
            password = resolve_probe_password(
                args.service_password, document, source_used, args.service
            )
            register_secret(password)
            client_identity = _load_client_identity(args, logger)
            run_probe(
                server_hash_text=args.server,
                service=args.service,
                password=password,
                logger=logger,
                config_dir=args.config,
                verbosity=args.verbose,
                path_timeout=args.path_timeout,
                connect_timeout=args.connect_timeout,
                client_identity=client_identity,
                link_stamp_cost=args.link_stamp_cost,
                no_link_stamp=args.no_link_stamp,
            )
            return EXIT_OK

        parser.error("unknown command")
        return 2
    except PortfwdError as exc:
        logger.error("%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception as exc:
        logger.error("unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
