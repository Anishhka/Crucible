"""
The single network transport chokepoint (CONTRACT.md §7).

Every outbound connection this program could ever make goes through
`reachable()`. That is deliberate and it is the requirement: offline enforcement
"MUST live at a single transport chokepoint, not be sprinkled across call
sites", because a check repeated at ten call sites is a check missing from the
eleventh.

Two rules, both enforced here:

  * **`--offline` is fail-closed.** Not attempted-and-caught: *not attempted*.
    `guard()` raises before a socket is created, so an offline run cannot open
    one even by mistake.

  * **Every request carries an explicit connect and read timeout.** A request
    with no timeout is an unbounded hang inside a batch, and a batch that hangs
    is a batch nobody sees the result of.

**What online mode means for this build, stated plainly.** There is no live
evidence provider implemented here -- all prior art comes from committed
corpora. What the absence of `--offline` means is therefore "consult live
sources as well", and this build cannot honour that request without a network.
So online mode probes reachability at this chokepoint:

  * network unreachable  -> the request could not be honoured, the live provider
    is recorded `unavailable`, and the run is a degraded success (exit 4). It is
    never a clean exit 0, because a clean exit 0 would claim the live sources
    were consulted.
  * network reachable    -> there is still no live provider to consult, which is
    a *planned* degradation and is recorded as
    `VERIFY.DEGRADED.PROVIDER_SKIPPED` rather than as an error.

The distinction between those two is the whole point: one is "we could not look",
the other is "we chose not to look, and here is why".
"""

from __future__ import annotations

import socket
from dataclasses import dataclass


class OfflineViolation(RuntimeError):
    """Raised when something tries to open a socket during an offline run.

    This is a programming error, not a runtime condition: reaching it means a
    code path bypassed the offline check rather than that the network failed.
    """


@dataclass
class Reachability:
    reachable: bool
    endpoint: str | None
    detail: str


_OFFLINE = False


def set_offline(offline: bool) -> None:
    """Arm or disarm the chokepoint. Called once, from the pipeline entry."""
    global _OFFLINE
    _OFFLINE = bool(offline)


def is_offline() -> bool:
    return _OFFLINE


def guard(what: str) -> None:
    """Refuse a network operation during an offline run."""
    if _OFFLINE:
        raise OfflineViolation(
            f"offline run attempted a network operation ({what}). --offline is "
            f"fail-closed: the connection is not attempted, not attempted-and-caught."
        )


def reachable(endpoints: list[str], connect_timeout: float,
              read_timeout: float) -> Reachability:
    """Can any configured endpoint be reached?

    Returns rather than raises: an unreachable network is an ordinary runtime
    condition that the caller records as `unavailable`, not an error that should
    stop the run. The run must still complete and produce a full artifact set.
    """
    guard("reachability probe")

    if not endpoints:
        return Reachability(False, None,
                            "No live endpoint is configured, so no probe was made.")

    for endpoint in endpoints:
        host, _, port_text = endpoint.rpartition(":")
        if not host:
            host, port = endpoint, 443
        else:
            try:
                port = int(port_text)
            except ValueError:
                host, port = endpoint, 443

        connection = None
        try:
            # Explicit connect timeout. socket.create_connection applies it to
            # the connect phase; the read timeout is set separately on the
            # returned socket so both bounds are stated rather than defaulted.
            connection = socket.create_connection((host, port), timeout=connect_timeout)
            connection.settimeout(read_timeout)
            return Reachability(
                True, endpoint,
                f"Reached {endpoint} within a {connect_timeout}s connect timeout.")
        except (OSError, socket.gaierror, socket.timeout) as exc:
            last = f"{type(exc).__name__}: {exc}"
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass

    return Reachability(
        False, None,
        f"No configured endpoint could be reached (last error: {last}). Live "
        f"evidence was requested by the absence of --offline and could not be "
        f"obtained.")
