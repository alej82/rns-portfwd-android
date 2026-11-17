"""Command line entry point for the server."""

from __future__ import annotations

import argparse
import math
import signal
import sys

from . import __version__
from .config import (
    apply_allow_identities,
    apply_server_secrets,
    default_server_name,
    parse_allow_identity,
    parse_expose,
    parse_expose_public,
    parse_expose_udp,
    parse_expose_udp_public,
    read_secret_source,
    validate_allow_identities,
    validate_link_stamp_cost,
    validate_services,
)
from .constants import (
    DEFAULT_ANNOUNCE_INTERVAL,
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_BACKEND_TIMEOUT,
    DEFAULT_HALF_OPEN_TIMEOUT,
    DEFAULT_LINK_STAMP_COST,
    DEFAULT_MAX_CONNECTIONS_PER_PUBLIC,
    DEFAULT_MAX_CONNECTIONS_PER_SERVICE,
    DEFAULT_MAX_HALF_OPEN,
    DEFAULT_MAX_LINKS,
    DEFAULT_MAX_STREAMS,
    DEFAULT_UDP_IDLE_TIMEOUT,
    DEFAULT_WINDOW,
    EXIT_OK,
    IDENTITY_WAIT_TIMEOUT,
    MAX_CREDIT,
    STREAM_ID_MAX,
)
from .discovery import link_stamp_validation_supported
from .errors import ConfigError, PortfwdError
from .logging import register_secret, setup_logging
from .server import PortfwdServer

EXAMPLES = """\
examples:
  rns-portfwd-server --expose 127.0.0.1:22:ssh:s3cret --list-password listpass
  rns-portfwd-server --expose-udp 127.0.0.1:5353:dns:s3cret --announce
  rns-portfwd-server --expose '[::1]:5432:db:pa:ss' --no-compress
  rns-portfwd-server --expose-public 127.0.0.1:80:web --expose 127.0.0.1:22:ssh:s3cret
  rns-portfwd-server --expose 127.0.0.1:22:ssh:s3cret \\
      --allow-identity ssh:0123456789abcdef0123456789abcdef
  rns-portfwd-server --expose-public 127.0.0.1:80:web \\
      --allow-identity web:fedcba9876543210fedcba9876543210
  rns-portfwd-server --expose 127.0.0.1:22:ssh:s3cret --link-stamp-cost 12.5
  rns-portfwd-server --expose 127.0.0.1:22:ssh:s3cret --max-half-open 32 \\
      --half-open-timeout 30
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rns-portfwd-server",
        description="Expose local TCP and UDP services over a Reticulum shared instance.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=None, help="Reticulum configuration directory")
    parser.add_argument(
        "--identity",
        default="~/.config/rns-portfwd/server.id",
        help="persistent server identity file",
    )
    parser.add_argument("--name", default=None, help="display name used in announces")
    parser.add_argument(
        "--list-password",
        default=None,
        help="password required by /list (listing disabled when omitted)",
    )
    parser.add_argument(
        "--expose",
        action="append",
        default=[],
        metavar="HOST:PORT:NAME:PASSWORD",
        help="TCP service to expose; may be repeated",
    )
    parser.add_argument(
        "--expose-udp",
        action="append",
        default=[],
        metavar="HOST:PORT:NAME:PASSWORD",
        help="UDP service to expose; may be repeated",
    )
    parser.add_argument(
        "--expose-public",
        action="append",
        default=[],
        metavar="HOST:PORT:NAME",
        help="TCP service to expose without a password; may be repeated",
    )
    parser.add_argument(
        "--expose-udp-public",
        action="append",
        default=[],
        metavar="HOST:PORT:NAME",
        help="UDP service to expose without a password; may be repeated",
    )
    parser.add_argument(
        "--allow-identity",
        action="append",
        default=[],
        metavar="SERVICE:HASH",
        help=(
            "restrict SERVICE to a Reticulum identity hash (32 hex characters); "
            "may be repeated"
        ),
    )
    parser.add_argument("--announce", action="store_true", help="emit announces periodically")
    parser.add_argument(
        "--announce-interval",
        type=float,
        default=DEFAULT_ANNOUNCE_INTERVAL,
        help="seconds between announces",
    )
    parser.add_argument("--no-compress", action="store_true", help="disable bz2 compression")
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW, help="per-stream credit window in bytes"
    )
    parser.add_argument("--max-links", type=int, default=DEFAULT_MAX_LINKS)
    parser.add_argument("--max-streams", type=int, default=DEFAULT_MAX_STREAMS)
    parser.add_argument(
        "--max-connections-per-service",
        type=int,
        default=DEFAULT_MAX_CONNECTIONS_PER_SERVICE,
    )
    parser.add_argument(
        "--max-connections-per-public",
        type=int,
        default=DEFAULT_MAX_CONNECTIONS_PER_PUBLIC,
        help="concurrent connections allowed per passwordless service",
    )
    parser.add_argument("--auth-timeout", type=float, default=DEFAULT_AUTH_TIMEOUT)
    parser.add_argument("--backend-timeout", type=float, default=DEFAULT_BACKEND_TIMEOUT)
    parser.add_argument(
        "--link-stamp-cost",
        type=float,
        default=DEFAULT_LINK_STAMP_COST,
        metavar="COST",
        help=(
            "require a Reticulum link-request proof-of-work stamp of at least COST "
            "(0 disables stamping, maximum 40; requires rns>=1.6)"
        ),
    )
    parser.add_argument(
        "--max-half-open",
        type=int,
        default=DEFAULT_MAX_HALF_OPEN,
        metavar="N",
        help="maximum links that have not completed the handshake (0 is unlimited)",
    )
    parser.add_argument(
        "--half-open-timeout",
        type=float,
        default=DEFAULT_HALF_OPEN_TIMEOUT,
        metavar="SECONDS",
        help="establishment timeout for accepted links that are still half-open",
    )
    parser.add_argument(
        "--udp-idle-timeout",
        type=float,
        default=DEFAULT_UDP_IDLE_TIMEOUT,
        help="seconds before an idle UDP session is closed",
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
    return parser


def _validate_options(args) -> None:
    if not 1024 <= args.window <= MAX_CREDIT:
        raise ConfigError(f"--window must be between 1024 and {MAX_CREDIT}")
    if not 1 <= args.max_streams <= STREAM_ID_MAX:
        raise ConfigError(f"--max-streams must be between 1 and {STREAM_ID_MAX}")
    if args.max_links < 1:
        raise ConfigError("--max-links must be at least 1")
    if args.max_connections_per_service < 1:
        raise ConfigError("--max-connections-per-service must be at least 1")
    if args.max_connections_per_public < 1:
        raise ConfigError("--max-connections-per-public must be at least 1")
    if args.announce_interval <= 0:
        raise ConfigError("--announce-interval must be positive")
    if args.auth_timeout <= 0:
        raise ConfigError("--auth-timeout must be positive")
    if args.allow_identity and args.auth_timeout <= IDENTITY_WAIT_TIMEOUT:
        raise ConfigError(
            f"--auth-timeout must exceed {IDENTITY_WAIT_TIMEOUT:g}s when "
            "--allow-identity is used"
        )
    if args.backend_timeout <= 0:
        raise ConfigError("--backend-timeout must be positive")
    if args.udp_idle_timeout <= 0:
        raise ConfigError("--udp-idle-timeout must be positive")
    args.link_stamp_cost = validate_link_stamp_cost(args.link_stamp_cost)
    if args.max_half_open < 0:
        raise ConfigError("--max-half-open must be 0 or greater")
    if not math.isfinite(args.half_open_timeout) or args.half_open_timeout <= 0:
        raise ConfigError("--half-open-timeout must be a positive finite number")
    if args.link_stamp_cost > 0 and not link_stamp_validation_supported():
        raise ConfigError(
            "--link-stamp-cost requires native link-request stamping support; "
            "install rns>=1.6 or leave the cost at 0"
        )
    if not args.name or len(args.name) > 64:
        raise ConfigError("--name must be between 1 and 64 characters")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = setup_logging(args.verbose)
    try:
        services = [parse_expose(item) for item in args.expose]
        services += [parse_expose_udp(item) for item in args.expose_udp]
        services += [parse_expose_public(item) for item in args.expose_public]
        services += [parse_expose_udp_public(item) for item in args.expose_udp_public]
        validate_services(services)
        allow_entries = [parse_allow_identity(item) for item in args.allow_identity]
        validate_allow_identities(services, allow_entries)
        name = args.name or default_server_name()
        args.name = name
        _validate_options(args)
        document, source_used = read_secret_source(args.password_stdin, args.secrets_fd)
        services, list_password = apply_server_secrets(
            services, args.list_password, document, source_used
        )
        services = apply_allow_identities(services, allow_entries)
        for service in services:
            register_secret(service.password)
        register_secret(list_password)

        server = PortfwdServer(
            services=services,
            list_password=list_password,
            identity_path=args.identity,
            name=name,
            config_dir=args.config,
            logger=logger,
            announce=args.announce,
            announce_interval=args.announce_interval,
            window=args.window,
            compress=not args.no_compress,
            max_links=args.max_links,
            max_streams=args.max_streams,
            max_connections_per_service=args.max_connections_per_service,
            max_connections_per_public=args.max_connections_per_public,
            auth_timeout=args.auth_timeout,
            backend_timeout=args.backend_timeout,
            udp_idle_timeout=args.udp_idle_timeout,
            verbosity=args.verbose,
            link_stamp_cost=args.link_stamp_cost,
            max_half_open=args.max_half_open,
            half_open_timeout=args.half_open_timeout,
        )

        def on_signal(signum, frame):
            server.stop()

        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)

        try:
            hexhash = server.start()
            print(f"destination: {hexhash}")
            print(f"services: {', '.join(server.service_names)}", flush=True)
            server.wait()
        finally:
            server.stop()
        return EXIT_OK
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
