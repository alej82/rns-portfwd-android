## Purpose

Lets the Android client forward services that the server publishes without a
password, without inventing a placeholder secret, and lets the UI show which
services need a password before the user connects.

## ADDED Requirements

### Requirement: Passwordless forward definitions

A forward MAY have no password. The absence of a password SHALL mean "public
service" and MUST be represented distinctly from an empty string in the
in-memory model and in the stored profile. A forward with no password MUST send
an empty credential in `OPEN` and MUST NOT require the user to type anything.

#### Scenario: Public forward connects
- **WHEN** the user adds a public forward for a service the server exposes without a password
- **THEN** the client opens streams for it using an empty credential and never prompts for a password

#### Scenario: Public and private forwards on one profile
- **WHEN** a profile mixes a public forward and a password-protected forward
- **THEN** both connect over the same Link and each stream sends its own credential

#### Scenario: Missing password is not an empty secret
- **WHEN** a public forward is configured alongside password-protected forwards
- **THEN** no empty or placeholder secret is registered for redaction or stored in the profile

### Requirement: Probing without a password

Probing a forward without a password MUST send an empty credential and MUST
succeed only when the service is public. When the service still requires a
password the client MUST report a translated authentication error that hints at
missing credentials, and MUST NOT retry in a loop.

#### Scenario: Probe a public service
- **WHEN** the user probes a public forward that has no password
- **THEN** the probe succeeds and shows the service metadata

#### Scenario: Probe a private service without a password
- **WHEN** the user probes a forward without a password against a private service
- **THEN** the probe fails with the authentication error and the UI suggests setting a password

#### Scenario: Authentication failure is terminal
- **WHEN** a session start fails because a configured forward lacks a required password
- **THEN** the session enters the error state instead of retrying the connection

### Requirement: Profile schema keeps working

Stored profiles SHALL record each forward's protocol and whether it has a
password. Reading a profile written by an earlier version MUST default a missing
protocol to `tcp` and keep its existing password. Writing profiles MUST keep
file permissions at `0600`.

#### Scenario: Legacy profile is loaded
- **WHEN** the app reads a profile whose forwards have no protocol field
- **THEN** every forward loads as `tcp` with its stored password intact

#### Scenario: Public forward is persisted
- **WHEN** a public UDP forward is saved in a profile and the app restarts
- **THEN** the forward reloads as public, over UDP, on the same local port

#### Scenario: Profile file stays private
- **WHEN** a profile containing a private password is saved
- **THEN** the file is written with mode `0600` and no password appears in logs or notifications

### Requirement: Service list reporting

The service list SHALL show each service's protocol and whether it requires a
password, using translated labels. The list MUST NOT be the only way to know
whether a service is public: when a listed service is public, adding a forward
for it MUST be possible without a password.

#### Scenario: Listing shows protocol and authentication
- **WHEN** the user lists services with the correct list password
- **THEN** each row shows its protocol (`tcp` or `udp`) and a translated "password required" or "no password" label

#### Scenario: Public service picked from the list
- **WHEN** the user picks a public service from the list
- **THEN** the created forward is marked public and needs no password

#### Scenario: Empty list of services
- **WHEN** the server has no exposed services
- **THEN** the list shows the translated empty state without inventing rows

### Requirement: Passwordless forwards in the UI

The forward editor SHALL let the user choose the protocol and mark the forward
as public, in which case the password field is hidden and not validated. A
private forward MUST still require a non-empty password. The forward row and the
session view SHALL identify the protocol and the public state without ever
showing the stored password.

#### Scenario: Editing a public UDP forward
- **WHEN** the user edits a profile with a public UDP forward
- **THEN** the editor shows UDP selected, the public switch on and no password value

#### Scenario: Switching a forward back to private
- **WHEN** the user turns off the public switch and saves without typing a password
- **THEN** the editor refuses to save and shows the translated password error

#### Scenario: Password is never displayed
- **WHEN** the user opens a saved private forward in the editor
- **THEN** the password field is masked and the forward row shows no secret text
