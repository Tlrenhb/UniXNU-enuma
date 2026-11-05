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
import json, subprocess, sys
token = open('/root/.gh_token').read().strip() if __import__('os').path.exists('/root/.gh_token') else ''
request = ['curl', '-sS', '-m', '30', '-H', f'Authorization: Bearer {token}',
           'https://api.github.com/repos/Tlrenhb/UniXNU-enuma/actions/runs?per_page=8']
runs = json.loads(subprocess.run(request, capture_output=True, text=True).stdout)['workflow_runs']
green = False
for run in runs:
    if run['head_branch'] != sys.argv[1]:
        continue
    print(f"  {run['id']} {run['name'][:34]:34s} {run['status']:10s} {run['conclusion']}")
    if run['name'].startswith('format2') and run['conclusion'] == 'success':
        green = True
print('a green format2 run exists' if green else 'NO green format2 run yet')
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
