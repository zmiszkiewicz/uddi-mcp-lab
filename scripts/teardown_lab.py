#!/usr/bin/env python3
"""
Remove the DDI objects seed_lab.py created. Safe to run twice.

This is the belt to revoke_mcp_keys.py's braces, and to the broker's braces
after that. The broker deletes the subtenant outright a few minutes after
deallocation_subtenant.py runs, so in the normal case this script is redundant.
It exists for the case that is not normal: a track that failed halfway, a
sandbox that gets recycled rather than destroyed, or a maintainer iterating on
the seed against a long-lived test tenant. In all three, a half-fixed tenant
that nobody cleaned is the thing that makes the NEXT run confusing.

Only objects tagged {instruqt-lab: <track slug>} are touched. Anything else in
the tenant is left alone — including the NIOS-X host, which the lab never
created and must not delete.

Deletion order is reverse-dependency: records before zones, ranges and
reservations before subnets, subnets before blocks before spaces. A 404 at any
step is success, not an error.

Usage:
    python3 teardown_lab.py                 remove the seeded objects
    python3 teardown_lab.py --reset         remove them, then re-seed from clean
    python3 teardown_lab.py --dry-run       list what would be deleted
"""

import argparse
import json
import os
import sys

import lab_config as cfg
from csp_client import CspClient, CspError, LabTodo, info, ok, read_state


# Reverse-dependency order. Each entry is (registry key, human label).
DELETE_ORDER = [
    ("dns_record",          "DNS records"),
    ("dhcp_range",          "DHCP ranges"),
    ("dhcp_fixed_address",  "fixed addresses / reservations"),
    ("ipam_subnet",         "IPAM subnets"),
    ("ipam_address_block",  "IPAM address blocks"),
    ("ipam_ip_space",       "IPAM IP spaces"),
    ("dns_forward_zone",    "forward zones"),
    ("dns_auth_zone",       "authoritative zones"),
    ("dns_config_profile",  "DNS config profiles"),
    ("dns_view",            "DNS views"),
]


def is_ours(obj):
    """True only for objects this lab tagged. The guard against collateral damage."""
    tags = obj.get("tags") or {}
    return tags.get(cfg.LAB_TAG_KEY) == cfg.LAB_TAG_VALUE


def detach_profile_first(client, ids):
    """
    Clear the host -> profile association before deleting the profile.

    Deleting a profile that is still attached either fails or leaves the host in
    a state the next seeding has to reconcile. Doing it explicitly is cheaper
    than debugging that later. The host itself is never deleted.
    """
    host_id = ids.get("host_id")
    if not host_id:
        return
    try:
        client.patch(cfg.path("dns_host") + f"/{host_id}",
                     json_body={"server": None})
        info(f"detached DNS config profile from {cfg.BRANCH_HOST_NAME}")
    except (CspError, LabTodo) as exc:
        info(f"could not detach profile from host (continuing): {exc}")


def purge(client, registry_key, label, dry_run):
    """Delete every tagged object under one collection. Returns a count."""
    try:
        collection = cfg.path(registry_key)
    except LabTodo as todo:
        info(f"skipping {label} — {todo}")
        return 0

    try:
        rows = client.list_results(collection)
    except CspError as exc:
        info(f"could not list {label} (continuing): {exc}")
        return 0

    ours = [row for row in rows if is_ours(row)]
    if not ours:
        info(f"no lab-tagged {label} to remove")
        return 0

    removed = 0
    for row in ours:
        obj_id = (row.get("id") or "").split("/")[-1]
        name = row.get("name") or row.get("fqdn") or obj_id

        if dry_run:
            print(f"   would delete {label[:-1]}: {name}", flush=True)
            removed += 1
            continue

        try:
            # csp_client.delete() accepts 404 — deleting twice is a no-op.
            client.delete(f"{collection}/{obj_id}")
            info(f"deleted {label[:-1]}: {name}")
            removed += 1
        except CspError as exc:
            # Never abort. One stubborn object must not strand the rest.
            print(f"⚠️  could not delete {label[:-1]} {name}: {exc}", flush=True)

    return removed


def load_ids():
    for directory in (os.getcwd(), cfg.SCRIPT_DIR):
        candidate = os.path.join(directory, "seed_ids.json")
        if os.path.exists(candidate):
            with open(candidate) as handle:
                return json.load(handle)
    return {}


def clear_ids():
    for directory in (os.getcwd(), cfg.SCRIPT_DIR):
        candidate = os.path.join(directory, "seed_ids.json")
        try:
            os.unlink(candidate)
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Remove the seeded lab objects from the tenant."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be deleted, change nothing")
    parser.add_argument("--reset", action="store_true",
                        help="after teardown, re-seed the tenant from clean")
    args = parser.parse_args()

    sandbox_id = read_state("sandbox_id", required=False)
    if not sandbox_id:
        print("⚠️  sandbox_id.txt not found — nothing was seeded, "
              "nothing to tear down", flush=True)
        return 0

    try:
        client = CspClient.as_admin(account_id=sandbox_id)
    except Exception as exc:                       # noqa: BLE001
        print(f"⚠️  could not authenticate against sandbox {sandbox_id}: {exc}",
              flush=True)
        print("   The account may already be gone. Nothing to tear down.",
              flush=True)
        return 0

    ids = load_ids()
    if not args.dry_run:
        detach_profile_first(client, ids)

    total = 0
    for registry_key, label in DELETE_ORDER:
        print(f"\n── {label} ──", flush=True)
        total += purge(client, registry_key, label, args.dry_run)

    if not args.dry_run:
        clear_ids()

    verb = "would remove" if args.dry_run else "removed"
    print(f"\n{'=' * 62}", flush=True)
    ok(f"teardown complete — {verb} {total} lab-tagged object(s)")
    print(f"{'=' * 62}", flush=True)

    if args.reset and not args.dry_run:
        print("\n=== Re-seeding ===", flush=True)
        import seed_lab
        return seed_lab.main()

    return 0


if __name__ == "__main__":
    sys.exit(main())
