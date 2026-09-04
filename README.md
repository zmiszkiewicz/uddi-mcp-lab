# uddi-mcp-lab

Automation for the Instruqt track **"Ask, Diagnose, Fix — Agentic DDI Operations
with the Infoblox MCP Server"**.

This repo is cloned onto the participant's shell container by the track's
`setup-shell` at track start. It is public and cloned anonymously.

The track itself — `track.yml`, `config.yml`, the challenge folders and their
`assignment.md` files — lives separately and is pushed with `instruqt track
push`. **A fix here needs a `git push`; a fix to an assignment or check needs an
`instruqt track push`.** Forgetting either is the most common cause of "I fixed
it but the lab still fails."

Full environment-variable reference, run order and open-questions punch list:
[`../README.md`](../README.md).

---

## What this does

Puts a CSP sandbox into a deterministic broken state, verifies each challenge's
end state, and cleans up after itself.

```
seed_lab.py     baseline footprint → apply 4 breaks → ASSERT each one landed
verify_lab.py   --stage c1..c5, reading the tenant only
revoke_mcp_keys.py + teardown_lab.py    idempotent cleanup
```

### The four breaks

| Name | Challenge | What it does |
|---|---|---|
| `dns_profile_detach` | 3 | Detaches the DNS config profile carrying `techcorp.internal` from the branch NIOS-X host |
| `dns_stale_forward` | 3 | Repoints the legacy forward zone at a decommissioned resolver |
| `dhcp_range_overlap` | 3 | Narrows the Branch-02 DHCP range onto the reserved fixed-address block |
| `view_zone_association` | 5 | Binds the partners zone to the wrong DNS view — unguided, never explained in the prose |

The first two are two halves of one story: the zone was updated during the
cutover, the profile carrying it never landed on the host, and a stale forward
zone still points at a resolver that was decommissioned. Together they produce
the intermittent SERVFAIL/NXDOMAIN of INC-4471. They are separate functions
because a learner can fix one without the other and the check has to say which.

Each break registers three callables together — `apply`, `assert`, `fix` — so
the remediation cannot drift when the seed changes.

---

## Vendored scripts

The sandbox and user lifecycle is copied **unchanged** from iracic82's
`techcorp-infrastructure`, which is the current pattern across the Infoblox lab
estate. Do not edit these here; fix them upstream and re-copy.

| Script | Role |
|---|---|
| `allocation_subtenant.py` | Allocates a CSP sandbox from the broker |
| `deallocation_subtenant.py` | Marks the sandbox for deletion at track stop |
| `cleanup_broker_allocation.py` | Alternative cleanup path |
| `sandbox_api.py` | Client for the direct sandbox create/delete API |
| `create_sandbox.py` / `delete_sandbox.py` | Direct-create fallback if the broker is bypassed |
| `user_provision.py` / `user_cleanup.py` | Interactive Portal login, create and delete |
| `create_user.py` / `delete_user.py` | Older single-purpose variants |
| `deploy_api_key.py` | Reference for `/v2/current_api_keys` |

New code exists only where this track needs something they do not do: minting
**two** keys against a **service** user, seeding a deterministic broken state,
and asserting it.

---

## Local development

```bash
pip3 install -r scripts/requirements.txt

export INFOBLOX_EMAIL=...            # CSP admin
export INFOBLOX_PASSWORD=...
export INSTRUQT_PARTICIPANT_ID=dev-local
echo "<sandbox-account-uuid>" > scripts/sandbox_id.txt

cd scripts
./preflight.sh                       # offline; no credentials needed
python3 seed_lab.py --list
python3 seed_lab.py
python3 verify_lab.py --stage all
python3 teardown_lab.py --dry-run
```

`preflight.sh` runs offline and needs no credentials. Run it before every push.
It checks that every script parses, that `lab_config` and `csp_client` import
with an empty environment, that the break registry is well formed, that the IP
range-overlap logic is correct, and it lists every CSP path still unconfirmed.

---

## Current state

**Not runnable yet.** Control flow, ordering, idempotency and assertions are
complete. A set of CSP REST paths and payload shapes still need confirming from
the API docs and the MCP setup guide — they are `Todo` objects in
`lab_config.PATHS`, and anything that tries to build a request from one raises
`LabTodo` with the exact question, at the point of use. There is no path by
which this silently calls an invented endpoint.

Run `./preflight.sh` for the current list, or see the punch list in
[`../README.md`](../README.md).
