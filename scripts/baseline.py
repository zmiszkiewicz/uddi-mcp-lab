#!/usr/bin/env python3
"""
Build the healthy TechCorp footprint the track breaks in Challenge 3.

Every function here is idempotent: it looks the object up by name first and
returns the existing one rather than creating a duplicate. seed_lab.py may be
re-run against a half-seeded tenant after a failed setup, and must converge.

Everything created is tagged {instruqt-lab: <track slug>} so teardown_lab.py can
identify its own objects and leave anything else in the tenant alone.

Paths and payloads are taken from Infoblox's own OpenAPI-generated Go client
(github.com/infobloxopen/universal-ddi-go-client), not guessed. Two of its
findings changed the shape of this file and are worth knowing before editing:

  * A DHCP range hangs off `space` + `parent`, not a `subnet` field.
  * A fixed address reserves exactly ONE address, so the "reserved block" is N
    objects rather than one object with bounds.
"""

import lab_config as cfg
from csp_client import info, ok


LAB_TAGS = {cfg.LAB_TAG_KEY: cfg.LAB_TAG_VALUE}


# --------------------------------------------------------------------------- #
# DNS
# --------------------------------------------------------------------------- #

def ensure_dns_view(client, name=None):
    """
    The DNS view holding techcorp.internal.

    /api/ddi/v1/dns/view is confirmed — it is read all over this estate — so
    this function is complete apart from the create body.
    """
    name = name or cfg.DNS_VIEW_NAME
    existing = client.find_by_name(cfg.path("dns_view"), name)
    if existing:
        info(f"DNS view {name} already present")
        return existing

    created = client.post(cfg.path("dns_view"), json_body={
        "name": name,
        "comment": "TechCorp production DNS view (Instruqt lab)",
        "tags": LAB_TAGS,
    })
    ok(f"created DNS view {name}")
    return created.get("result", created)


def ensure_auth_zone(client, view_id, fqdn=None):
    """The authoritative zone updated during the cutover."""
    fqdn = fqdn or cfg.ZONE_FQDN
    existing = client.find_by_name(cfg.path("dns_auth_zone"), fqdn, field="fqdn")
    if existing:
        info(f"auth zone {fqdn} already present")
        return existing

    # primary_type "cloud" = zone data owned by a Universal DDI host, which is
    # what a NIOS-X host serving the branch network needs. Both fqdn and
    # primary_type are read-only after creation.
    created = client.post(cfg.path("dns_auth_zone"), json_body={
        "fqdn": fqdn,
        "primary_type": "cloud",
        "view": view_id,
        "comment": "TechCorp internal zone",
        "tags": LAB_TAGS,
    })
    ok(f"created auth zone {fqdn}")
    return created.get("result", created)


def ensure_records(client, zone_id):
    """
    A and CNAME records inside techcorp.internal, including app.techcorp.internal
    — the name Challenge 2's starter prompt asks about and Challenge 3's ticket
    reports failing.
    """
    created = []

    for label, spec in cfg.BASELINE_A_RECORDS.items():
        created.append(_ensure_record(client, zone_id, label, "A", {
            "address": spec["address"],
        }, spec["comment"]))

    for label, spec in cfg.BASELINE_CNAME_RECORDS.items():
        # CNAME rdata is keyed `cname`, not `target`.
        created.append(_ensure_record(client, zone_id, label, "CNAME", {
            "cname": spec["target"],
        }, spec["comment"]))

    ok(f"{len(created)} records present in {cfg.ZONE_FQDN}")
    return created


def _ensure_record(client, zone_id, name_in_zone, rtype, rdata, comment):
    params = {"_filter": f'zone=="{zone_id}" and name_in_zone=="{name_in_zone}"'}
    for row in client.list_results(cfg.path("dns_record"), params=params):
        if row.get("type") == rtype:
            return row

    created = client.post(cfg.path("dns_record"), json_body={
        "zone": zone_id,
        "name_in_zone": name_in_zone,
        "type": rtype,
        "rdata": rdata,
        "comment": comment,
        "tags": LAB_TAGS,
    })
    return created.get("result", created)


def ensure_forward_zone(client, view_id, resolver=None):
    """
    The forward zone that the Challenge 3 break repoints at a decommissioned
    resolver. Baseline points it somewhere that answers.
    """
    resolver = resolver or cfg.FORWARD_RESOLVER_GOOD
    existing = client.find_by_name(
        cfg.path("dns_forward_zone"), cfg.FORWARD_ZONE_FQDN, field="fqdn"
    )
    if existing:
        info(f"forward zone {cfg.FORWARD_ZONE_FQDN} already present")
        return existing

    created = client.post(cfg.path("dns_forward_zone"), json_body={
        "fqdn": cfg.FORWARD_ZONE_FQDN,
        "view": view_id,
        "external_forwarders": [{"address": resolver}],
        "comment": "Legacy forward zone retained through the cutover",
        "tags": LAB_TAGS,
    })
    ok(f"created forward zone {cfg.FORWARD_ZONE_FQDN} -> {resolver}")
    return created.get("result", created)


def ensure_dns_config_profile(client, zone_id):
    """
    The DNS config profile that carries techcorp.internal and is supposed to be
    attached to the branch NIOS-X host. Detaching it is break #1.
    """
    existing = client.find_by_name(
        cfg.path("dns_config_profile"), cfg.DNS_CONFIG_PROFILE_NAME
    )
    if existing:
        info(f"DNS config profile {cfg.DNS_CONFIG_PROFILE_NAME} already present")
        return existing

    created = client.post(cfg.path("dns_config_profile"), json_body={
        "name": cfg.DNS_CONFIG_PROFILE_NAME,
        "comment": "Serves techcorp.internal to the branch networks",
        "tags": LAB_TAGS,
    })
    ok(f"created DNS config profile {cfg.DNS_CONFIG_PROFILE_NAME}")
    return created.get("result", created)


def attach_profile_to_host(client, profile_id, host_id):
    """
    Associate the DNS config profile with the branch NIOS-X host.

    The HOST owns this relationship — a Host object carries a `server` field
    naming the config profile attached to it. break_dns_profile_detach() and
    verify_lab.check_c3() read and write this same field, which is what stops a
    learner "fixing" it in the Portal and still failing the check.
    """
    client.patch(cfg.path("dns_host") + f"/{host_id}", json_body={
        "server": profile_id,
    })
    ok(f"attached {cfg.DNS_CONFIG_PROFILE_NAME} to {cfg.BRANCH_HOST_NAME}")


def find_branch_host(client):
    """
    The NIOS-X (or NIOS-X-as-a-Service) host serving the branch network.

    Not created here — the host is part of the sandbox the broker hands us, or
    it is stood up by the traffic-generator work (see traffic/README.md). If it
    is absent the whole track is meaningless, so this raises rather than
    quietly continuing.
    """
    host = (client.find_by_name(cfg.path("dns_host"), cfg.BRANCH_HOST_NAME)
            or client.find_by_name(cfg.path("dns_host"), cfg.BRANCH_HOST_NAME,
                                   field="absolute_name"))
    if not host:
        raise SystemExit(
            f"❌ NIOS-X host {cfg.BRANCH_HOST_NAME!r} not found in this tenant.\n"
            f"   Challenges 1-5 all depend on it. Check that the sandbox the "
            f"broker allocated has a host deployed, or set LAB_BRANCH_HOST to "
            f"the host that actually exists."
        )
    return host


# --------------------------------------------------------------------------- #
# IPAM / DHCP
# --------------------------------------------------------------------------- #

def ensure_ip_space(client):
    existing = client.find_by_name(cfg.path("ipam_ip_space"), cfg.IP_SPACE_NAME)
    if existing:
        info(f"IP space {cfg.IP_SPACE_NAME} already present")
        return existing

    created = client.post(cfg.path("ipam_ip_space"), json_body={
        "name": cfg.IP_SPACE_NAME,
        "comment": "TechCorp authoritative IP space (Instruqt lab)",
        "tags": LAB_TAGS,
    })
    ok(f"created IP space {cfg.IP_SPACE_NAME}")
    return created.get("result", created)


def ensure_address_block(client, space_id):
    block = cfg.ADDRESS_BLOCK
    existing = client.find_by_name(cfg.path("ipam_address_block"), block["name"])
    if existing:
        info(f"address block {block['address']}/{block['cidr']} already present")
        return existing

    created = client.post(cfg.path("ipam_address_block"), json_body={
        "address": block["address"],
        "cidr": block["cidr"],
        "space": space_id,
        "name": block["name"],
        "tags": LAB_TAGS,
    })
    ok(f"created address block {block['address']}/{block['cidr']}")
    return created.get("result", created)


def ensure_subnets(client, space_id):
    """HQ-01 and Branch-02. Branch-02 is where INC-4472 lands."""
    subnets = {}
    for key, spec in cfg.SUBNETS.items():
        existing = client.find_by_name(cfg.path("ipam_subnet"), spec["name"])
        if existing:
            info(f"subnet {spec['name']} already present")
            subnets[key] = existing
            continue

        created = client.post(cfg.path("ipam_subnet"), json_body={
            "address": spec["address"],
            "cidr": spec["cidr"],
            "space": space_id,
            "name": spec["name"],
            "comment": spec["comment"],
            "tags": LAB_TAGS,
        })
        ok(f"created subnet {spec['name']} ({spec['address']}/{spec['cidr']})")
        subnets[key] = created.get("result", created)
    return subnets


def ensure_reserved_block(client, space_id, subnet_id):
    """
    The reserved addresses in Branch-02 that the DHCP range must not collide
    with. Seeded healthy; the break moves the RANGE onto them, not the other way
    round, so these objects stay constant across the whole track.

    A fixed address in Universal DDI reserves exactly ONE address — the object
    has a single `address` field, not bounds. So a reserved "block" is N
    objects, one per address across 10.20.2.10-.30. That is what makes the
    Challenge 3 DHCP symptom real: once the range is narrowed onto this span,
    every address in it is already spoken for and utilization pins at 100%.

    `match_type`/`match_value` are required. We use synthetic MACs derived from
    the address so they are deterministic across re-seeds.
    """
    reserved = cfg.BRANCH_RESERVED_BLOCK
    existing = {
        row.get("address")
        for row in client.list_results(cfg.path("dhcp_fixed_address"))
        if row.get("tags", {}).get(cfg.LAB_TAG_KEY) == cfg.LAB_TAG_VALUE
    }

    created = []
    for address in _addresses_between(reserved["start"], reserved["end"]):
        if address in existing:
            continue
        octets = address.split(".")
        mac = "02:42:" + ":".join(f"{int(o):02x}" for o in octets)
        row = client.post(cfg.path("dhcp_fixed_address"), json_body={
            "address": address,
            "ip_space": space_id,
            "parent": subnet_id,
            "name": f"{reserved['name']} {address}",
            "comment": "Branch-02 infrastructure - do not assign",
            "match_type": "mac",
            "match_value": mac,
            "tags": LAB_TAGS,
        })
        created.append(row.get("result", row))

    if created:
        ok(f"created {len(created)} fixed addresses across "
           f"{reserved['start']}-{reserved['end']}")
    else:
        info(f"reserved addresses {reserved['start']}-{reserved['end']} "
             f"already present")
    return created


def ensure_dhcp_range(client, space_id, subnet_id, bounds=None):
    """
    The Branch-02 DHCP range. Seeded healthy (.100-.200); break #3 narrows it
    onto the reserved addresses.

    A range hangs off the IP space via `space` and off its subnet via `parent` —
    there is no `subnet` field.
    """
    bounds = bounds or cfg.BRANCH_RANGE_HEALTHY
    existing = client.find_by_name(cfg.path("dhcp_range"), bounds["name"])
    if existing:
        info(f"DHCP range {bounds['name']} already present")
        return existing

    created = client.post(cfg.path("dhcp_range"), json_body={
        "start": bounds["start"],
        "end": bounds["end"],
        "space": space_id,
        "parent": subnet_id,
        "name": bounds["name"],
        "comment": "Branch-02 client pool",
        "tags": LAB_TAGS,
    })
    ok(f"created DHCP range {bounds['start']}-{bounds['end']}")
    return created.get("result", created)


def _addresses_between(start, end):
    """Every IPv4 address from start to end inclusive."""
    def to_int(addr):
        a, b, c, d = (int(part) for part in addr.split("."))
        return (a << 24) | (b << 16) | (c << 8) | d

    def to_str(value):
        return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))

    return [to_str(value) for value in range(to_int(start), to_int(end) + 1)]


# --------------------------------------------------------------------------- #
# Challenge 5 scaffolding
# --------------------------------------------------------------------------- #

def ensure_decoy_view_and_zone(client):
    """
    A second DNS view plus the partners zone. Challenge 5's unguided break moves
    the zone into the wrong view; the baseline puts it in the right one.
    """
    correct_view = ensure_dns_view(client, cfg.DNS_VIEW_NAME)
    decoy_view = ensure_dns_view(client, cfg.DECOY_DNS_VIEW_NAME)

    zone = client.find_by_name(
        cfg.path("dns_auth_zone"), cfg.C5_ZONE_FQDN, field="fqdn"
    )
    if not zone:
        zone = ensure_auth_zone(client, correct_view["id"], cfg.C5_ZONE_FQDN)

    return correct_view, decoy_view, zone


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def build_all(client):
    """
    Build the entire healthy footprint and return the ids the break functions
    and checks need. Idempotent end to end.
    """
    print("\n=== Baseline: DNS ===", flush=True)
    view = ensure_dns_view(client)
    zone = ensure_auth_zone(client, view["id"])
    ensure_records(client, zone["id"])
    forward_zone = ensure_forward_zone(client, view["id"])
    profile = ensure_dns_config_profile(client, zone["id"])
    host = find_branch_host(client)
    attach_profile_to_host(client, profile["id"], host["id"])

    print("\n=== Baseline: IPAM / DHCP ===", flush=True)
    space = ensure_ip_space(client)
    ensure_address_block(client, space["id"])
    subnets = ensure_subnets(client, space["id"])
    branch = subnets["branch-02"]
    ensure_reserved_block(client, space["id"], branch["id"])
    dhcp_range = ensure_dhcp_range(client, space["id"], branch["id"])

    print("\n=== Baseline: Challenge 5 scaffolding ===", flush=True)
    correct_view, decoy_view, c5_zone = ensure_decoy_view_and_zone(client)

    return {
        "view_id": view["id"],
        "zone_id": zone["id"],
        "forward_zone_id": forward_zone["id"],
        "profile_id": profile["id"],
        "host_id": host["id"],
        "space_id": space["id"],
        "branch_subnet_id": branch["id"],
        "range_id": dhcp_range["id"],
        "c5_correct_view_id": correct_view["id"],
        "c5_decoy_view_id": decoy_view["id"],
        "c5_zone_id": c5_zone["id"],
    }
