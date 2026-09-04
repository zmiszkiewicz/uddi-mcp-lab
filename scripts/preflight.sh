#!/usr/bin/env bash
#
# Run before every `git push` to this repo.
#
# Catches the class of bug that otherwise only surfaces three minutes into a
# track start with a participant watching: a Python file that does not parse, a
# break with no matching assertion, or a registry key referenced by a script but
# never defined.
#
# Deliberately does NOT call CSP. It needs no credentials and runs offline.

set -uo pipefail
cd "$(dirname "$0")"

FAILED=0
step() { printf '\n\033[1m── %s\033[0m\n' "$1"; }
pass() { printf '   ✅ %s\n' "$1"; }
fail() { printf '   ❌ %s\n' "$1"; FAILED=1; }

# --------------------------------------------------------------------------- #
step "Python syntax"
if python3 -m py_compile ./*.py 2>/tmp/preflight_py.log; then
  pass "all scripts parse"
else
  fail "py_compile failed:"; sed 's/^/      /' /tmp/preflight_py.log
fi

# --------------------------------------------------------------------------- #
step "Module imports"
# lab_config and csp_client must import with no environment set at all —
# anything that reads a required env var at import time breaks `--help`.
if env -i PATH="$PATH" python3 -c "import lab_config, csp_client" 2>/tmp/preflight_import.log; then
  pass "lab_config and csp_client import cleanly with an empty environment"
else
  fail "import failed:"; sed 's/^/      /' /tmp/preflight_import.log
fi

# --------------------------------------------------------------------------- #
step "Break registry integrity"
python3 - <<'PY'
import sys
import breaks

problems = []
for name, spec in breaks.BREAKS.items():
    for required in ("challenge", "summary", "apply", "assert", "fix"):
        if required not in spec:
            problems.append(f"{name}: missing {required!r}")
    for callable_key in ("apply", "assert", "fix"):
        if callable_key in spec and not callable(spec[callable_key]):
            problems.append(f"{name}: {callable_key} is not callable")

# Every break must belong to a challenge that exists.
for name, spec in breaks.BREAKS.items():
    if spec.get("challenge") not in (3, 5):
        problems.append(f"{name}: challenge {spec.get('challenge')} is not 3 or 5")

if problems:
    for problem in problems:
        print(f"      {problem}")
    sys.exit(1)
print(f"      {len(breaks.BREAKS)} breaks, each with apply/assert/fix")
PY
if [ $? -eq 0 ]; then pass "break registry is well formed"; else fail "break registry is malformed"; fi

# --------------------------------------------------------------------------- #
step "Overlap helper"
python3 - <<'PY'
import sys
from breaks import ranges_overlap

cases = [
    ("10.20.2.10",  "10.20.2.30",  "10.20.2.10",  "10.20.2.30",  True),   # identical
    ("10.20.2.100", "10.20.2.200", "10.20.2.10",  "10.20.2.30",  False),  # disjoint
    ("10.20.2.20",  "10.20.2.120", "10.20.2.10",  "10.20.2.30",  True),   # partial
    ("10.20.2.30",  "10.20.2.40",  "10.20.2.10",  "10.20.2.30",  True),   # touching
]
bad = [c for c in cases if ranges_overlap(*c[:4]) is not c[4]]
if bad:
    for case in bad:
        print(f"      wrong result for {case}")
    sys.exit(1)
print(f"      {len(cases)} overlap cases correct")
PY
if [ $? -eq 0 ]; then pass "range overlap logic is correct"; else fail "range overlap logic is wrong"; fi

# --------------------------------------------------------------------------- #
step "Unresolved TODOs"
python3 - <<'PY'
import lab_config as cfg
todos = {k: v for k, v in cfg.PATHS.items() if cfg.is_todo(v)}
if todos:
    print(f"      {len(todos)} CSP path(s) still unconfirmed:")
    for key, todo in sorted(todos.items(), key=lambda kv: kv[1].ident):
        print(f"        {todo.ident}  {key}")
    print("      The lab CANNOT run end to end until these are filled in.")
else:
    print("      none — every CSP path is confirmed")
PY

# --------------------------------------------------------------------------- #
printf '\n'
if [ "$FAILED" -eq 0 ]; then
  printf '\033[1;32m✅ preflight passed\033[0m\n'
else
  printf '\033[1;31m❌ preflight failed — do not push\033[0m\n'
fi
exit "$FAILED"
