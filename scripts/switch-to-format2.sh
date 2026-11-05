#!/usr/bin/env bash
# Cut main over to the schema-2 board, but only if the format-2 pipeline is green.
#
# Format 1 keeps working on main today; the cut-over is one fast-forward once format2 builds a kernel,
# a payload and a boot image in CI (workflow "format2", step "Full release" = success).
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH=${1:-format2}
git fetch -q origin
git rev-parse --verify "$BRANCH" >/dev/null || { echo "no such branch: $BRANCH"; exit 1; }

echo "--- last five format2 runs"
python3 - "$BRANCH" <<'PY'
"""Green means: the run built a kernel twice, asserted the PMGR bridge and produced a payload.

A check-only run is also 'success', so the conclusion alone is not enough - the bridge assertion step
has to have run and passed, which only happens with full=true.
"""
import json, os, subprocess, sys

token = open('/root/.gh_token').read().strip() if os.path.exists('/root/.gh_token') else ''
def api(path):
    out = subprocess.run(['curl', '-sS', '-m', '30', '-H', f'Authorization: Bearer {token}',
                          f'https://api.github.com/repos/Tlrenhb/UniXNU-enuma{path}'],
                         capture_output=True, text=True).stdout
    return json.loads(out)

REQUIRED = 'Assert the PMGR bridge is not a no-op stub'
green = None
for run in api('/actions/runs?per_page=12')['workflow_runs']:
    if run['head_branch'] != sys.argv[1] or not run['name'].startswith('format2'):
        continue
    verdict = run['status'] == 'completed' and run['conclusion'] == 'success'
    has_assertion = False
    if verdict:
        for job in api(f"/actions/runs/{run['id']}/jobs")['jobs']:
            for step in job['steps']:
                if step['name'] == REQUIRED and step['conclusion'] == 'success':
                    has_assertion = True
    print(f"  {run['id']} {run['status']:10s} {str(run['conclusion']):8s} "
          f"bridge-assert={'yes' if has_assertion else 'no'}")
    if verdict and has_assertion:
        green = run['id']
        break
print(f'a green full format-2 run exists: {green}' if green else 'NO green full format-2 run yet')
sys.exit(0 if green else 2)
PY
status=$?
if [ "$status" -ne 0 ]; then
  echo "refusing the cut-over: the format-2 pipeline has not produced a green run yet"
  exit "$status"
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "refusing the cut-over: the working tree is dirty"; exit 3
fi
echo "--- fast-forwarding main to $BRANCH"
git checkout -q main
git merge --ff-only "$BRANCH"
git push -q origin main
echo "main is now schema 2 at $(git rev-parse --short HEAD)"
