#!/usr/bin/env python3
"""
Validate each challenge's END STATE against the Infoblox API.

    verify_lab.py --stage c1     challenge 1  connection is real
    verify_lab.py --stage c2     challenge 2  utilization answer matches reality
    verify_lab.py --stage c3     challenge 3  both incidents actually fixed
    verify_lab.py --stage c4     challenge 4  audit trail + chart artifact
    verify_lab.py --stage c5     challenge 5  unguided break found and fixed
    verify_lab.py --stage all    everything, for a maintainer smoke test

NEVER PARSE THE CHAT TRANSCRIPT. The agent's wording is nondeterministic — the
same correct fix can be described five different ways, and a check that greps
for phrasing will fail a learner who did everything right. Every assertion below
reads the tenant through the API and asks whether the world is in the state the
challenge requires, regardless of how the learner got it there. A learner who
fixes something in the Portal instead of through the agent still passes, and
that is correct: the challenge is about the outcome.

On failure, csp_client.fail() writes a one-sentence reason to
/tmp/mcp_lab_check_reason.txt, which check-shell hands straight to Instruqt's
fail-message.
"""

import argparse
import json
import os
import sys

import lab_config as cfg
from csp_client import (CspClient, CspError, LabTodo, clear_reason, fail, info,
                        ok, read_state)


def load_ids():
    """Object ids recorded by seed_lab.py."""
    for directory in (os.getcwd(), cfg.SCRIPT_DIR):
        candidate = os.path.join(directory, "seed_ids.json")
        if os.path.exists(candidate):
            with open(candidate) as handle:
                return json.load(handle)
    fail("The lab's seeded state is missing — the environment did not finish "
         "provisioning. Restart the track.")


# --------------------------------------------------------------------------- #
# Challenge 1 — "Your new co-pilot"
# --------------------------------------------------------------------------- #

def check_c1(client, ids):
    """
    Confirm the agent is really connected to THIS participant's tenant.

    Preferred: prove via API that the participant's key has authenticated to the
    MCP endpoint at least once (TODO-17 — needs a key-last-used or auth-audit
    endpoint, which may not exist).

    Fallback, and what runs today: confirm the read-only key itself
    authenticates and can see a non-trivial service catalog. Since the lab runs
    its own agent against that same key, a key that works is a strong signal the
    agent tab works too.
    """
    # ---- Preferred: the key has been used against the MCP endpoint ----------
    try:
        events = client.list_results(
            cfg.path("mcp_auth_events"),
            params={"_filter": f'key_id=="{read_state("mcp_ro_key_id")}"'},
        )
    except LabTodo as todo:
        info(f"MCP auth-event check unavailable — {todo}")
        events = None
    except CspError as exc:
        info(f"MCP auth-event lookup failed: {exc}")
        events = None

    if events is not None:
        if not events:
            fail("Your service key has not authenticated to the Infoblox MCP "
                 "Server yet. Open the Agent tab and run the connection-check "
                 "prompt, then click Check again.")
        ok(f"service key has authenticated to {cfg.MCP_SERVER_URL}")
        return

    # ---- Fallback: the key works, and the catalog is non-trivially large ----
    # A healthy connection returns dozens of CSP services. If the key cannot
    # read the service catalog at all, the MCP Server would have refused the
    # connection outright, which is the RBAC teaching beat for this challenge.
    key = read_state("mcp_ro_key")
    key_client = CspClient.from_service_key(key)
    try:
        services = key_client.list_results(cfg.path("infra_services"))
    except CspError as exc:
        fail("Your read-only service key cannot reach Infoblox. Access to the "
             "MCP Server is gated by RBAC — if no MCP Server role is assigned "
             f"the connection is refused outright. ({exc.status})")

    if len(services) < 2:
        fail(f"The service catalog came back with only {len(services)} entries. "
             "A healthy connection returns many more — the tenant may not be "
             "fully entitled. Ask your facilitator.")

    ok(f"read-only key authenticates and sees {len(services)} services")


# --------------------------------------------------------------------------- #
# Challenge 2 — "Ask, don't click"
# --------------------------------------------------------------------------- #

def _utilization_pct(obj):
    """Pull an integer utilization percentage off a range/subnet, or None."""
    util = obj.get("utilization")
    if util is None:
        return None
    if not isinstance(util, dict):
        return float(util)
    for key in ("utilization", "dhcp_utilization"):
        if util.get(key) is not None:
            return float(util[key])
    return None


def most_utilized_range(client):
    """
    The range with the highest utilization, read live from the API.

    A Range carries `utilization` (an object). Depending on the object type CSP
    returns either `utilization.utilization` or `utilization.dhcp_utilization`
    as an integer percentage, so accept both and normalise here — nothing
    downstream should have to care which shape came back.
    """
    ranges = client.list_results(cfg.path("dhcp_range"))
    if not ranges:
        fail("No DHCP ranges found in this tenant — the environment did not "
             "seed correctly. Restart the track.")

    scored = []
    for row in ranges:
        pct = _utilization_pct(row)
        if pct is None:
            continue
        scored.append((pct, row))

    if not scored:
        fail("Could not read utilization from any DHCP range. This is a lab "
             "defect, not your mistake — tell your facilitator.")

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[0]


def check_c2(client, ids):
    """
    A READINESS GATE, not a test of the participant.

    Challenge 2 is entirely read-only — the learner asks three questions and
    cross-checks one against the Portal. Nothing in the tenant changes, so there
    is no participant-authored end state to assert, and there is no answer field
    to grade.

    What IS worth asserting is that all three prompts were actually answerable.
    Each one below maps to one prompt in the assignment. If any of them comes
    back empty the participant just had a hollow conversation with an assistant
    that could not see their data, and every later challenge inherits that
    problem — so it is far better to say so here than to pass silently and let
    them discover it mid-incident in Challenge 3.

    The failure messages are written accordingly: they tell the participant this
    is an environment fault, not their mistake.
    """
    # -- Prompt 1: "What A and CNAME records exist for app.techcorp.internal?" -
    label = cfg.APP_FQDN.rstrip(".").split(".")[0]
    records = client.list_results(
        cfg.path("dns_record"),
        params={"_filter": f'zone=="{ids["zone_id"]}" and name_in_zone=="{label}"'},
    )
    if not records:
        fail(f"No records found for {cfg.APP_FQDN.rstrip('.')} in your tenant. "
             f"That is an environment fault, not your mistake — the record "
             f"lookup prompt cannot have returned anything useful. Tell your "
             f"facilitator.")
    types = sorted({row.get("type") for row in records if row.get("type")})
    ok(f"{cfg.APP_FQDN.rstrip('.')} has {len(records)} record(s): {', '.join(types)}")

    # -- Prompt 2: top queried domains / NXDOMAIN by client -------------------
    # The analytics half of the challenge. Without populated cubes this prompt
    # returns nothing and the "configuration vs. activity" teaching beat — the
    # whole point of the challenge — does not land.
    try:
        activity = client.list_results(cfg.path("dns_activity_cube"))
        if not activity:
            fail("No DNS activity data is available for your tenant, so the "
                 "top-queried-domains and NXDOMAIN questions have nothing to "
                 "answer from. That is an environment fault, not your mistake "
                 "— tell your facilitator.")
        ok(f"DNS activity data present ({len(activity)} row(s))")
    except LabTodo as todo:
        info(f"DNS activity check unavailable — {todo}")

    # -- Prompt 3: "Which DHCP ranges are above 90% utilization?" -------------
    # At least one range must actually be above the threshold, or the question
    # has an empty answer and the Portal cross-check has nothing to compare.
    actual, row = most_utilized_range(client)
    if actual < cfg.UTILIZATION_THRESHOLD_PCT:
        fail(f"No DHCP range is above {cfg.UTILIZATION_THRESHOLD_PCT:.0f}% "
             f"utilization — the highest is {actual}%. The utilization prompt "
             f"has nothing to return. That is a seeding fault, not your "
             f"mistake — tell your facilitator.")
    ok(f"most-utilized range is {row.get('name')} at {actual}% — above the "
       f"{cfg.UTILIZATION_THRESHOLD_PCT:.0f}% threshold, so the prompt returns "
       f"a real answer")


# --------------------------------------------------------------------------- #
# Challenge 3 — "Something is broken" (the core challenge)
# --------------------------------------------------------------------------- #

def check_c3(client, ids):
    """
    Four conditions, exactly as the spec lists them. All four must hold.

    Deliberately checked in incident order — DNS first, DHCP second — so a
    learner who fixed one incident and not the other gets a message naming the
    one still outstanding rather than a generic failure.
    """
    # -- 1. DNS config profile attached, stale forward zone corrected ---------
    host = client.get(cfg.path("dns_host") + f"/{ids['host_id']}")
    host = host.get("result", host)
    if host.get("server") != ids["profile_id"]:
        fail(f"The DNS config profile '{cfg.DNS_CONFIG_PROFILE_NAME}' is still "
             f"not attached to {cfg.BRANCH_HOST_NAME}. Until it is, that host "
             f"is not serving {cfg.ZONE_FQDN.rstrip('.')} at all.")
    ok(f"DNS config profile attached to {cfg.BRANCH_HOST_NAME}")

    forward = client.get(
        cfg.path("dns_forward_zone") + f"/{ids['forward_zone_id']}"
    )
    forward = forward.get("result", forward)
    forwarders = [f.get("address") for f in forward.get("external_forwarders", [])]
    if cfg.FORWARD_RESOLVER_DECOMMISSIONED in forwarders:
        fail(f"The forward zone {cfg.FORWARD_ZONE_FQDN.rstrip('.')} still points "
             f"at {cfg.FORWARD_RESOLVER_DECOMMISSIONED}, which was "
             f"decommissioned during the cutover. Remove or correct it.")
    ok(f"stale forward zone corrected (now: {forwarders or 'removed'})")

    # -- 2. app.techcorp.internal resolves from the client host ---------------
    # Independent of any config read: the challenge's own success criterion is
    # that the name resolves, and a config that says it should is not the same
    # thing as a resolver that does.
    check_app_resolves()

    # -- 3. Branch-02 range no longer overlaps, utilization under threshold ---
    import breaks  # local import: only the C3 check needs the overlap helper

    dhcp_range = client.get(cfg.path("dhcp_range") + f"/{ids['range_id']}")
    dhcp_range = dhcp_range.get("result", dhcp_range)
    start, end = dhcp_range.get("start"), dhcp_range.get("end")

    if breaks.ranges_overlap(start, end,
                             cfg.BRANCH_RESERVED_BLOCK["start"],
                             cfg.BRANCH_RESERVED_BLOCK["end"]):
        fail(f"The Branch-02 DHCP range ({start}-{end}) still overlaps the "
             f"reserved block {cfg.BRANCH_RESERVED_BLOCK['start']}-"
             f"{cfg.BRANCH_RESERVED_BLOCK['end']}, so there are effectively no "
             f"assignable addresses.")
    ok(f"Branch-02 range {start}-{end} is clear of the reserved block")

    pct = _utilization_pct(dhcp_range)
    if pct is not None and pct >= cfg.UTILIZATION_THRESHOLD_PCT:
        fail(f"Branch-02 utilization is still {pct}%. It needs to be below "
             f"{cfg.UTILIZATION_THRESHOLD_PCT:.0f}% before clients can reliably "
             f"get addresses.")
    ok(f"Branch-02 utilization is {pct}%")

    # -- 4. A lease was issued to the branch client host ---------------------
    check_lease_issued(client, ids)


def check_app_resolves():
    """
    Resolve app.techcorp.internal the way the learner does from the terminal tab.

    TODO-22 — this needs to run against the branch client host's resolver, not
    the check container's. Confirm how the terminal tab reaches the client host
    (direct dig with @<resolver>, or ssh to the client), then fill in RESOLVER
    below.
    """
    import shutil
    import subprocess

    if not shutil.which("dig"):
        info("dig not installed in the check container — skipping the "
             "independent resolution probe")
        return

    resolver = os.environ.get("LAB_BRANCH_RESOLVER")
    if not resolver:
        info("LAB_BRANCH_RESOLVER not set (TODO-22) — skipping the independent "
             "resolution probe")
        return

    fqdn = cfg.APP_FQDN.rstrip(".")
    result = subprocess.run(
        ["dig", "+short", "+timeout=3", "+tries=2", f"@{resolver}", fqdn, "A"],
        capture_output=True, text=True,
    )
    answers = [line for line in result.stdout.split() if line]

    if not answers:
        fail(f"{fqdn} still does not resolve from the branch resolver "
             f"({resolver}). The configuration may be correct but not yet "
             f"serving — ask the agent to verify the fix.")

    ok(f"{fqdn} resolves to {', '.join(answers)}")


def check_lease_issued(client, ids):
    """
    Confirm the branch client host actually got an address.

    TODO-15 — the lease-listing endpoint and its filter are the unknown. This is
    the single most valuable assertion in the track: a range that looks correct
    but issues no leases is exactly the failure mode INC-4472 describes, so
    "the config looks right" is not good enough here.
    """
    try:
        leases = client.list_results(
            cfg.path("dhcp_lease"),
            params={"_filter": f'hardware=="{cfg.CLIENT_HOST_MAC}"'},
        )
    except LabTodo as todo:
        info(f"lease check unavailable — {todo}")
        return

    if not leases:
        fail(f"No DHCP lease has been issued to {cfg.CLIENT_HOST_NAME} yet. The "
             f"range looks right — request a fresh lease from the client host, "
             f"then click Check again.")

    addresses = [lease.get("address") for lease in leases]
    ok(f"{cfg.CLIENT_HOST_NAME} holds a lease: {', '.join(a for a in addresses if a)}")


# --------------------------------------------------------------------------- #
# Challenge 4 — "Prove it, then prevent it"
# --------------------------------------------------------------------------- #

def check_c4(client, ids):
    """
    The spec's check is "quiz answers plus confirmation that a chart artifact was
    produced". The quiz answers are handled by Instruqt natively in the
    assignment frontmatter, so this validates the two things the API can see:
    that the learner's changes left an audit trail, and that a chart artifact
    exists.
    """
    # -- Audit trail for the Challenge 3 writes ------------------------------
    try:
        service_user_id = read_state("mcp_service_user_id", required=False)
        entries = client.list_results(
            cfg.path("audit_log"),
            params={"_filter": f'user_id=="{service_user_id}"'},
        )
    except LabTodo as todo:
        info(f"audit-log check unavailable — {todo}")
        entries = None
    except CspError as exc:
        info(f"audit-log lookup failed: {exc}")
        entries = None

    if entries is not None:
        if not entries:
            fail("No audit records found for the changes made in Challenge 3. "
                 "Every change the agent makes is attributed and auditable — if "
                 "nothing is recorded, the fix was not applied through your "
                 "service user.")
        ok(f"{len(entries)} audit record(s) attributed to your service user")

    # -- Chart artifact ------------------------------------------------------
    # TODO-18 — what counts as a verifiable chart artifact depends on how the
    # hosted Claude instance surfaces generated charts. If it writes them to a
    # shared volume, point LAB_ARTIFACT_DIR at it and this works as written.
    artifact_dir = os.environ.get("LAB_ARTIFACT_DIR")
    if not artifact_dir:
        info("LAB_ARTIFACT_DIR not set (TODO-18) — skipping the chart-artifact "
             "check")
        return

    charts = [
        name for name in os.listdir(artifact_dir)
        if name.lower().endswith((".png", ".svg", ".jpg", ".jpeg", ".html"))
    ] if os.path.isdir(artifact_dir) else []

    if not charts:
        fail("No chart artifact found. Ask the agent to chart DNS query failures "
             "for the last 24 hours — that chart is the thing you take back to "
             "your team.")

    ok(f"chart artifact produced: {charts[0]}")


# --------------------------------------------------------------------------- #
# Challenge 5 — "Make it yours" (optional)
# --------------------------------------------------------------------------- #

def check_c5(client, ids):
    """
    Two assertions, matching the two halves of the challenge that leave a trace.

    The RBAC exercise and the tool-discovery exercise are both read-only and
    leave nothing to verify — they are for the learner's understanding, and the
    knowledge check in Challenge 4 covers the concept. What IS verifiable is
    that the unguided break was found and fixed with no prompt scaffolding,
    which is the real skills test.
    """
    # -- The read-only key is genuinely read-only ----------------------------
    # Confirms the RBAC exercise is actually demonstrable rather than a claim in
    # the assignment prose. If the read-only key can write, the exercise is a
    # lie and the maintainer needs to know.
    ro_key = read_state("mcp_ro_key", required=False)
    if ro_key:
        ro_client = CspClient.from_service_key(ro_key)
        try:
            ro_client.post(cfg.path("dns_view"), json_body={
                "name": f"rbac-probe-{cfg.PARTICIPANT_ID}",
            }, expect={403, 401})
            ok("read-only key is correctly refused write access")
        except CspError as exc:
            if exc.status in (200, 201):
                fail("The read-only key was able to write. This is a lab defect "
                     "— tell your facilitator; the RBAC exercise will not "
                     "demonstrate anything.")
            info(f"read-only write probe returned {exc.status} (treated as denied)")

    # -- The unguided break has been fixed -----------------------------------
    zone = client.get(cfg.path("dns_auth_zone") + f"/{ids['c5_zone_id']}")
    zone = zone.get("result", zone)

    if zone.get("view") == ids["c5_decoy_view_id"]:
        fail(f"There is still a misconfiguration in this tenant that nobody has "
             f"told you about. Use the agent to find it — start by asking what "
             f"looks inconsistent across your DNS configuration.")

    if zone.get("view") != ids["c5_correct_view_id"]:
        fail(f"{cfg.C5_ZONE_FQDN.rstrip('.')} is in an unexpected DNS view. It "
             f"should be served from {cfg.DNS_VIEW_NAME}.")

    ok(f"{cfg.C5_ZONE_FQDN.rstrip('.')} is correctly associated with "
       f"{cfg.DNS_VIEW_NAME}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

STAGES = {
    "c1": check_c1,
    "c2": check_c2,
    "c3": check_c3,
    "c4": check_c4,
    "c5": check_c5,
}


def main():
    parser = argparse.ArgumentParser(
        description="Validate challenge end state via the Infoblox API."
    )
    parser.add_argument("--stage", required=True,
                        choices=list(STAGES) + ["all"])
    args = parser.parse_args()

    clear_reason()

    try:
        client = CspClient.as_admin()
    except LabTodo as todo:
        fail(f"The lab is not fully configured yet: {todo}")
    except Exception as exc:                       # noqa: BLE001
        fail(f"Could not reach Infoblox to verify your work: {exc}")

    stages = list(STAGES) if args.stage == "all" else [args.stage]
    ids = load_ids()

    for stage in stages:
        print(f"\n── checking {stage} ──", flush=True)
        try:
            STAGES[stage](client, ids)
        except LabTodo as todo:
            fail(f"This check is not finished yet: {todo}")

    ok(f"{args.stage} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
