## Purpose

Lets the Android client establish Links to servers that require a Reticulum
link-request proof-of-work stamp, choosing the cost automatically from what the
server advertises while keeping an explicit advanced override.

## ADDED Requirements

### Requirement: Automatic stamping from the advertised cost

By default a profile SHALL use the link-request stamp cost the server
advertises, and SHALL apply it to every Link the app creates for that server:
the session Link, reconnects, listing and probing. When the server advertises
no cost, no stamp SHALL be generated.

#### Scenario: Server advertises a cost

- **WHEN** the server's announce carries a positive stamp cost and the profile
  uses the automatic mode
- **THEN** every Link to that server is created with a stamp of at least that
  cost

#### Scenario: Server requires no stamp

- **WHEN** the server advertises no stamp cost
- **THEN** the app creates Links without stamps and the session behaves as
  before this change

#### Scenario: Applied to discovery operations

- **WHEN** the user lists services or probes a forward against a stamping server
- **THEN** those Links are stamped too, and the operations succeed

#### Scenario: Reconnect after an outage

- **WHEN** the Link is lost and the session re-establishes it
- **THEN** the stamp cost is re-applied for that attempt

### Requirement: Advanced per-profile stamp controls

A profile SHALL offer three stamp modes: automatic, disabled and a custom cost.
A custom cost MUST be a finite number between 0 and 40, and the app MUST reject
invalid values before connecting.

#### Scenario: Disabled mode

- **WHEN** the user selects the disabled mode and connects to a server that
  requires stamps
- **THEN** the app does not stamp and reports the resulting failure instead of
  silently retrying forever

#### Scenario: Custom cost

- **WHEN** the user sets a custom cost within range
- **THEN** the app uses that cost even if the announcement is missing or
  different

#### Scenario: Invalid custom cost

- **WHEN** the user enters a negative, non-numeric or over-range cost
- **THEN** the app shows a translated configuration error and does not start the
  session

#### Scenario: Persisted per profile

- **WHEN** the user saves a profile with a stamp mode and reopens it
- **THEN** the mode and any custom cost are restored

### Requirement: An incapable local Reticulum fails clearly

If stamping is required but the local Reticulum cannot generate stamps, the app
MUST stop with a terminal, translated error naming the limitation, rather than
retrying the connection indefinitely.

#### Scenario: Local Reticulum without stamp support

- **WHEN** the server requires stamps and the bundled Reticulum has no stamp
  support
- **THEN** the session ends with a `stamp_unsupported` error that is shown
  translated and is not retried

#### Scenario: No error when stamping is not needed

- **WHEN** the server advertises no cost and the local Reticulum lacks stamp
  support
- **THEN** the session connects normally

### Requirement: Stamping does not block the interface

Stamp generation SHALL run without blocking the app's user interface, and the
session SHALL remain in its normal connecting state while the stamp is
generated.

#### Scenario: Slow stamp generation

- **WHEN** stamp generation takes several seconds on the device
- **THEN** the interface stays responsive and the session reports that it is
  still connecting

### Requirement: Wire compatibility is preserved

Enabling identity or stamping MUST NOT change the application protocol version
or the message layout, and MUST NOT bypass the server's password or allow-list
checks.

#### Scenario: Existing servers keep working

- **WHEN** the app connects to a server that predates identity allow-lists and
  stamping
- **THEN** the session works exactly as before

#### Scenario: Stamped client against a password service

- **WHEN** a stamping server also requires a password
- **THEN** the stamp satisfies the link policy but the password is still
  required for the service
