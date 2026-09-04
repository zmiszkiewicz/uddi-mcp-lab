#!/usr/bin/env python3
"""
The deterministic misconfigurations. One function per break, plus one assertion
per break.

Contract every break honours:

  * Independently callable.        seed_lab.py --break dhcp_range_overlap
  * Idempotent.                    Applying twice leaves the same state.
  * Self-asserting.                apply() is followed by assert_applied(), which
                                   re-reads the tenant and returns (bool, reason).

A break that applies but does not assert is worse than no break at all — the
learner spends fifteen minutes hunting a fault that is not there. seed_lab.py
therefore exits non-zero on any failed assertion, which turns into a red
"setup failed" screen instead of a confusing lab.

  Challenge 3  dns_profile_detach     DNS config profile detached from branch host
  Challenge 3  dns_stale_forward       Forward zone -> decommissioned resolver
  Challenge 3  dhcp_range_overlap      Branch-02 range narrowed onto reserved block
  Challenge 5  view_zone_association    Zone bound to the wrong DNS view (unguided)

The first two are the two halves of one story: the zone was updated during the
cutover, but the profile carrying it never landed on the host, and a stale
forward zone still points at a resolver that was decommissioned. Together they
produce the intermittent SERVFAIL/NXDOMAIN of INC-4471. They are separate
functions because a learner can fix one without the other and the check has to
be able to say which.
"""

import lab_config as cfg
from csp_client import info, ok


# --------------------------------------------------------------------------- #
# Break 1 (Challenge 3, DNS) — profile never attached to the branch host
# --------------------------------------------------------------------------- #

def break_dns_profile_detach(client, ids):
    """
    Detach the DNS config profile carrying techcorp.internal from the NIOS-X
    host serving the branch network.

    This is what `get_host_config` surfaces: the effective configuration on the
    host has no profile, so the zone it is supposed to serve simply is not
    there. Clients that happen to resolve via another host succeed, which is
    exactly why the ticket says "intermittent".
    """
    # The host owns the association: a Host carries `server`, naming the config
    # profile attached to it. Clearing it is the break.
    client.patch(cfg.path("dns_host") + f"/{ids['host_id']}", json_body={
        "server": None,
    })
    ok("break applied: DNS config profile detached from " + cfg.BRANCH_HOST_NAME)


def assert_dns_profile_detach(client, ids):
    host = client.get(cfg.path("dns_host") + f"/{ids['host_id']}")
    host = host.get("result", host)
    attached = host.get("server")
    if attached:
        return False, (
            f"expected no DNS config profile on {cfg.BRANCH_HOST_NAME}, "
            f"found {attached!r}"
        )
    return True, f"{cfg.BRANCH_HOST_NAME} has no DNS config profile attached"


def fix_dns_profile_detach(client, ids):
    """The remediation, for solve-shell and for reset between runs."""
    client.patch(cfg.path("dns_host") + f"/{ids['host_id']}", json_body={
        "server": ids["profile_id"],
    })
    ok("reattached DNS config profile to " + cfg.BRANCH_HOST_NAME)


# --------------------------------------------------------------------------- #
# Break 2 (Challenge 3, DNS) — stale forward zone
# --------------------------------------------------------------------------- #

def break_dns_stale_forward(client, ids):
    """
    Point the legacy forward zone at a resolver that was decommissioned during
    the cutover. Queries that land on it time out rather than fail fast, which
    is the other half of the "intermittent" symptom.
    """
    client.patch(cfg.path("dns_forward_zone") + f"/{ids['forward_zone_id']}",
                 json_body={
                     "external_forwarders": [
                         {"address": cfg.FORWARD_RESOLVER_DECOMMISSIONED}
                     ],
                 })
    ok(f"break applied: {cfg.FORWARD_ZONE_FQDN} -> "
       f"{cfg.FORWARD_RESOLVER_DECOMMISSIONED} (decommissioned)")


def assert_dns_stale_forward(client, ids):
    zone = client.get(cfg.path("dns_forward_zone") + f"/{ids['forward_zone_id']}")
    zone = zone.get("result", zone)
    forwarders = [f.get("address") for f in zone.get("external_forwarders", [])]
    if cfg.FORWARD_RESOLVER_DECOMMISSIONED not in forwarders:
        return False, (
            f"expected {cfg.FORWARD_ZONE_FQDN} to forward to "
            f"{cfg.FORWARD_RESOLVER_DECOMMISSIONED}, found {forwarders}"
        )
    return True, f"{cfg.FORWARD_ZONE_FQDN} forwards to a decommissioned resolver"


def fix_dns_stale_forward(client, ids):
    client.patch(cfg.path("dns_forward_zone") + f"/{ids['forward_zone_id']}",
                 json_body={
                     "external_forwarders": [
                         {"address": cfg.FORWARD_RESOLVER_GOOD}
                     ],
                 })
    ok(f"repointed {cfg.FORWARD_ZONE_FQDN} at {cfg.FORWARD_RESOLVER_GOOD}")


# --------------------------------------------------------------------------- #
# Break 3 (Challenge 3, DHCP/IPAM) — range collides with the reserved block
# --------------------------------------------------------------------------- #

def break_dhcp_range_overlap(client, ids):
    """
    Narrow the Branch-02 DHCP range from .100-.200 down to .10-.30, which is
    precisely the reserved fixed-address block.

    Result: the range exists, DHCP is "configured", utilization reads at or near
    100%, and no client can get a lease. INC-4472.
    """
    broken = cfg.BRANCH_RANGE_BROKEN
    client.patch(cfg.path("dhcp_range") + f"/{ids['range_id']}", json_body={
        "start": broken["start"],
        "end": broken["end"],
    })
    ok(f"break applied: Branch-02 range narrowed to "
       f"{broken['start']}-{broken['end']} (collides with reserved block)")


def assert_dhcp_range_overlap(client, ids):
    dhcp_range = client.get(cfg.path("dhcp_range") + f"/{ids['range_id']}")
    dhcp_range = dhcp_range.get("result", dhcp_range)
    start, end = dhcp_range.get("start"), dhcp_range.get("end")

    if (start, end) != (cfg.BRANCH_RANGE_BROKEN["start"],
                        cfg.BRANCH_RANGE_BROKEN["end"]):
        return False, (
            f"expected Branch-02 range {cfg.BRANCH_RANGE_BROKEN['start']}-"
            f"{cfg.BRANCH_RANGE_BROKEN['end']}, found {start}-{end}"
        )

    if not ranges_overlap(start, end,
                          cfg.BRANCH_RESERVED_BLOCK["start"],
                          cfg.BRANCH_RESERVED_BLOCK["end"]):
        return False, (
            "the range was narrowed but does not overlap the reserved block — "
            "the DHCP symptom will not reproduce"
        )

    return True, "Branch-02 DHCP range overlaps the reserved fixed-address block"


def fix_dhcp_range_overlap(client, ids):
    healthy = cfg.BRANCH_RANGE_HEALTHY
    client.patch(cfg.path("dhcp_range") + f"/{ids['range_id']}", json_body={
        "start": healthy["start"],
        "end": healthy["end"],
    })
    ok(f"restored Branch-02 range to {healthy['start']}-{healthy['end']}")


# --------------------------------------------------------------------------- #
# Break 4 (Challenge 5, unguided) — zone bound to the wrong view
# --------------------------------------------------------------------------- #

def break_view_zone_association(client, ids):
    """
    Move partners.techcorp.internal out of techcorp-view and into the legacy
    view, where nothing serving the branch network will ever look for it.

    Deliberately NOT explained anywhere in assignment prose. Challenge 5 asks
    the learner to find it with no scaffolding, which is the best assessment
    signal in the track.
    """
    # `view` is not read-only on an auth zone (unlike fqdn and primary_type),
    # so a PATCH moves it.
    client.patch(cfg.path("dns_auth_zone") + f"/{ids['c5_zone_id']}", json_body={
        "view": ids["c5_decoy_view_id"],
    })
    ok(f"break applied: {cfg.C5_ZONE_FQDN} moved into "
       f"{cfg.DECOY_DNS_VIEW_NAME}")


def assert_view_zone_association(client, ids):
    zone = client.get(cfg.path("dns_auth_zone") + f"/{ids['c5_zone_id']}")
    zone = zone.get("result", zone)
    if zone.get("view") != ids["c5_decoy_view_id"]:
        return False, (
            f"expected {cfg.C5_ZONE_FQDN} in view {cfg.DECOY_DNS_VIEW_NAME}, "
            f"found view {zone.get('view')!r}"
        )
    return True, f"{cfg.C5_ZONE_FQDN} is bound to the wrong DNS view"


def fix_view_zone_association(client, ids):
    client.patch(cfg.path("dns_auth_zone") + f"/{ids['c5_zone_id']}", json_body={
        "view": ids["c5_correct_view_id"],
    })
    ok(f"moved {cfg.C5_ZONE_FQDN} back into {cfg.DNS_VIEW_NAME}")


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

BREAKS = {
    "dns_profile_detach": {
        "challenge": 3,
        "summary": "DNS config profile detached from the branch NIOS-X host",
        "apply": break_dns_profile_detach,
        "assert": assert_dns_profile_detach,
        "fix": fix_dns_profile_detach,
    },
    "dns_stale_forward": {
        "challenge": 3,
        "summary": "Forward zone points at a decommissioned resolver",
        "apply": break_dns_stale_forward,
        "assert": assert_dns_stale_forward,
        "fix": fix_dns_stale_forward,
    },
    "dhcp_range_overlap": {
        "challenge": 3,
        "summary": "Branch-02 DHCP range collides with the reserved block",
        "apply": break_dhcp_range_overlap,
        "assert": assert_dhcp_range_overlap,
        "fix": fix_dhcp_range_overlap,
    },
    "view_zone_association": {
        "challenge": 5,
        "summary": "partners zone bound to the wrong DNS view (unguided)",
        "apply": break_view_zone_association,
        "assert": assert_view_zone_association,
        "fix": fix_view_zone_association,
    },
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def ip_to_int(addr):
    octets = [int(part) for part in addr.split(".")]
    return (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]


def ranges_overlap(a_start, a_end, b_start, b_end):
    """Inclusive overlap test on two IPv4 ranges."""
    return (ip_to_int(a_start) <= ip_to_int(b_end)
            and ip_to_int(b_start) <= ip_to_int(a_end))


def apply_break(client, name, ids):
    """Apply one break and assert it landed. Returns (bool, reason)."""
    spec = BREAKS[name]
    info(f"applying break {name}: {spec['summary']}")
    spec["apply"](client, ids)
    return spec["assert"](client, ids)
