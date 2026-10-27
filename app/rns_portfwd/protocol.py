"""Application messages carried over an RNS Channel."""

from __future__ import annotations

import struct

import RNS

from .constants import (
    DATA_FLAG_COMPRESSED,
    DATA_FLAG_EOF,
    DATA_FLAGS_ALLOWED,
    MAX_CREDIT,
    MSG_CLOSE,
    MSG_DATA,
    MSG_OPEN,
    MSG_OPEN_ERR,
    MSG_OPEN_OK,
    MSG_WINDOW,
    STREAM_ID_MAX,
    CloseReason,
    ErrorCode,
)
from .errors import ProtocolError

_MAX_SERVICE_LEN = 255
_MAX_PASSWORD_LEN = 4096


def _check_stream_id(stream_id: object) -> int:
    if not isinstance(stream_id, int) or not 0 <= stream_id <= STREAM_ID_MAX:
        raise ProtocolError(f"stream_id out of range: {stream_id!r}")
    return stream_id


def _check_credit(credit: object) -> int:
    if not isinstance(credit, int) or not 1 <= credit <= MAX_CREDIT:
        raise ProtocolError(f"credit out of range: {credit!r}")
    return credit


def _check_error_code(code: object) -> int:
    try:
        return int(ErrorCode(code))
    except (TypeError, ValueError):
        raise ProtocolError(f"unknown error code: {code!r}") from None


def _check_close_reason(reason: object) -> int:
    try:
        return int(CloseReason(reason))
    except (TypeError, ValueError):
        raise ProtocolError(f"unknown close reason: {reason!r}") from None


def _encode_text(value: object, *, field: str, limit: int) -> bytes:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{field} must be a non-empty string")
    encoded = value.encode("utf-8")
    if len(encoded) > limit:
        raise ProtocolError(f"{field} is too long")
    return encoded


def _decode_text(raw: bytes, *, field: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(f"{field} is not valid UTF-8") from None


class OpenMessage(RNS.MessageBase):
    MSGTYPE = MSG_OPEN

    def __init__(self, stream_id=None, version=None, service=None, password=None):
        self.stream_id = stream_id
        self.version = version
        self.service = service
        self.password = password

    def pack(self) -> bytes:
        stream_id = _check_stream_id(self.stream_id)
        if not isinstance(self.version, int) or not 0 <= self.version <= 255:
            raise ProtocolError("version must be an unsigned byte")
        service = _encode_text(self.service, field="service", limit=_MAX_SERVICE_LEN)
        password = _encode_text(self.password, field="password", limit=_MAX_PASSWORD_LEN)
        return (
            struct.pack(">HBHH", stream_id, self.version, len(service), len(password))
            + service
            + password
        )

    def unpack(self, raw: bytes):
        if len(raw) < 7:
            raise ProtocolError("OPEN message is too short")
        stream_id, version, service_len, password_len = struct.unpack(">HBHH", raw[:7])
        expected = 7 + service_len + password_len
        if len(raw) != expected:
            raise ProtocolError("OPEN message has an invalid length")
        _check_stream_id(stream_id)
        service_start = 7
        password_start = service_start + service_len
        self.stream_id = stream_id
        self.version = version
        self.service = _decode_text(raw[service_start:password_start], field="service")
        self.password = _decode_text(raw[password_start:], field="password")


class OpenOkMessage(RNS.MessageBase):
    MSGTYPE = MSG_OPEN_OK

    def __init__(self, stream_id=None, credit=None):
        self.stream_id = stream_id
        self.credit = credit

    def pack(self) -> bytes:
        return struct.pack(">HI", _check_stream_id(self.stream_id), _check_credit(self.credit))

    def unpack(self, raw: bytes):
        if len(raw) != 6:
            raise ProtocolError("OPEN_OK message has an invalid length")
        self.stream_id, self.credit = struct.unpack(">HI", raw)
        _check_stream_id(self.stream_id)
        _check_credit(self.credit)


class OpenErrMessage(RNS.MessageBase):
    MSGTYPE = MSG_OPEN_ERR

    def __init__(self, stream_id=None, code=None):
        self.stream_id = stream_id
        self.code = code

    def pack(self) -> bytes:
        return struct.pack(
            ">HB", _check_stream_id(self.stream_id), _check_error_code(self.code)
        )

    def unpack(self, raw: bytes):
        if len(raw) != 3:
            raise ProtocolError("OPEN_ERR message has an invalid length")
        self.stream_id, self.code = struct.unpack(">HB", raw)
        _check_stream_id(self.stream_id)
        _check_error_code(self.code)


class DataMessage(RNS.MessageBase):
    MSGTYPE = MSG_DATA

    def __init__(self, stream_id=None, data=None, eof=False, compressed=False):
        self.stream_id = stream_id
        self.data = data if data is not None else b""
        self.eof = bool(eof)
        self.compressed = bool(compressed)

    def pack(self) -> bytes:
        flags = 0
        if self.eof:
            flags |= DATA_FLAG_EOF
        if self.compressed:
            flags |= DATA_FLAG_COMPRESSED
        if not isinstance(self.data, (bytes, bytearray)):
            raise ProtocolError("DATA payload must be bytes")
        return struct.pack(">HB", _check_stream_id(self.stream_id), flags) + bytes(self.data)

    def unpack(self, raw: bytes):
        if len(raw) < 3:
            raise ProtocolError("DATA message is too short")
        stream_id, flags = struct.unpack(">HB", raw[:3])
        if flags & ~DATA_FLAGS_ALLOWED:
            raise ProtocolError(f"DATA message has unknown flags: {flags:#x}")
        _check_stream_id(stream_id)
        self.stream_id = stream_id
        self.eof = bool(flags & DATA_FLAG_EOF)
        self.compressed = bool(flags & DATA_FLAG_COMPRESSED)
        self.data = raw[3:]


class WindowMessage(RNS.MessageBase):
    MSGTYPE = MSG_WINDOW

    def __init__(self, stream_id=None, credit=None):
        self.stream_id = stream_id
        self.credit = credit

    def pack(self) -> bytes:
        return struct.pack(">HI", _check_stream_id(self.stream_id), _check_credit(self.credit))

    def unpack(self, raw: bytes):
        if len(raw) != 6:
            raise ProtocolError("WINDOW message has an invalid length")
        self.stream_id, self.credit = struct.unpack(">HI", raw)
        _check_stream_id(self.stream_id)
        _check_credit(self.credit)


class CloseMessage(RNS.MessageBase):
    MSGTYPE = MSG_CLOSE

    def __init__(self, stream_id=None, reason=None):
        self.stream_id = stream_id
        self.reason = reason

    def pack(self) -> bytes:
        return struct.pack(
            ">HB", _check_stream_id(self.stream_id), _check_close_reason(self.reason)
        )

    def unpack(self, raw: bytes):
        if len(raw) != 3:
            raise ProtocolError("CLOSE message has an invalid length")
        self.stream_id, self.reason = struct.unpack(">HB", raw)
        _check_stream_id(self.stream_id)
        _check_close_reason(self.reason)


MESSAGE_TYPES = (
    OpenMessage,
    OpenOkMessage,
    OpenErrMessage,
    DataMessage,
    WindowMessage,
    CloseMessage,
)


def register_message_types(channel) -> None:
    for message_type in MESSAGE_TYPES:
        channel.register_message_type(message_type)
