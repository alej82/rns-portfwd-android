## Purpose

Lets the Android client forward UDP services over the same Reticulum Link as
its TCP forwards, mapping each local UDP peer to its own datagram stream so
responses return only to the socket that asked.

## ADDED Requirements

### Requirement: UDP forward definitions

A forward definition SHALL carry a protocol of either `tcp` or `udp`. The
system MUST reject any other protocol value. A forward's protocol MUST be
included in duplicate port detection, so a TCP forward and a UDP forward MAY
share the same local port but two UDP forwards MUST NOT.

#### Scenario: TCP and UDP share a port number
- **WHEN** the user configures a TCP forward on port 8080 and a UDP forward on port 8080
- **THEN** the configuration is accepted and both forwards start

#### Scenario: Duplicate UDP port is rejected
- **WHEN** the user configures two UDP forwards on the same local port
- **THEN** the configuration is rejected with a translated duplicate-port error before any session starts

#### Scenario: Unknown protocol is rejected
- **WHEN** a profile contains a forward whose protocol is neither `tcp` nor `udp`
- **THEN** the forward is rejected instead of being treated as TCP

### Requirement: UDP loopback listener

A UDP forward SHALL bind a UDP socket on `127.0.0.1` and, when the platform has
IPv6, on `[::1]` using the same port. Port `0` MUST resolve on IPv4 first and
reuse the resolved port for IPv6. Ports below 1024 MUST be rejected with the
existing low-port error. An IPv6 bind failure MUST degrade to IPv4 only and mark
the forward as degraded rather than aborting the session.

#### Scenario: Dual-stack bind on an explicit port
- **WHEN** a UDP forward requests local port 5353
- **THEN** datagrams sent to `127.0.0.1:5353` and to `[::1]:5353` both reach the remote service

#### Scenario: Ephemeral UDP port
- **WHEN** a UDP forward requests local port `0`
- **THEN** the session reports the port chosen by the operating system and accepts datagrams on it over both families

#### Scenario: Low UDP port is refused
- **WHEN** a UDP forward requests local port 53
- **THEN** starting the session fails with the low-port error and no listener is left behind

#### Scenario: IPv6 unavailable
- **WHEN** the IPv6 loopback bind fails
- **THEN** the forward keeps working on IPv4 and its snapshot reports the degraded state

### Requirement: Per-source datagram sessions

Each distinct local source address and port MUST map to its own remote
datagram stream. A stream MUST be opened lazily on the first datagram from a new
source, and datagrams that arrive before the stream is confirmed MUST be
buffered up to a fixed small bound and then dropped. Responses MUST be delivered
only to the source that created the stream. Multiple UDP forwards and TCP
forwards MUST share the single server Link.

#### Scenario: Round trip to the originating socket
- **WHEN** a local process sends a datagram through a UDP forward and the backend replies
- **THEN** the reply arrives at the same local address and port with identical bytes

#### Scenario: Two local peers are isolated
- **WHEN** two local sockets send datagrams through the same forward
- **THEN** each reply returns only to its originating socket and each peer uses a distinct stream

#### Scenario: Buffered first datagram
- **WHEN** the first datagram of a new source arrives while its stream is still opening
- **THEN** that datagram is delivered once the stream is confirmed

#### Scenario: No session before the Link is ready
- **WHEN** a datagram arrives before the Link is established
- **THEN** the engine waits for the Link up to the connect timeout and drops the pending datagrams if it never becomes ready

### Requirement: Datagram limits and expiry

A datagram that exceeds the negotiated maximum for the stream MUST be dropped
and counted, never fragmented or truncated. A datagram that cannot be queued
MUST be dropped rather than blocking the receive loop or the session worker. A
stream with no traffic for the configured idle timeout MUST be closed and its
slot released.

#### Scenario: Oversized datagram is dropped
- **WHEN** a local datagram is larger than the stream maximum
- **THEN** the engine discards it, keeps the stream usable and records the drop in the log without leaking the payload

#### Scenario: Idle UDP stream expires
- **WHEN** neither side sends a datagram for the idle timeout
- **THEN** the stream is closed, the forward's stream count returns to zero and a new datagram opens a fresh stream

#### Scenario: Link loss clears UDP state
- **WHEN** the Link closes while UDP streams are active
- **THEN** every UDP stream is discarded before reconnecting and stale mappings are not reused

### Requirement: Protocol confirmation before forwarding

Before accepting local traffic the session SHALL confirm each forward's
protocol with `/probe`. A forward whose protocol does not match the service's
reported protocol MUST fail with a translated configuration error naming the
service and both protocols, and no listener MUST be left open. A public forward
MUST probe successfully without a password.

#### Scenario: UDP forward against a TCP service
- **WHEN** a UDP forward names a service the server exposes over TCP
- **THEN** the session stops with the translated protocol-mismatch error and releases its listeners

#### Scenario: TCP forward against a UDP service
- **WHEN** a TCP forward names a service the server exposes over UDP
- **THEN** the session stops with the translated protocol-mismatch error and no TCP port is left open
