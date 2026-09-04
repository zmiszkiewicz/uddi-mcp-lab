#!/usr/bin/env python3
"""
Create the participant's two MCP identities and mint a Service API key for each.

WHY TWO USERS RATHER THAN ONE SERVICE USER WITH TWO KEYS
--------------------------------------------------------
CSP mints API keys for the *calling* identity — POST /v2/current_api_keys, the
endpoint the estate's deploy_api_key.py already uses. There is no confirmed
endpoint for minting a key on behalf of a different user, so rather than guess
one, this script does what the API actually supports:

    for each role (read-only, read/write):
        create a user in the group carrying that role   (POST /v2/users)
        set its password                                (POST /v2/users/{id}/password)
        sign in AS that user                            (POST /v2/session/users/sign_in)
        switch into the sandbox account                 (POST /v2/session/account_switch)
        mint a key for itself                           (POST /v2/current_api_keys)

Every call above is already in production elsewhere in this estate. The
read-only/read-write split comes from group membership, which is exactly where
CSP puts it — so the RBAC lesson in Challenge 5 is enforced by the platform, not
simulated by the lab.

The two keys:

    read-only    Challenges 1, 2 and the diagnosis half of 3; and again for the
                 RBAC exercise in Challenge 5
    read/write   introduced at Challenge 3 step 5, once the learner has reviewed
                 the agent's proposed changes and approved them

The Infoblox MCP Server has no dry-run mode — write verbs execute immediately.
Two keys is how the lab models the right operational pattern rather than just
describing it. The handover is real: the agent re-reads its key file every turn,
so 03/setup-shell swapping the file genuinely changes what the agent can do.

Both keys are written mode 0600 and never echoed to stdout.

Usage:
    python3 provision_mcp_keys.py            create both users + both keys
    python3 provision_mcp_keys.py --verify   confirm both keys authenticate
"""

import argparse
import random
import string
import sys
import time

import lab_config as cfg
from csp_client import (CspClient, CspError, LabTodo, info, ok, read_state,
                        write_state)


# The two roles the track needs, and the state-file keys they land in.
ROLES = {
    "read_only": {
        "label": "read-only",
        "user_prefix": "mcp-ro",
        "group_env": "MCP_RO_GROUP",
        "key_state": "mcp_ro_key",
        "key_id_state": "mcp_ro_key_id",
    },
    "read_write": {
        "label": "read/write",
        "user_prefix": "mcp-rw",
        "group_env": "MCP_RW_GROUP",
        "key_state": "mcp_rw_key",
        "key_id_state": "mcp_rw_key_id",
    },
}


def generate_password(length=16):
    """
    A password meeting CSP's complexity rules.

    Same construction as the estate's user_provision.py: upper, lower, digits
    and 2+ specials, shuffled.
    """
    chars = (random.choices(string.ascii_uppercase, k=3)
             + random.choices(string.ascii_lowercase, k=5)
             + random.choices(string.digits, k=4)
             + random.choices("!@#$%&", k=4))
    random.shuffle(chars)
    return "".join(chars)


def discover_group(groups, role):
    """
    Guess which CSP group carries an MCP role, from the group names present.

    TODO-02 — nobody has told us the literal names yet. Rather than block the
    whole track on that, look for an unambiguous match: a group mentioning MCP
    whose name also indicates the right side of the read/write split.

    Deliberately conservative. A single unambiguous candidate is used; zero or
    several means we do NOT guess, because binding the read-only key to a group
    that can actually write would silently break the Challenge 5 RBAC exercise
    and quietly hand the agent write access for the whole track. Wrong here is
    much worse than absent.
    """
    write_words = ("write", "readwrite", "read_write", "rw", "admin", "edit")
    read_words = ("readonly", "read_only", "read-only", "read", "ro", "view")

    candidates = []
    for name, gid in groups.items():
        if not name or "mcp" not in name.lower():
            continue
        lowered = name.lower()
        looks_write = any(word in lowered for word in write_words)
        looks_read = any(word in lowered for word in read_words)

        if role == "read_write" and looks_write:
            candidates.append((name, gid))
        elif role == "read_only" and looks_read and not looks_write:
            candidates.append((name, gid))

    return candidates[0] if len(candidates) == 1 else None


def resolve_role_groups(admin):
    """
    Map the read-only and read/write MCP roles onto CSP group ids.

    Order of preference:
      1. MCP_RO_GROUP / MCP_RW_GROUP, if set. Always wins — an explicit name is
         the only thing we fully trust.
      2. An unambiguous match among the groups this sandbox actually has.
      3. Fail, printing every group that exists so the right names can be read
         straight off the setup log and pinned via the env vars.

    Whatever it resolves to is logged, because "which group did the read-only
    key end up bound to" is the difference between the RBAC lesson working and
    the lab quietly lying to the learner.
    """
    groups = {row.get("name"): row.get("id")
              for row in admin.list_results(cfg.path("groups"))}

    resolved = {}
    missing = []
    for role, spec in ROLES.items():
        override = getattr(cfg, spec["group_env"])

        if override:
            if override not in groups:
                missing.append(
                    f"{spec['group_env']}={override!r} does not exist in this "
                    f"sandbox"
                )
                continue
            resolved[role] = groups[override]
            ok(f"MCP {spec['label']} role: {override} (pinned via "
               f"{spec['group_env']})")
            continue

        guess = discover_group(groups, role)
        if guess:
            name, gid = guess
            resolved[role] = gid
            ok(f"MCP {spec['label']} role: {name} (auto-discovered)")
            info(f"    pin this with {spec['group_env']} if it is wrong")
        else:
            missing.append(
                f"could not identify the group carrying the MCP "
                f"{spec['label']} role — set {spec['group_env']}"
            )

    if missing:
        raise SystemExit(
            "❌ Could not resolve the MCP role groups (TODO-02):\n"
            + "".join(f"   - {m}\n" for m in missing)
            + "\n   Groups that DO exist in this sandbox:\n"
            + "".join(f"     {n}\n" for n in sorted(g for g in groups if g))
            + "\n   Set the right names as Instruqt team secrets and re-run:\n"
              "     instruqt secrets create --name MCP_RO_GROUP --value '<name>'\n"
              "     instruqt secrets create --name MCP_RW_GROUP --value '<name>'\n"
              "   then add them back to config.yml under `secrets:`.\n"
              "\n   Without an MCP Server role the server refuses the connection "
              "outright, so Challenge 1 cannot pass."
        )
    return resolved


def ensure_user(admin, name, email, group_id, password):
    """
    Create one MCP user in one group. Idempotent — a re-run finds the existing
    user by name rather than creating a second.

    Mirrors the estate's user_provision.py, including the 409-means-it-exists
    handling, but with a single role group instead of user+act_admin.
    """
    existing = admin.find_by_name(cfg.path("users"), name)
    if existing:
        user_id = existing["id"].split("/")[-1]
        info(f"user {name} already exists ({user_id})")
    else:
        created = admin.post(cfg.path("users"), json_body={
            "name": name,
            "email": email,
            "type": "interactive",
            "group_ids": [group_id],
        })
        user_id = created.get("result", {}).get("id", "").split("/")[-1]
        if not user_id:
            raise SystemExit(f"❌ user create for {name} returned no id: {created}")
        ok(f"created user {name} ({user_id})")

    # Always (re)set the password — we need to know it to sign in as this user,
    # and on a re-run we do not have the one from last time.
    admin.post(cfg.path("user_password", user_id=user_id),
               json_body={"new_password": password})
    return user_id


def mint_key_as_user(email, password, account_id, label):
    """
    Sign in as the user we just created and mint a key for itself.

    This is the whole trick: /v2/current_api_keys mints for the caller, so we
    become the caller. The key inherits exactly the permissions of the group the
    user is in — which is what makes the read-only key genuinely read-only.
    """
    user_client = CspClient()

    # Fresh users occasionally are not yet accepted by sign_in immediately after
    # the password set. Retry rather than fail the whole track setup on a race.
    jwt = None
    for attempt in range(5):
        try:
            resp = user_client.session.post(
                f"{user_client.base_url}{cfg.path('signin')}",
                json={"email": email, "password": password},
                timeout=(5, 30),
            )
            if resp.status_code == 200:
                jwt = resp.json()["jwt"]
                break
            info(f"sign-in as {email} returned {resp.status_code}, retrying")
        except Exception as exc:                       # noqa: BLE001
            info(f"sign-in as {email} failed ({exc}), retrying")
        time.sleep(2 ** attempt)

    if not jwt:
        raise SystemExit(
            f"❌ Could not sign in as {email} to mint its {label} key.\n"
            f"   The user exists but its credentials were not accepted."
        )

    user_client._set_jwt(jwt)
    user_client.switch_account(account_id)

    created = user_client.post(cfg.path("current_api_keys"), json_body={
        "name": f"mcp-{label.replace('/', '-')}-{cfg.PARTICIPANT_ID}",
    })
    result = created.get("result", created)

    key = result.get("key")
    key_id = (result.get("id") or "").split("/")[-1]
    if not key:
        raise SystemExit(
            f"❌ no plaintext key in the {label} response. "
            f"Fields returned: {sorted(result)}"
        )

    ok(f"minted {label} Service API key ({key_id})")
    return key_id, key


def verify_key(api_key, label):
    """
    Confirm a key authenticates, using the same header the Infoblox MCP Server
    uses: `Authorization: Token <key>`.

    A read is enough to prove the key is live and its role attached. We do NOT
    attempt a write with the read/write key — that would mutate the tenant
    during setup, and the entire write-safety story is that writes happen only
    when the operator approves them.
    """
    client = CspClient.from_service_key(api_key)
    try:
        views = client.list_results(cfg.path("dns_view"))
    except CspError as exc:
        return False, f"{label} key could not read DNS views: {exc}"
    info(f"{label} key authenticated, sees {len(views)} DNS view(s)")
    return True, None


def main():
    parser = argparse.ArgumentParser(
        description="Provision the participant's MCP users and Service API keys."
    )
    parser.add_argument("--verify", action="store_true",
                        help="verify existing keys instead of creating new ones")
    args = parser.parse_args()

    cfg.require_env()

    try:
        if args.verify:
            failures = []
            for spec in ROLES.values():
                passed, reason = verify_key(read_state(spec["key_state"]),
                                            spec["label"])
                if not passed:
                    failures.append(reason)
            if failures:
                for reason in failures:
                    print(f"❌ {reason}", flush=True)
                return 1
            ok("both Service API keys authenticate")
            return 0

        admin = CspClient.as_admin()
        account_id = admin.account_id
        ok(f"authenticated; sandbox account {account_id}")

        role_groups = resolve_role_groups(admin)

        for role, spec in ROLES.items():
            name = f"{spec['user_prefix']}-{cfg.PARTICIPANT_ID}"
            email = f"{name}@{cfg.USER_DOMAIN}"
            password = generate_password()

            user_id = ensure_user(admin, name, email, role_groups[role], password)
            key_id, key = mint_key_as_user(email, password, account_id,
                                           spec["label"])

            write_state(spec["key_id_state"], key_id)
            write_state(spec["key_state"], key, secret=True)
            spec["_user_id"] = user_id

        # Recorded so revoke_mcp_keys.py can delete both users at teardown.
        write_state("mcp_service_user_id",
                    ",".join(ROLES[r]["_user_id"] for r in ROLES))

    except LabTodo as todo:
        print(f"\n❌ Unresolved TODO reached while provisioning keys:\n"
              f"   {todo}\n   Fill this in at lab_config.PATHS and re-run.\n",
              flush=True)
        return 1

    print(f"\n{'=' * 62}", flush=True)
    print("🔐 MCP credentials provisioned", flush=True)
    for spec in ROLES.values():
        print(f"   {spec['label']:<11} user mcp-… ({spec['_user_id']}), "
              f"key id {read_state(spec['key_id_state'])}", flush=True)
    print(f"   Keys written to mcp_ro_key.txt / mcp_rw_key.txt (mode 0600)",
          flush=True)
    print(f"   MCP endpoint: {cfg.MCP_SERVER_URL}", flush=True)
    print(f"{'=' * 62}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
