## Purpose

Keeps a listening session's Link usable long enough for the first forward,
despite servers that expire a Link which never opened an application stream.

## ADDED Requirements

### Requirement: An unused Link is refreshed before its first stream

Before handing a Link to a local connection, the session SHALL rebuild it when
it has never carried a stream and is older than the configured idle threshold.
The local connection MUST keep waiting for the replacement Link instead of
being refused, and a Link that already carried a stream MUST NOT be refreshed.

#### Scenario: First forward arrives after the server's auth timeout

- **WHEN** the session has been listening for longer than the idle threshold
  without receiving any local connection and the server would have expired the
  Link
- **THEN** the session tears the Link down, rebuilds it, and serves the
  connection on the new Link

#### Scenario: Reused Link is never refreshed

- **WHEN** a Link has already carried a stream and a new local connection
  arrives much later
- **THEN** the existing Link is used as-is

#### Scenario: Concurrent connections race the refresh

- **WHEN** several local connections arrive while the Link is being refreshed
- **THEN** exactly one refresh is triggered and every connection waits for the
  replacement Link
