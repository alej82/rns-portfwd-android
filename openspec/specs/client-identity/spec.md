# client-identity Specification

## Purpose
Lets the Android client present a persistent Reticulum identity to servers that
restrict services to an allow-list of identity hashes, while staying anonymous
everywhere the user has not opted in.
## Requirements
### Requirement: Identifying is opt-in per profile

The app SHALL keep the client anonymous unless the stored profile has
identifying enabled. A profile MUST carry its own identifying preference, and
the preference MUST default to disabled for new profiles and for profile files
written by earlier versions.

#### Scenario: Anonymous by default

- **WHEN** the user connects with a profile that has never enabled identifying
- **THEN** the app establishes the Link without sending any identity proof and
  the server sees an anonymous client

#### Scenario: Existing profile files stay anonymous

- **WHEN** the app loads a `profiles.json` that predates this change
- **THEN** every profile is treated as anonymous and no identity is sent

#### Scenario: Enabled profile identifies

- **WHEN** the user enables identifying for a profile and connects
- **THEN** the app sends its identity on the Link before any probe or `OPEN`
  traffic for that session

### Requirement: One device identity, created on demand

The app SHALL keep a single Reticulum identity for the whole device in its
private sandbox, readable only by the app, and SHALL create it the first time it
is needed. Every profile SHALL use that same identity.

#### Scenario: Created on first use

- **WHEN** identifying is used and no identity file exists yet
- **THEN** the app creates the identity inside its sandbox with owner-only
  permissions and reuses it on later sessions

#### Scenario: Survives restarts

- **WHEN** the app is restarted and a profile with identifying enabled connects
- **THEN** the same identity hash is presented as before

#### Scenario: Sandbox wipe

- **WHEN** the app data is cleared and identifying is used again
- **THEN** a new identity is created and the previously allow-listed hash no
  longer applies until it is allow-listed again

### Requirement: The identity hash can be handed to a server operator

The app SHALL show the device identity hash as 32 hexadecimal characters and
SHALL let the user copy it, so it can be added to a server allow-list. The
private key material MUST never be displayed.

#### Scenario: Show and copy the hash

- **WHEN** the user opens the identity panel
- **THEN** the app shows the 32-character hash and an action that copies it to
  the clipboard

#### Scenario: No key material exposed

- **WHEN** the identity panel, logs or notifications are inspected
- **THEN** only the public hash appears and the private key never does

### Requirement: Identification precedes application traffic

When identifying is enabled, the app SHALL identify the Link as soon as it is
established, before it sends a probe, an `OPEN` or any stream data, and SHALL
repeat this on every reconnect.

#### Scenario: First connection

- **WHEN** the session connects with identifying enabled
- **THEN** the identity proof is sent before the first `/probe` request

#### Scenario: Reconnect

- **WHEN** the Link is lost and the session re-establishes it
- **THEN** the new Link is identified again before probing

### Requirement: An identification race does not fail the session

If a probe is answered with an authentication failure while identifying is
enabled, the app SHALL re-identify the Link and retry the probe a bounded number
of times before treating it as an authentication failure.

#### Scenario: Proof arrives after the probe

- **WHEN** the server answers the first probe with an authentication failure and
  the identity proof arrives shortly after
- **THEN** the app retries the probe and the session continues

#### Scenario: Genuine authentication failure

- **WHEN** the identity is not allow-listed or the password is wrong
- **THEN** the retries are exhausted and the session ends with a translated
  authentication error, not an endless retry loop

