#!/bin/bash

# This worker has no resource directives. The tracked zero-argument parent
# submit_sol_cpu_timing_v1.sh supplies every launch setting and required input.

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

if [[ "$#" -ne 20 ]]; then
    fail "expected twenty parent-supplied arguments; observed_count=$#. Submit only through calibrations/submit_sol_cpu_timing_v1.sh"
fi

repo_root=$1
shift
python_path=$1
shift
scratch_root=$1
shift
config_path=$1
shift
parent_source_path=$1
shift
worker_source_path=$1
shift
run_id=$1
shift
thread_count=$1
shift
expected_slurm_job_name=$1
shift
expected_slurm_account=$1
shift
expected_slurm_partition=$1
shift
expected_slurm_qos=$1
shift
expected_slurm_nodes=$1
shift
expected_slurm_ntasks=$1
shift
expected_slurm_cpus_per_task=$1
shift
expected_revision=$1
shift
expected_config_sha256=$1
shift
expected_plan_sha256=$1
shift
expected_plan_manifest_sha256=$1
shift
expected_plan_marker_sha256=$1

[[ -n "${SLURM_JOB_ID:-}" ]] || fail "SLURM_JOB_ID is missing; this script must run under Slurm"
[[ "${SLURM_JOB_NAME:-}" == "$expected_slurm_job_name" ]] || fail "Slurm job name differs from the parent; expected=$expected_slurm_job_name observed=${SLURM_JOB_NAME:-missing}"
[[ "${SLURM_JOB_ACCOUNT:-}" == "$expected_slurm_account" ]] || fail "Slurm account differs from the parent; expected=$expected_slurm_account observed=${SLURM_JOB_ACCOUNT:-missing}"
[[ "${SLURM_JOB_PARTITION:-}" == "$expected_slurm_partition" ]] || fail "Slurm partition differs from the parent; expected=$expected_slurm_partition observed=${SLURM_JOB_PARTITION:-missing}"
[[ "${SLURM_JOB_QOS:-}" == "$expected_slurm_qos" ]] || fail "Slurm QoS differs from the parent; expected=$expected_slurm_qos observed=${SLURM_JOB_QOS:-missing}"
[[ "${SLURM_JOB_NUM_NODES:-}" == "$expected_slurm_nodes" ]] || fail "Slurm node allocation differs from the parent; expected=$expected_slurm_nodes observed=${SLURM_JOB_NUM_NODES:-missing}"
[[ "$expected_slurm_ntasks" == 1 ]] || fail "the current serial generator requires one Slurm task; parent requested=$expected_slurm_ntasks"
[[ "$thread_count" == "$expected_slurm_cpus_per_task" ]] || fail "thread count must equal requested CPUs per task; thread_count=$thread_count cpus_per_task=$expected_slurm_cpus_per_task"
[[ "${SLURM_NTASKS:-}" == "$expected_slurm_ntasks" ]] || fail "Slurm task allocation differs from the parent; expected=$expected_slurm_ntasks observed=${SLURM_NTASKS:-missing}"
[[ "${SLURM_CPUS_PER_TASK:-}" == "$expected_slurm_cpus_per_task" ]] || fail "Slurm CPU allocation differs from the parent; expected=$expected_slurm_cpus_per_task observed=${SLURM_CPUS_PER_TASK:-missing}"
[[ "$repo_root" == /* && -d "$repo_root" ]] || fail "REPO_ROOT must be an existing absolute directory; observed=$repo_root"
[[ "$python_path" == /* && -x "$python_path" ]] || fail "PYTHON_PATH must be an executable absolute path; observed=$python_path"
[[ "$scratch_root" == /* && "$scratch_root" != / ]] || fail "SCRATCH_ROOT must be an absolute path other than /; observed=$scratch_root"
[[ "$config_path" == /* && -f "$config_path" ]] || fail "CONFIG_PATH must be an existing absolute file; observed=$config_path"
[[ "$parent_source_path" == /* && -f "$parent_source_path" ]] || fail "PARENT_SOURCE_PATH must be an existing absolute file; observed=$parent_source_path"
[[ "$worker_source_path" == /* && -f "$worker_source_path" ]] || fail "WORKER_SOURCE_PATH must be an existing absolute file; observed=$worker_source_path"

for command_name in git hostname lscpu sha256sum scontrol srun; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required command is unavailable; command=$command_name"
done
[[ -x /usr/bin/time ]] || fail "required GNU time executable is unavailable; expected=/usr/bin/time"

plan_dir="$scratch_root/plans/$run_id"
run_root="$scratch_root/runs/$run_id"
result_dir="$run_root/result"
result_staging_dir="$run_root/.result.staging"
job_dir="$run_root/job"

[[ -f "$plan_dir/plan.json" ]] || fail "materialized plan is missing; expected=$plan_dir/plan.json"
[[ -f "$plan_dir/manifest.json" ]] || fail "plan manifest is missing; expected=$plan_dir/manifest.json"
[[ -f "$plan_dir/PLAN_COMPLETE" ]] || fail "plan completion marker is missing; expected=$plan_dir/PLAN_COMPLETE"
[[ -d "$job_dir" ]] || fail "reserved job directory is missing; expected=$job_dir"
[[ ! -e "$result_dir" ]] || fail "result path already exists; expected absent=$result_dir"
[[ ! -e "$result_staging_dir" ]] || fail "result staging path already exists; expected absent=$result_staging_dir"

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || fail "Git checkout changed after submission; repo=$repo_root dirty_paths=$dirty_paths"
observed_revision=$(git rev-parse --verify HEAD)
[[ "$observed_revision" == "$expected_revision" ]] || \
    fail "Git revision changed after submission; expected=$expected_revision observed=$observed_revision"
checksum_record=$(sha256sum -- "$config_path")
observed_config_sha256=${checksum_record%% *}
[[ "$observed_config_sha256" == "$expected_config_sha256" ]] || \
    fail "configuration changed after submission; expected_sha256=$expected_config_sha256 observed_sha256=$observed_config_sha256 path=$config_path"

checksum_record=$(sha256sum -- "$plan_dir/plan.json")
observed_plan_sha256=${checksum_record%% *}
[[ "$observed_plan_sha256" == "$expected_plan_sha256" ]] || \
    fail "plan changed after submission; expected_sha256=$expected_plan_sha256 observed_sha256=$observed_plan_sha256 plan=$plan_dir/plan.json"
checksum_record=$(sha256sum -- "$plan_dir/manifest.json")
observed_plan_manifest_sha256=${checksum_record%% *}
[[ "$observed_plan_manifest_sha256" == "$expected_plan_manifest_sha256" ]] || \
    fail "plan manifest changed after submission; expected_sha256=$expected_plan_manifest_sha256 observed_sha256=$observed_plan_manifest_sha256 plan=$plan_dir/manifest.json"
checksum_record=$(sha256sum -- "$plan_dir/PLAN_COMPLETE")
observed_plan_marker_sha256=${checksum_record%% *}
[[ "$observed_plan_marker_sha256" == "$expected_plan_marker_sha256" ]] || \
    fail "plan marker changed after submission; expected_sha256=$expected_plan_marker_sha256 observed_sha256=$observed_plan_marker_sha256 plan=$plan_dir/PLAN_COMPLETE"

plan_config_sha256=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["source_config_sha256"])' \
    "$plan_dir/plan.json")
[[ "$plan_config_sha256" == "$expected_config_sha256" ]] || \
    fail "plan was built from another configuration; expected_sha256=$expected_config_sha256 observed_sha256=$plan_config_sha256 plan=$plan_dir/plan.json"
marker_manifest_sha256=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["manifest_sha256"])' \
    "$plan_dir/PLAN_COMPLETE")
[[ "$marker_manifest_sha256" == "$observed_plan_manifest_sha256" ]] || \
    fail "plan completion marker does not match its manifest; expected_sha256=$marker_manifest_sha256 observed_sha256=$observed_plan_manifest_sha256 plan=$plan_dir"
manifest_plan_sha256=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["plan_sha256"])' \
    "$plan_dir/manifest.json")
[[ "$manifest_plan_sha256" == "$observed_plan_sha256" ]] || \
    fail "materialized plan does not match its manifest; expected_sha256=$manifest_plan_sha256 observed_sha256=$observed_plan_sha256 plan=$plan_dir/plan.json"
plan_revision=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["environment"]["git"]["revision"])' \
    "$plan_dir/manifest.json")
[[ "$plan_revision" == "$expected_revision" ]] || \
    fail "plan was built from another Git revision; expected=$expected_revision observed=$plan_revision plan=$plan_dir/manifest.json"
plan_run_id=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["run_id"])' \
    "$plan_dir/plan.json")
[[ "$plan_run_id" == "$run_id" ]] || \
    fail "plan run ID differs from the parent; expected=$run_id observed=$plan_run_id plan=$plan_dir/plan.json"
"$python_path" - "$plan_dir/manifest.json" <<'PYTHON'
import importlib.metadata
import json
import sys

with open(sys.argv[1], encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
expected_python = manifest["environment"]["python"]
expected_numpy = manifest["environment"]["numpy"]
observed_python = sys.version
observed_numpy = importlib.metadata.version("numpy")
if expected_python != observed_python:
    raise SystemExit(
        "ERROR: Python identity changed after planning; "
        f"expected={expected_python!r} observed={observed_python!r}"
    )
if expected_numpy != observed_numpy:
    raise SystemExit(
        "ERROR: NumPy identity changed after planning; "
        f"expected={expected_numpy!r} observed={observed_numpy!r}"
    )
PYTHON
plan_uv_lock_sha256=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["environment"]["source_sha256"]["uv.lock"])' \
    "$plan_dir/manifest.json")
checksum_record=$(sha256sum -- "$repo_root/uv.lock")
observed_uv_lock_sha256=${checksum_record%% *}
[[ "$plan_uv_lock_sha256" == "$observed_uv_lock_sha256" ]] || \
    fail "uv.lock differs from the planned environment; expected_sha256=$plan_uv_lock_sha256 observed_sha256=$observed_uv_lock_sha256 path=$repo_root/uv.lock"
export OMP_NUM_THREADS="$thread_count"
export OPENBLAS_NUM_THREADS="$thread_count"
export MKL_NUM_THREADS="$thread_count"
export VECLIB_MAXIMUM_THREADS="$thread_count"
export NUMEXPR_NUM_THREADS="$thread_count"
export PYTHONHASHSEED=0
export SLURM_EXPORT_ENV=ALL

context_path="$job_dir/slurm-context.txt"
import_time_path="$job_dir/gnu-time-import.txt"
time_path="$job_dir/gnu-time.txt"
verify_time_path="$job_dir/gnu-time-verify.txt"
completion_path="$job_dir/completion.txt"
for absent_path in \
    "$context_path" "$import_time_path" "$time_path" "$verify_time_path" \
    "$completion_path"; do
    [[ ! -e "$absent_path" ]] || fail "job evidence path already exists; expected absent=$absent_path"
done

{
    printf 'started_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'hostname=%s\n' "$(hostname)"
    printf 'run_id=%s\n' "$run_id"
    printf 'slurm_job_id=%s\n' "$SLURM_JOB_ID"
    printf 'slurm_job_name=%s\n' "${SLURM_JOB_NAME:-missing}"
    printf 'slurm_account=%s\n' "${SLURM_JOB_ACCOUNT:-missing}"
    printf 'slurm_partition=%s\n' "${SLURM_JOB_PARTITION:-missing}"
    printf 'slurm_qos=%s\n' "${SLURM_JOB_QOS:-missing}"
    printf 'slurm_nodelist=%s\n' "${SLURM_JOB_NODELIST:-missing}"
    printf 'slurm_ntasks=%s\n' "$SLURM_NTASKS"
    printf 'slurm_cpus_per_task=%s\n' "$SLURM_CPUS_PER_TASK"
    printf 'repo_root=%s\n' "$repo_root"
    printf 'git_revision=%s\n' "$observed_revision"
    printf 'python_path=%s\n' "$python_path"
    printf 'config_path=%s\n' "$config_path"
    printf 'config_sha256=%s\n' "$observed_config_sha256"
    printf 'plan_sha256=%s\n' "$observed_plan_sha256"
    printf 'plan_manifest_sha256=%s\n' "$observed_plan_manifest_sha256"
    printf 'plan_marker_sha256=%s\n' "$observed_plan_marker_sha256"
    printf 'plan_dir=%s\n' "$plan_dir"
    printf 'result_dir=%s\n' "$result_dir"
    printf 'thread_count=%s\n' "$thread_count"
    "$python_path" --version
    "$python_path" -c \
        'import importlib.metadata; print("numpy={}".format(importlib.metadata.version("numpy")))'
    uname -a
    lscpu
    scontrol show job -dd "$SLURM_JOB_ID"
    sha256sum -- "$config_path" "$parent_source_path" "$worker_source_path"
} > "$context_path"

printf 'Measuring standalone Python and NumPy import warmup.\n'
srun --export=ALL \
    --ntasks="$expected_slurm_ntasks" \
    --cpus-per-task="$expected_slurm_cpus_per_task" \
    --cpu-bind=verbose,cores \
    --kill-on-bad-exit=1 \
    /usr/bin/time -v -o "$import_time_path" \
    "$python_path" -c 'import numpy; print(f"numpy_imported={numpy.__version__}")'

printf 'Starting run_id=%s job_id=%s host=%s result=%s\n' \
    "$run_id" "$SLURM_JOB_ID" "$(hostname)" "$result_dir"
srun --export=ALL \
    --ntasks="$expected_slurm_ntasks" \
    --cpus-per-task="$expected_slurm_cpus_per_task" \
    --cpu-bind=verbose,cores \
    --kill-on-bad-exit=1 \
    /usr/bin/time -v -o "$time_path" \
    "$python_path" -m retro_gol \
    --mode run \
    --config "$config_path" \
    --input-plan "$plan_dir/plan.json" \
    --output-dir "$result_dir"

checksum_record=$(sha256sum -- "$result_dir/plan.json")
result_plan_sha256=${checksum_record%% *}
[[ "$result_plan_sha256" == "$expected_plan_sha256" ]] || \
    fail "retained run plan differs from the launched plan; expected_sha256=$expected_plan_sha256 observed_sha256=$result_plan_sha256 path=$result_dir/plan.json"

srun --export=ALL \
    --ntasks="$expected_slurm_ntasks" \
    --cpus-per-task="$expected_slurm_cpus_per_task" \
    --cpu-bind=verbose,cores \
    --kill-on-bad-exit=1 \
    /usr/bin/time -v -o "$verify_time_path" \
    "$python_path" -c \
    'import sys; from pathlib import Path; from retro_gol.generate import verify_run; verify_run(Path(sys.argv[1])); print(f"verified_run={sys.argv[1]}")' \
    "$result_dir"

completion_tmp="$job_dir/.completion.txt.tmp"
{
    printf 'finished_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'run_id=%s\n' "$run_id"
    printf 'slurm_job_id=%s\n' "$SLURM_JOB_ID"
    printf 'result_dir=%s\n' "$result_dir"
    sha256sum -- "$result_dir/COMPLETE" "$result_dir/manifest.json" "$result_dir/summary.json"
    du -sk -- "$result_dir"
} > "$completion_tmp"
mv -- "$completion_tmp" "$completion_path"

printf 'Completed and independently verified run_id=%s result=%s\n' "$run_id" "$result_dir"
printf '%s\n' \
    "After the job exits: sacct -j $SLURM_JOB_ID --units=K --format=JobIDRaw,JobName%28,Account,Partition,QOS,NodeList,AllocCPUS,NTasks,ElapsedRaw,TotalCPU,CPUTimeRAW,MaxRSS,State,ExitCode"
