"""What the API is allowed to say about why a fetch failed.

Internal reasons (which private range a host resolved to, whether DNS failed, which port was
refused) would let an API user map the internal network by choosing redirect targets on a page
they control. So network-policy reasons collapse into one coarse label. The stored fetch_status
is still returned, as the spec requires.
"""

_POLICY = {
    "blocked_ip",
    "blocked_host",
    "bad_scheme",
    "credentials_in_url",
    "port_not_allowed",
    "dns_failure",
    "invalid_url",
}


def public_reason(reason: str) -> str:
    return "blocked_by_policy" if reason in _POLICY else reason
