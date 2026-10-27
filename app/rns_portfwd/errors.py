"""Typed errors carrying a stable process exit code."""

from __future__ import annotations

from . import constants


class PortfwdError(Exception):
    """Base class for expected, user-facing failures."""

    exit_code = constants.EXIT_RUNTIME


class ConfigError(PortfwdError):
    exit_code = constants.EXIT_CONFIG


class SharedInstanceError(PortfwdError):
    exit_code = constants.EXIT_RNS


class IdentityError(PortfwdError):
    exit_code = constants.EXIT_RNS


class ResolutionError(PortfwdError):
    exit_code = constants.EXIT_RESOLUTION


class AuthError(PortfwdError):
    exit_code = constants.EXIT_AUTH


class BindError(PortfwdError):
    exit_code = constants.EXIT_BIND


class ProtocolError(PortfwdError):
    exit_code = constants.EXIT_RUNTIME


class BackendError(PortfwdError):
    exit_code = constants.EXIT_RUNTIME
