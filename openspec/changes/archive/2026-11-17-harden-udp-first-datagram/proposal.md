## Why

On the device pass of `add-udp-and-public-forwards` the first datagram of a UDP
forward was lost when it happened to arrive while the session was rebuilding an
idle Link: the counters stayed at `0/0`, the activity log said nothing, and the
only trace was a `debug` line on the host. The cause is that
`Session._open_udp_session` drops the pending datagram on any failure of its
setup path (including "the Link is being rebuilt") without counting it, and
that the datagram which triggers the rebuild has to pay for the whole rebuild
before it can leave the device.

## What Changes

- **A rebuild no longer swallows the datagram.** A reconnect that the refresh
  itself triggers must not discard the datagrams still waiting for a Link, and
  when the setup of their stream fails because the Link was unavailable or was
  being rebuilt, they are retried once against the replacement Link instead of
  being thrown away. The retry is bounded: one attempt, and only for
  link-level failures — never for a rejected open, an authentication failure or
  a protocol mismatch, which stay terminal or are reported as they are today.
- **No silent loss.** Every pending datagram that is finally discarded
  increments the forward's `dropped` counter and publishes a
  `stream_rejected` event, which the UI renders as a translated activity line
  naming the reason, so `0/0` is never left unexplained.
- Everything else stays: the buffer bounds (32 datagrams, at most
  `max_streams` pending peers), the drop-instead-of-block rule, the idle
  timeout, and the lazy refresh of an unused Link before its first stream.

## Non-goals

- No proactive or timer-driven Link refresh: sessions that nobody uses stay
  silent, which is what keeps them cheap on a phone.
- No change to the wire protocol, the vendor tree or the datagram stream
  semantics.
- No user-visible retransmission of datagrams the remote never answered: only
  the local setup path is retried, once.
- The buffer sizes, the idle timeout and the stream limit are unchanged.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `udp-forwarding`: the buffering requirement now says the datagrams of a peer
  survive the rebuild of the Link that opens their stream, and the limits
  requirement now requires every dropped datagram to be counted and reported
  with a reason.

## Impact

- Code: `app/portfwd_android/session.py` (`_open_udp_session`,
  `_handle_udp_datagram`, `_record_reject`), `app/portfwd_android/messages.py`
  or `app/main.py` for the translated activity line, and
  `app/assets/i18n/{translations,es}.json` if a new string is needed.
- Tests: `tests/unit/test_session.py` for the retry and the counters,
  `tests/unit/test_messages.py` if the reason needs a translated text, and the
  existing UDP integration tests keep covering the round trip.
- No vendor change, no new dependency, no new permission.
