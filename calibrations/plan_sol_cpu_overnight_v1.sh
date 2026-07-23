#!/bin/bash

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

[[ "$#" -eq 0 ]] || fail "this versioned overnight planner accepts no arguments; observed_count=$#"
inherited_sbatch_variables=$(compgen -A variable SBATCH_ || true)
[[ -z "$inherited_sbatch_variables" ]] || fail "inherited SBATCH_* variables are forbidden; variables=$inherited_sbatch_variables"

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
python_path="$repo_root/.venv/bin/python"
config_path="$script_dir/sol_cpu_overnight_v1.json"
scratch_root=/scratch/pdressla/retro-gol/calibrations
run_id=sol-cpu-overnight-v1
plan_id=sol-cpu-overnight-v1-plan-001
plan_root="$scratch_root/plans/$plan_id"
plan_staging_root="$scratch_root/plans/.$plan_id.staging"

for command_name in git sha256sum; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is unavailable; command=$command_name"
done
[[ -x "$python_path" ]] || fail "repository Python is not executable; expected=$python_path"
[[ -f "$config_path" ]] || fail "overnight configuration is missing; expected=$config_path"
[[ "$repo_root" != *[[:space:]]* && "$scratch_root" != *[[:space:]]* ]] || fail "overnight paths must not contain whitespace"

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || fail "overnight planning requires a clean Git checkout; dirty_paths=$dirty_paths"
revision=$(git rev-parse --verify HEAD)
[[ ! -e "$plan_root" ]] || fail "overnight plan already exists and requires explicit inspection; path=$plan_root"
[[ ! -e "$plan_staging_root" ]] || fail "overnight plan staging already exists and requires explicit inspection; path=$plan_staging_root"

printf 'Running the focused test suite before overnight planning\n'
"$python_path" -m unittest discover -s tests -q

mkdir -p -- "$scratch_root/plans"
mkdir -- "$plan_staging_root"
"$python_path" - "$config_path" "$plan_staging_root" "$run_id" "$plan_id" "$revision" <<'PYTHON'
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from retro_gol.overnight import _sha256, _write_json, build_plan, load_config

config_path = Path(sys.argv[1]).resolve()
staging_root = Path(sys.argv[2]).resolve()
expected_run_id, expected_plan_id, revision = sys.argv[3:]
config = load_config(config_path)
if config["run_id"] != expected_run_id:
    raise SystemExit(
        f"ERROR: overnight configuration run_id differs; expected={expected_run_id}, observed={config['run_id']!r}"
    )
plan = build_plan(config, config_path)
plan_path = staging_root / "plan.json"
_write_json(plan_path, plan)
manifest = {
    "schema_version": 1,
    "run_id": expected_run_id,
    "plan_id": expected_plan_id,
    "status": "planned",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "git_revision": revision,
    "config_sha256": _sha256(config_path),
    "plan_sha256": _sha256(plan_path),
    "environment": {
        "python": sys.version,
        "numpy": __import__("numpy").__version__,
        "platform": platform.platform(),
    },
}
manifest_path = staging_root / "manifest.json"
_write_json(manifest_path, manifest)
_write_json(staging_root / "PLAN_COMPLETE", {"manifest_sha256": _sha256(manifest_path)})
PYTHON
mv -- "$plan_staging_root" "$plan_root"
printf 'Planned overnight run=%s plan_id=%s plan=%s/plan.json\n' "$run_id" "$plan_id" "$plan_root"
