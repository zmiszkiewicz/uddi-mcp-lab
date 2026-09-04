#!/usr/bin/env python3
"""
Single source of truth for the "Ask, Diagnose, Fix" MCP track.

Three things live here and nowhere else:

  1. Every environment variable the lab reads, with its default.
  2. The deterministic topology — zone names, CIDRs, host names, thresholds.
     Baseline seeding, the break functions and the checks all read these same
     constants, so a rename in one place cannot desynchronise the three.
  3. The CSP REST path registry, including the paths nobody has confirmed yet.

On (3): paths we have used in production elsewhere in the estate are plain
strings. Paths that still need confirming from the Infoblox API docs are `Todo`
objects. Asking the client for one of those raises `LabTodo` with the exact
question that needs answering and the punch-list ID from README.md — so an
unfinished lab fails loudly at the right line instead of 404-ing somewhere
confusing.

NOTHING SECRET IS HARDCODED HERE. Credentials and tenant identifiers come from
the environment only.
"""

import os


# --------------------------------------------------------------------------- #
# TODO registry
# --------------------------------------------------------------------------- #

class LabTodo(RuntimeError):
    """Raised when the lab reaches a path or payload that is not confirmed yet."""


class Todo:
    """
    A placeholder standing in for a CSP REST path we have not confirmed.

    Deliberately not a string: anything that tries to use it as one blows up at
    the point of use with the question that needs answering, rather than
    silently building a request against a made-up endpoint.
    """

    def __init__(self, ident, question):
        self.ident = ident
        self.question = question

    def __str__(self):
        raise LabTodo(f"[{self.ident}] {self.question}")

    __repr__ = __str__

    def __fspath__(self):
        raise LabTodo(f"[{self.ident}] {self.question}")


def is_todo(value):
    return isinstance(value, Todo)


# --------------------------------------------------------------------------- #
# Environment — control plane
# --------------------------------------------------------------------------- #

# CSP tenant that owns the sandbox pool. Admin credentials, from Instruqt secrets.
CSP_URL = f"https://{os.environ.get('CSP_URL', 'csp.infoblox.com')}"
INFOBLOX_EMAIL = os.environ.get("INFOBLOX_EMAIL")
INFOBLOX_PASSWORD = os.environ.get("INFOBLOX_PASSWORD")

# Sandbox broker — the estate's current allocation path (allocation_subtenant.py).
BROKER_API_URL = os.environ.get(
    "BROKER_API_URL",
    "https://api-sandbox-broker.highvelocitynetworking.com/v1",
)
BROKER_API_TOKEN = os.environ.get("BROKER_API_TOKEN")
SANDBOX_NAME_PREFIX = os.environ.get("SANDBOX_NAME_PREFIX", "lab")

# Legacy direct-create path (create_sandbox.py / delete_sandbox.py). Only needed
# if the broker is bypassed.
INFOBLOX_TOKEN = os.environ.get("Infoblox_Token")

# Instruqt-supplied, per participant.
PARTICIPANT_ID = os.environ.get("INSTRUQT_PARTICIPANT_ID")
TRACK_SLUG = os.environ.get("INSTRUQT_TRACK_SLUG", "uddi-mcp-agentic-ddi")
USER_EMAIL = os.environ.get("INSTRUQT_USER_EMAIL") or os.environ.get("INSTRUQT_EMAIL")
USER_DOMAIN = os.environ.get("USER_DOMAIN", "infoblox.lab")

# Hosted Infoblox MCP Server. Named per the brand guidelines: "Infoblox Model
# Context Protocol (MCP) Server" on first mention, "Infoblox MCP Server" after.
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "https://csp.infoblox.com/mcp")

# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #
#
# Claude runs on Amazon Bedrock in the Instruqt-provided AWS sandbox account.
# Bedrock does NOT support the Claude API's MCP connector (see
# platform-availability: MCP connector is 1P/Foundry only), so the agent runs
# its own MCP client and drives the tool loop itself. agent/ has the code.

# Bedrock model id. Bedrock ids carry an `anthropic.` prefix.
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-opus-5")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

# The agent reads its Service API key from this file on EVERY turn, rather than
# capturing it at boot. That is what makes the read-only -> read/write handover
# at Challenge 3 real: 03/setup-shell rewrites this file with the read/write
# key, 05/setup-shell writes the read-only key back, and the agent picks the
# change up on the next message with no restart.
AGENT_KEY_FILE = os.environ.get("AGENT_KEY_FILE", "/opt/lab/mcp_key")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "8501"))

# Where the agent writes generated charts. Challenge 4's check looks here.
ARTIFACT_DIR = os.environ.get("LAB_ARTIFACT_DIR", "/opt/lab/artifacts")

# CSP groups assigned to each MCP user.
#
# Confirmed from a live sandbox. The tenant follows a consistent
# `ib-<service>-admin` / `ib-<service>-user` convention (ib-ddi-admin /
# ib-ddi-user, ib-td-admin / ib-td-user, and so on), and the MCP pair is
# ib-mcp-server-admin / ib-mcp-server-user.
#
# THREE groups per user, not one. An MCP-server role gates access to the MCP
# server itself; it does not grant permission to read DNS, DHCP or IPAM data.
# Without the matching ib-ddi-* role the connection succeeds and then every
# tool call comes back empty or denied — which looks exactly like a broken lab.
#
#   user                  base group every CSP user needs (create_user.py
#                         assigns it too)
#   ib-mcp-server-user    read-only access to the MCP Server
#   ib-ddi-user           read-only access to the DDI data behind it
#
# Deliberately NOT act_admin: an account-admin read-only user would make the
# Challenge 5 RBAC exercise meaningless.
#
# Comma-separated, overridable. Names that do not exist in a given sandbox are
# skipped with a warning rather than being fatal, so a tenant with a slightly
# different group set still comes up.
MCP_RO_GROUPS = [
    g.strip() for g in os.environ.get(
        "MCP_RO_GROUPS", "user,ib-mcp-server-user,ib-ddi-user"
    ).split(",") if g.strip()
]
MCP_RW_GROUPS = [
    g.strip() for g in os.environ.get(
        "MCP_RW_GROUPS", "user,ib-mcp-server-admin,ib-ddi-admin"
    ).split(",") if g.strip()
]

# Single-name overrides, kept for the Instruqt secrets already documented. If
# set, each replaces just the MCP-server group in the corresponding list.
MCP_RO_GROUP = os.environ.get("MCP_RO_GROUP")
MCP_RW_GROUP = os.environ.get("MCP_RW_GROUP")

# Where setup-shell drops the clone. Checks source /root/.bashrc to pick this up.
LAB_DIR = os.environ.get("LAB_DIR", "/root/infoblox-lab/uddi-mcp-lab")
SCRIPT_DIR = os.path.join(LAB_DIR, "scripts")

# State files written by setup-shell, read by checks and teardown. All live in
# SCRIPT_DIR because that is the working directory the vendored iracic82
# scripts assume.
STATE_FILES = {
    "sandbox_id": "sandbox_id.txt",        # CSP account UUID
    "external_id": "external_id.txt",      # same value, broker's name for it
    "subtenant_id": "subtenant_id.txt",    # broker sandbox id, used to deallocate
    "sandbox_name": "sandbox_name.txt",    # human name, e.g. lab-adventure-0086
    "sfdc_account_id": "sfdc_account_id.txt",
    "user_id": "user_id.txt",              # interactive portal user
    "user_email": "user_email.txt",
    "user_password": "user_password.txt",
    "mcp_service_user_id": "mcp_service_user_id.txt",
    "mcp_ro_key_id": "mcp_ro_key_id.txt",
    "mcp_rw_key_id": "mcp_rw_key_id.txt",
    "mcp_ro_key": "mcp_ro_key.txt",        # chmod 600 — the secret itself
    "mcp_rw_key": "mcp_rw_key.txt",        # chmod 600 — the secret itself
}


# --------------------------------------------------------------------------- #
# Topology — the deterministic world every participant starts in
# --------------------------------------------------------------------------- #
#
# Overridable so a maintainer can run two seedings side by side in one tenant,
# but the defaults are what the assignment prose names. Change a default here
# and you must change the matching assignment.md text.

DNS_VIEW_NAME = os.environ.get("LAB_DNS_VIEW", "techcorp-view")
ZONE_FQDN = os.environ.get("LAB_ZONE_FQDN", "techcorp.internal.")
APP_FQDN = os.environ.get("LAB_APP_FQDN", "app.techcorp.internal.")

# The A records the baseline publishes inside techcorp.internal.
BASELINE_A_RECORDS = {
    "app":       {"address": "10.20.1.20", "comment": "Cutover target - INC-4471"},
    "api":       {"address": "10.20.1.21", "comment": "TechCorp API tier"},
    "intranet":  {"address": "10.20.1.22", "comment": "TechCorp intranet"},
    "branch-02": {"address": "10.20.2.10", "comment": "Branch-02 client host"},
}

BASELINE_CNAME_RECORDS = {
    "www":    {"target": "app.techcorp.internal.", "comment": "Alias for the app tier"},
    "portal": {"target": "app.techcorp.internal.", "comment": "Alias for the app tier"},
}

# The stale forward zone. The baseline points it at a resolver that works; the
# break repoints it at a decommissioned one.
FORWARD_ZONE_FQDN = os.environ.get("LAB_FORWARD_ZONE", "legacy.techcorp.internal.")
FORWARD_RESOLVER_GOOD = os.environ.get("LAB_FORWARD_RESOLVER_GOOD", "10.20.1.53")
FORWARD_RESOLVER_DECOMMISSIONED = os.environ.get(
    "LAB_FORWARD_RESOLVER_DEAD", "10.20.9.53"
)

# DNS config profile carrying techcorp.internal, and the NIOS-X host that serves
# the branch network. The Challenge 3 DNS break detaches the two.
DNS_CONFIG_PROFILE_NAME = os.environ.get("LAB_DNS_PROFILE", "techcorp-branch-dns")
BRANCH_HOST_NAME = os.environ.get("LAB_BRANCH_HOST", "niosx-branch-02")

# IPAM.
IP_SPACE_NAME = os.environ.get("LAB_IP_SPACE", "techcorp-ipam")
ADDRESS_BLOCK = {
    "address": os.environ.get("LAB_ADDRESS_BLOCK_ADDR", "10.20.0.0"),
    "cidr": int(os.environ.get("LAB_ADDRESS_BLOCK_CIDR", "16")),
    "name": "TechCorp Corporate",
}

SUBNETS = {
    "hq-01": {
        "address": "10.20.1.0", "cidr": 24,
        "name": "HQ-01", "comment": "Headquarters server segment",
    },
    "branch-02": {
        "address": "10.20.2.0", "cidr": 24,
        "name": "Branch-02", "comment": "Branch office client segment - INC-4472",
    },
}

# Branch-02 DHCP range. Healthy baseline: .100-.200, clear of the reserved block.
BRANCH_RANGE_HEALTHY = {
    "start": os.environ.get("LAB_BRANCH_RANGE_START", "10.20.2.100"),
    "end": os.environ.get("LAB_BRANCH_RANGE_END", "10.20.2.200"),
    "name": "Branch-02 DHCP",
}

# The reserved fixed-address block that the IPAM cleanup was supposed to avoid.
BRANCH_RESERVED_BLOCK = {
    "start": os.environ.get("LAB_RESERVED_START", "10.20.2.10"),
    "end": os.environ.get("LAB_RESERVED_END", "10.20.2.30"),
    "name": "Branch-02 reserved infrastructure",
}

# Broken state: the range was "narrowed" onto the reserved block, so effectively
# every address in it is already taken and no lease can be issued.
BRANCH_RANGE_BROKEN = {
    "start": os.environ.get("LAB_BROKEN_RANGE_START", "10.20.2.10"),
    "end": os.environ.get("LAB_BROKEN_RANGE_END", "10.20.2.30"),
    "name": "Branch-02 DHCP",
}

# Challenge 2 and Challenge 3 both key off this. Challenge 2 asks which ranges
# are above it — so at least one range must be, or that prompt returns nothing
# and the challenge is hollow. Challenge 3 passes only once Branch-02
# utilization has come back under it.
UTILIZATION_THRESHOLD_PCT = float(os.environ.get("LAB_UTIL_THRESHOLD", "90"))

# Challenge 5's unguided break: the zone gets associated with the wrong view.
DECOY_DNS_VIEW_NAME = os.environ.get("LAB_DECOY_DNS_VIEW", "techcorp-legacy-view")
C5_ZONE_FQDN = os.environ.get("LAB_C5_ZONE", "partners.techcorp.internal.")

# The client host the learner digs from and requests a lease on.
CLIENT_HOST_NAME = os.environ.get("LAB_CLIENT_HOST", "branch-02-client")
CLIENT_HOST_MAC = os.environ.get("LAB_CLIENT_MAC", "02:42:0a:14:02:0a")

# Tag stamped on everything the seeder creates, so teardown can find its own
# objects and leave anything else in the tenant alone.
LAB_TAG_KEY = "instruqt-lab"
LAB_TAG_VALUE = os.environ.get("LAB_TAG_VALUE", TRACK_SLUG)


# --------------------------------------------------------------------------- #
# CSP REST path registry
# --------------------------------------------------------------------------- #
#
# CONFIRMED — in production use elsewhere in this estate today.

PATHS = {
    # Identity / session. From user_provision.py and create_user.py.
    "signin":          "/v2/session/users/sign_in",
    "account_switch":  "/v2/session/account_switch",
    "groups":          "/v2/groups",
    "users":           "/v2/users",
    "user_password":   "/v2/users/{user_id}/password",
    "current_account": "/v2/current_account",
    "current_user":    "/v2/current_user",

    # API keys. `current_api_keys` mints a key for the *calling* identity
    # (deploy_api_key.py). `iam/v2/keys` lists and deletes
    # (delete_azure_credential_from_file.py).
    "current_api_keys": "/v2/current_api_keys",
    "iam_keys":         "/api/iam/v2/keys",
    "iam_key_by_id":    "/api/iam/v2/keys/{key_id}",

    # ---------------------------------------------------------------------- #
    # DDI — confirmed against Infoblox's own OpenAPI-generated Go client,
    # github.com/infobloxopen/universal-ddi-go-client (packages dnsconfig,
    # dnsdata, ipam). Base path for all of these is /api/ddi/v1.
    # ---------------------------------------------------------------------- #

    # DNS configuration (dnsconfig package).
    "dns_view":          "/api/ddi/v1/dns/view",
    "dns_view_id":       "/api/ddi/v1/dns/view/{view_id}",
    "dns_auth_zone":     "/api/ddi/v1/dns/auth_zone",
    "dns_forward_zone":  "/api/ddi/v1/dns/forward_zone",
    "zone_child":        "/api/ddi/v1/dns/zone_child",

    # The DNS config profile IS the Server object. This is the single most
    # important thing the API docs settled: in Universal DDI there is no
    # separate "config profile" resource — /dns/server is it, and a Host
    # carries a `server` field naming the one attached to it.
    "dns_config_profile": "/api/ddi/v1/dns/server",

    # The DNS-side view of a host, carrying the attached config profile. The
    # HOST owns the association: PATCH /dns/host/{id} with {"server": <id>}.
    # baseline.attach_profile_to_host(), breaks.break_dns_profile_detach() and
    # verify_lab.check_c3() all read and write this same field.
    "dns_host":          "/api/ddi/v1/dns/host",

    # DNS records (dnsdata package). `type` is the textual mnemonic ("A",
    # "CNAME"); `rdata` is a type-specific map — {"address": ...} for A,
    # {"cname": ...} for CNAME.
    "dns_record":        "/api/ddi/v1/dns/record",

    # IPAM and DHCP (ipam package — note DHCP objects live under /dhcp).
    "ipam_ip_space":     "/api/ddi/v1/ipam/ip_space",
    "ipam_address_block": "/api/ddi/v1/ipam/address_block",
    "ipam_subnet":       "/api/ddi/v1/ipam/subnet",
    "dhcp_range":        "/api/ddi/v1/ipam/range",
    "dhcp_fixed_address": "/api/ddi/v1/dhcp/fixed_address",

    # Infrastructure (inframgmt package — base path /api/infra/v1).
    "infra_hosts":        "/api/infra/v1/hosts",
    "infra_services":     "/api/infra/v1/services",
    "infra_detail_hosts": "/api/infra/v1/detail_hosts",

    # ---------------------------------------------------------------------- #
    # UNCONFIRMED — still questions for the API docs.
    # Each Todo id maps to a row in README.md "TODO punch list".
    # ---------------------------------------------------------------------- #

    "mcp_role_group": Todo(
        "TODO-02",
        "What are the exact CSP group/role names that grant (a) MCP Server "
        "read-only access and (b) MCP Server read/write access? PTCI-4674 "
        "describes the split; provision_mcp_keys.py needs the literal names to "
        "match against GET /v2/groups. Override without editing code by setting "
        "MCP_RO_GROUP and MCP_RW_GROUP.",
    ),
    "dhcp_lease": Todo(
        "TODO-15",
        "Which endpoint LISTS active DHCP leases? The ipam package only exposes "
        "POST /dhcp/leases_command (for clearing leases) — there is no lease-list "
        "operation in the public Go client, so this needs answering from the CSP "
        "API docs directly. Challenge 3's final check wants to prove a lease was "
        "issued to the branch client host.",
    ),

    "dns_activity_cube": Todo(
        "TODO-16",
        "Which endpoint backs the DNS activity analytics the Challenge 2 and 4 "
        "prompts rely on — top queried domains, NXDOMAIN by client, query failures "
        "over 24h? The MCP setup guide calls these analytics cubes; the checks need "
        "the REST path, the time-window parameter and the response shape.",
    ),
    "mcp_auth_events": Todo(
        "TODO-17",
        "How can we confirm via API that a given Service API key has authenticated "
        "to https://csp.infoblox.com/mcp at least once? Is there an audit-log or "
        "key-last-used endpoint? Lower priority now: the lab runs its own agent, "
        "so check_c1 can fall back to confirming the key itself authenticates, "
        "which it already does.",
    ),
    "audit_log": Todo(
        "TODO-18",
        "What is the REST path for the CSP audit log, and which filter isolates "
        "changes made by a specific service user? Challenge 4 asks the learner to "
        "find their own changes in the Portal; the check should confirm the records "
        "exist.",
    ),
}


def path(key, **fmt):
    """
    Resolve a registry key to a REST path.

    Raises LabTodo — loudly, with the question — for anything unconfirmed.
    """
    try:
        value = PATHS[key]
    except KeyError:
        raise KeyError(f"No CSP path registered under {key!r}") from None

    if is_todo(value):
        raise LabTodo(f"[{value.ident}] {value.question}")

    return value.format(**fmt) if fmt else value


def require_env():
    """Fail fast with one message naming every missing variable."""
    missing = [
        name for name, value in (
            ("INFOBLOX_EMAIL", INFOBLOX_EMAIL),
            ("INFOBLOX_PASSWORD", INFOBLOX_PASSWORD),
            ("INSTRUQT_PARTICIPANT_ID", PARTICIPANT_ID),
        ) if not value
    ]
    if missing:
        raise SystemExit(
            "❌ Missing required environment variables: " + ", ".join(missing) +
            "\n   See uddi-mcp-lab/README.md for the full list."
        )
