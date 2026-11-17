## Purpose

Lets the Android client establish Links to servers that require a Reticulum
link-request proof-of-work stamp, choosing the cost automatically from what the
server advertises while keeping an explicit advanced override.

## MODIFIED Requirements

### Requirement: Advanced per-profile stamp controls

A profile SHALL offer three stamp modes: automatic, disabled and a custom cost.
A custom cost MUST be a finite number between 0 and the app bound of 16, and the
app MUST reject invalid values before connecting. The label, the hint and the
error SHALL advertise that bound instead of the vendor maximum of 40, and a
stored profile carrying a higher custom cost MUST fall back to automatic
stamping rather than failing to load.

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

#### Scenario: Custom cost above the app bound

- **WHEN** the user enters 40 with «Custom cost» selected and connects
- **THEN** the app refuses with «The stamp cost must be a number between 0 and
  16.» and no Link is created
- **AND** the field and its hint read «Cost (0-16)»

#### Scenario: Stored profile carries an out-of-range cost

- **WHEN** `profiles.json` contains `stamp_mode: "cost"` with
  `stamp_cost: 40.0`
- **THEN** the profile loads with automatic stamping and stays usable

#### Scenario: Persisted per profile

- **WHEN** the user saves a profile with a stamp mode and reopens it
- **THEN** the mode and any custom cost are restored

### Requirement: Stamping does not block the interface

Stamp generation SHALL run without blocking the app's user interface. While a
link-request stamp is being computed the session SHALL publish a `stamping`
state carrying the effective cost, so the UI can show progress instead of an
indeterminate "Opening Link…". The reported cost MUST be the one the stamping
configuration resolved, including the server's advertised cost for automatic
profiles, and no stamping state SHALL be published when the cost is zero.

#### Scenario: Slow stamp generation

- **WHEN** stamp generation takes several seconds on the device
- **THEN** the interface stays responsive and the session reports that it is
  still connecting

#### Scenario: Custom cost of 8

- **WHEN** the session opens a Link with `stamp_mode: "cost"` and
  `stamp_cost: 8`
- **THEN** the session publishes `state=stamping` with `stamp_cost=8` before the
  Link is created, and the connect screen shows «Generating link stamp
  (cost 8)…»

#### Scenario: Automatic mode on a server that advertises no cost

- **WHEN** the server's announce carries no `stamp_cost`
- **THEN** no `stamping` state is published and the session goes straight to
  opening the Link
