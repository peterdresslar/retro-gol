#!/bin/bash

# Resource directives are supplied only by the tracked zero-argument parent.

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

if [[ "$#" -ne 22 ]]; then
    fail "expected twenty-two parent-supplied arguments; observed_count=$#. Submit only through calibrations/submit_sol_private_backup_smoke_v1.sh"
fi

repo_root=$1
python_path=$2
scratch_root=$3
config_path=$4
parent_source_path=$5
compute_source_path=$6
run_id=$7
thread_count=$8
expected_job_name=$9
expected_account=${10}
expected_partition=${11}
expected_qos=${12}
expected_nodes=${13}
expected_ntasks=${14}
expected_cpus_per_task=${15}
expected_memory_mib=${16}
expected_wall_time=${17}
expected_revision=${18}
expected_config_sha256=${19}
expected_plan_sha256=${20}
expected_plan_manifest_sha256=${21}
expected_plan_marker_sha256=${22}

[[ -n "${SLURM_JOB_ID:-}" ]] || fail "SLURM_JOB_ID is missing"
[[ "${SLURM_JOB_NAME:-}" == "$expected_job_name" ]] || \
    fail "Slurm job name mismatch; expected=$expected_job_name observed=${SLURM_JOB_NAME:-missing}"
[[ "${SLURM_JOB_ACCOUNT:-}" == "$expected_account" ]] || \
    fail "Slurm account mismatch; expected=$expected_account observed=${SLURM_JOB_ACCOUNT:-missing}"
[[ "${SLURM_JOB_PARTITION:-}" == "$expected_partition" ]] || \
    fail "Slurm partition mismatch; expected=$expected_partition observed=${SLURM_JOB_PARTITION:-missing}"
[[ "${SLURM_JOB_QOS:-}" == "$expected_qos" ]] || \
    fail "Slurm QoS mismatch; expected=$expected_qos observed=${SLURM_JOB_QOS:-missing}"
[[ "${SLURM_JOB_NUM_NODES:-}" == "$expected_nodes" ]] || \
    fail "Slurm node count mismatch; expected=$expected_nodes observed=${SLURM_JOB_NUM_NODES:-missing}"
[[ "${SLURM_NTASKS:-}" == "$expected_ntasks" ]] || \
    fail "Slurm task count mismatch; expected=$expected_ntasks observed=${SLURM_NTASKS:-missing}"
[[ "${SLURM_CPUS_PER_TASK:-}" == "$expected_cpus_per_task" ]] || \
    fail "Slurm CPU count mismatch; expected=$expected_cpus_per_task observed=${SLURM_CPUS_PER_TASK:-missing}"
[[ "${SLURM_MEM_PER_NODE:-}" == "$expected_memory_mib" ]] || \
    fail "Slurm memory mismatch; expected_MiB=$expected_memory_mib observed=${SLURM_MEM_PER_NODE:-missing}"
[[ -z "${SLURM_JOB_GPUS:-}" && -z "${SLURM_GPUS:-}" ]] || \
    fail "CPU smoke unexpectedly received GPU resources"
[[ "$thread_count" == 1 && "$expected_ntasks" == 1 && "$expected_cpus_per_task" == 1 ]] || \
    fail "smoke requires one process, one CPU, and one library thread"

for command_name in date du git hostname lscpu sha256sum scontrol srun uname; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required command is unavailable; command=$command_name"
done
[[ -x /usr/bin/time ]] || fail "GNU time is unavailable; expected=/usr/bin/time"
[[ "$repo_root" == /* && -d "$repo_root" ]] || fail "invalid repo_root=$repo_root"
[[ "$python_path" == /* && -x "$python_path" ]] || fail "invalid python_path=$python_path"
[[ "$scratch_root" == /* && "$scratch_root" != / ]] || fail "invalid scratch_root=$scratch_root"
for required_file in "$config_path" "$parent_source_path" "$compute_source_path"; do
    [[ "$required_file" == /* && -f "$required_file" ]] || \
        fail "required tracked file is missing; expected=$required_file"
done

plan_dir="$scratch_root/plans/$run_id"
run_root="$scratch_root/runs/$run_id"
job_dir="$run_root/job"
result_dir="$run_root/result"
result_staging_dir="$run_root/.result.staging"
[[ -d "$job_dir" ]] || fail "parent job directory is missing; expected=$job_dir"
[[ ! -e "$result_dir" && ! -e "$result_staging_dir" ]] || \
    fail "smoke result or staging path already exists; result=$result_dir staging=$result_staging_dir"

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || fail "Git checkout changed after submission; dirty_paths=$dirty_paths"
observed_revision=$(git rev-parse --verify HEAD)
[[ "$observed_revision" == "$expected_revision" ]] || \
    fail "Git revision mismatch; expected=$expected_revision observed=$observed_revision"

for specification in \
    "$config_path:$expected_config_sha256" \
    "$plan_dir/plan.json:$expected_plan_sha256" \
    "$plan_dir/manifest.json:$expected_plan_manifest_sha256" \
    "$plan_dir/PLAN_COMPLETE:$expected_plan_marker_sha256"; do
    path=${specification%:*}
    expected_sha256=${specification##*:}
    [[ -f "$path" ]] || fail "required planned file is missing; expected=$path"
    checksum_record=$(sha256sum -- "$path")
    observed_sha256=${checksum_record%% *}
    [[ "$observed_sha256" == "$expected_sha256" ]] || \
        fail "planned file checksum mismatch; path=$path expected=$expected_sha256 observed=$observed_sha256"
done

"$python_path" - "$plan_dir/manifest.json" "$repo_root/uv.lock" <<'PYTHON'
import hashlib
import importlib.metadata
import json
import sys

manifest_path, lock_path = sys.argv[1:]
with open(manifest_path, encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
expected_python = manifest["environment"]["python"]
expected_numpy = manifest["environment"]["numpy"]
expected_lock_sha256 = manifest["environment"]["source_sha256"]["uv.lock"]
observed_python = sys.version
observed_numpy = importlib.metadata.version("numpy")
with open(lock_path, "rb") as lock_file:
    observed_lock_sha256 = hashlib.file_digest(lock_file, "sha256").hexdigest()
if observed_python != expected_python:
    raise SystemExit(
        "ERROR: Python identity changed after planning; "
        f"expected={expected_python!r} observed={observed_python!r}"
    )
if observed_numpy != expected_numpy:
    raise SystemExit(
        "ERROR: NumPy identity changed after planning; "
        f"expected={expected_numpy!r} observed={observed_numpy!r}"
    )
if observed_lock_sha256 != expected_lock_sha256:
    raise SystemExit(
        "ERROR: uv.lock changed after planning; "
        f"expected={expected_lock_sha256} observed={observed_lock_sha256}"
    )
PYTHON

export OMP_NUM_THREADS="$thread_count"
export OPENBLAS_NUM_THREADS="$thread_count"
export MKL_NUM_THREADS="$thread_count"
export VECLIB_MAXIMUM_THREADS="$thread_count"
export NUMEXPR_NUM_THREADS="$thread_count"
export PYTHONHASHSEED=0
export SLURM_EXPORT_ENV=ALL

context_path="$job_dir/compute-context.txt"
{
    printf 'started_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'hostname=%s\n' "$(hostname)"
    printf 'run_id=%s\nslurm_job_id=%s\nslurm_job_name=%s\n' \
        "$run_id" "$SLURM_JOB_ID" "$SLURM_JOB_NAME"
    printf 'account=%s\npartition=%s\nqos=%s\nnode_list=%s\n' \
        "$SLURM_JOB_ACCOUNT" "$SLURM_JOB_PARTITION" "$SLURM_JOB_QOS" \
        "${SLURM_JOB_NODELIST:-missing}"
    printf 'nodes=%s\ntasks=%s\ncpus_per_task=%s\nmemory_mib=%s\nwall_time=%s\n' \
        "$expected_nodes" "$expected_ntasks" "$expected_cpus_per_task" \
        "$expected_memory_mib" "$expected_wall_time"
    printf 'repo_root=%s\ngit_revision=%s\npython_path=%s\nthread_count=%s\n' \
        "$repo_root" "$observed_revision" "$python_path" "$thread_count"
    "$python_path" --version
    "$python_path" -c 'import numpy; print("numpy={}".format(numpy.__version__))'
    uname -a
    lscpu
    scontrol show job -dd "$SLURM_JOB_ID"
    sha256sum -- "$config_path" "$parent_source_path" "$compute_source_path"
} > "$context_path"

/usr/bin/time -v -o "$job_dir/gnu-time-compute.txt" \
    srun --export=ALL --ntasks=1 --cpus-per-task=1 \
    --cpu-bind=verbose,cores --kill-on-bad-exit=1 \
    "$python_path" -m retro_gol \
    --mode run \
    --config "$config_path" \
    --input-plan "$plan_dir/plan.json" \
    --output-dir "$result_dir"

/usr/bin/time -v -o "$job_dir/gnu-time-independent-verify.txt" \
    "$python_path" -c \
    'import sys; from pathlib import Path; from retro_gol.generate import verify_run; verify_run(Path(sys.argv[1]))' \
    "$result_dir"

completion_tmp="$job_dir/.COMPUTE_COMPLETE.tmp"
{
    printf 'finished_at_utc=%s\nrun_id=%s\nslurm_job_id=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$SLURM_JOB_ID"
    sha256sum -- "$result_dir/COMPLETE" "$result_dir/manifest.json" \
        "$result_dir/summary.json"
    du -sk -- "$result_dir"
} > "$completion_tmp"
mv -- "$completion_tmp" "$job_dir/COMPUTE_COMPLETE"
printf 'Completed RG-CAL-003 compute; result=%s\n' "$result_dir"
