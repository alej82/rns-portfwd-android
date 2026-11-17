## Purpose

Lets the Android client establish Links to servers that require a Reticulum
link-request proof-of-work stamp, choosing the cost automatically from what the
server advertises while keeping an explicit advanced override.

## MODIFIED Requirements

### Requirement: A custom stamp cost stays within a usable range

The advanced override SHALL accept only costs the device can compute in a
practical time, and the UI SHALL advertise that bound instead of the vendor
maximum. A cost above the app bound MUST be refused with the translated
`stamp_cost` field error, and a stored profile carrying one MUST fall back to
automatic stamping rather than failing to load.

#### Scenario: Custom cost above the app bound

- **WHEN** the user enters 40 with «Custom cost» selected and connects
- **THEN** the app refuses with «The stamp cost must be a number between 0 and
  16.» and no Link is created
- **AND** the field and its hint read «Cost (0-16)»

#### Scenario: Stored profile carries an out-of-range cost

- **WHEN** `profiles.json` contains `stamp_mode: "cost"` with
  `stamp_cost: 40.0`
- **THEN** the profile loads with automatic stamping and stays usable

### Requirement: Stamp generation is reported while the Link opens

While a link-request stamp is being computed the session SHALL publish a
`stamping` state carrying the effective cost, so the UI can show progress
instead of an indeterminate "Opening Link…". The reported cost MUST be the one
the stamping configuration resolved, including the server's advertised cost for
automatic profiles, and no stamping state SHALL be published when the cost is
zero.

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
