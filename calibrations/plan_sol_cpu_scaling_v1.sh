#!/bin/bash

# Materialize RG-CAL-002 without allocating cluster resources or transferring
# artifacts. Review the resulting plan before running the separate submitter.

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

if [[ "$#" -ne 0 ]]; then
    fail "this versioned plan launcher accepts no arguments; observed_count=$#. Usage: bash calibrations/plan_sol_cpu_scaling_v1.sh"
fi

inherited_sbatch_variables=$(compgen -A variable SBATCH_ || true)
[[ -z "$inherited_sbatch_variables" ]] || \
    fail "inherited SBATCH_* variables are forbidden; variables=$inherited_sbatch_variables"

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
python_path="$repo_root/.venv/bin/python"
config_path="$script_dir/sol_cpu_scaling_v1.json"
run_id=sol-cpu-scaling-v1
scratch_root=/scratch/pdressla/retro-gol/calibrations
plan_dir="$scratch_root/plans/$run_id"
plan_staging_dir="$scratch_root/plans/.$run_id.staging"
thread_count=1

for command_name in git sha256sum; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required command is unavailable; command=$command_name"
done
[[ -x "$python_path" ]] || \
    fail "embedded Python interpreter is not executable; expected=$python_path"
[[ -f "$config_path" ]] || \
    fail "tracked scaling configuration is missing; expected=$config_path"
[[ "$scratch_root" == /* && "$scratch_root" != / ]] || \
    fail "scratch root must be an absolute path other than /; observed=$scratch_root"
[[ ! -e "$plan_dir" ]] || \
    fail "materialized plan path already exists and requires explicit inspection; path=$plan_dir run_id=$run_id"
[[ ! -e "$plan_staging_dir" ]] || \
    fail "plan staging path already exists and requires explicit inspection; path=$plan_staging_dir run_id=$run_id"

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || \
    fail "plan materialization requires a clean Git checkout; repo=$repo_root dirty_paths=$dirty_paths"
revision=$(git rev-parse --verify HEAD)

"$python_path" - "$config_path" "$run_id" <<'PYTHON'
import importlib.metadata
import json
import sys

config_path, expected_run_id = sys.argv[1:]
with open(config_path, encoding="utf-8") as config_file:
    config = json.load(config_file)
expected = {
    "run_id": expected_run_id,
    "purpose": "sol_cpu_scaling_calibration",
    "backup_mode": "required_private_hf",
}
for name, expected_value in expected.items():
    observed_value = config.get(name)
    if observed_value != expected_value:
        raise SystemExit(
            "ERROR: scaling configuration differs from the tracked campaign; "
            f"field={name} expected={expected_value!r} observed={observed_value!r} "
            f"path={config_path}"
        )
if sys.version_info[:2] < (3, 12):
    raise SystemExit(
        f"ERROR: Python >=3.12 is required; observed={sys.version_info[:2]}"
    )
numpy_version = importlib.metadata.version("numpy")
if numpy_version.split(".")[0] != "2":
    raise SystemExit(
        f"ERROR: NumPy 2.x is required; observed={numpy_version}"
    )
PYTHON

export OMP_NUM_THREADS="$thread_count"
export OPENBLAS_NUM_THREADS="$thread_count"
export MKL_NUM_THREADS="$thread_count"
export VECLIB_MAXIMUM_THREADS="$thread_count"
export NUMEXPR_NUM_THREADS="$thread_count"
export PYTHONHASHSEED=0

printf 'Running the complete local test suite before materializing run_id=%s\n' "$run_id"
"$python_path" -m unittest discover -s tests -v

mkdir -p -- "$scratch_root/plans"
printf 'Materializing immutable master plan at %s\n' "$plan_dir"
"$python_path" -m retro_gol \
    --mode plan \
    --config "$config_path" \
    --output-dir "$plan_dir"

[[ -f "$plan_dir/plan.json" ]] || \
    fail "plan command returned without plan.json; expected=$plan_dir/plan.json"
[[ -f "$plan_dir/manifest.json" ]] || \
    fail "plan command returned without manifest.json; expected=$plan_dir/manifest.json"
[[ -f "$plan_dir/PLAN_COMPLETE" ]] || \
    fail "plan command returned without PLAN_COMPLETE; expected=$plan_dir/PLAN_COMPLETE"

config_sha256=$(sha256sum -- "$config_path")
plan_sha256=$(sha256sum -- "$plan_dir/plan.json")
manifest_sha256=$(sha256sum -- "$plan_dir/manifest.json")
marker_sha256=$(sha256sum -- "$plan_dir/PLAN_COMPLETE")

printf '\nPlan materialized; no Slurm job or remote operation was started.\n'
printf 'run_id=%s\n' "$run_id"
printf 'git_revision=%s\n' "$revision"
printf 'config=%s\nconfig_sha256=%s\n' "$config_path" "${config_sha256%% *}"
printf 'plan=%s\nplan_sha256=%s\n' "$plan_dir/plan.json" "${plan_sha256%% *}"
printf 'manifest_sha256=%s\n' "${manifest_sha256%% *}"
printf 'marker_sha256=%s\n' "${marker_sha256%% *}"
printf '%s\n' \
    "Review the full plan and METHODS.md decision RG-CAL-002 before invoking:" \
    "  bash calibrations/submit_sol_cpu_scaling_v1.sh"
