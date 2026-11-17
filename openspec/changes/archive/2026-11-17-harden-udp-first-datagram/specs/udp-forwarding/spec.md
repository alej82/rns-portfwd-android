## MODIFIED Requirements

### Requirement: Per-source datagram sessions

Each distinct local source address and port MUST map to its own remote
datagram stream. A stream MUST be opened lazily on the first datagram from a new
source, and datagrams that arrive before the stream is confirmed MUST be
buffered up to a fixed small bound and then dropped. Buffered datagrams MUST
survive the rebuild of the Link that carries their stream: when the setup fails
because the Link is unavailable or is being rebuilt, the buffered datagrams MUST
be retried once against the replacement Link before they are discarded.
Responses MUST be delivered only to the source that created the stream.
Multiple UDP forwards and TCP forwards MUST share the single server Link.

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

#### Scenario: The datagram that triggers a Link rebuild
- **WHEN** the first datagram of a new source arrives while the session is rebuilding a Link that has been idle
- **THEN** the datagram is delivered on the replacement Link instead of being lost
- **AND** a second datagram sent after the rebuild arrives on the peer's existing stream

#### Scenario: A rejected open is not retried
- **WHEN** the server rejects the stream open with an authentication, protocol or version error
- **THEN** the outcome is reported as it is today and the buffered datagrams are not retried

### Requirement: Datagram limits and expiry

A datagram that exceeds the negotiated maximum for the stream MUST be dropped
and counted, never fragmented or truncated. A datagram that cannot be queued
MUST be dropped rather than blocking the receive loop or the session worker. A
datagram discarded because its stream could not be set up — including the
failure to obtain a usable Link — MUST be counted and reported with a reason
instead of disappearing silently. A stream with no traffic for the configured
idle timeout MUST be closed and its slot released.

#### Scenario: Oversized datagram is dropped
- **WHEN** a local datagram is larger than the stream maximum
- **THEN** the engine discards it, keeps the stream usable and records the drop in the log without leaking the payload

#### Scenario: Idle UDP stream expires
- **WHEN** neither side sends a datagram for the idle timeout
- **THEN** the stream is closed, the forward's stream count returns to zero and a new datagram opens a fresh stream

#### Scenario: Link loss clears UDP state
- **WHEN** the Link closes while UDP streams are active
- **THEN** every UDP stream is discarded before reconnecting and stale mappings are not reused

#### Scenario: A discarded datagram is visible
- **WHEN** the pending datagrams of a peer are finally discarded because no usable Link appeared
- **THEN** the forward's dropped counter grows by the number of datagrams
- **AND** the activity log shows a translated line naming the reason
- **AND** no payload bytes appear in the log or the notification
