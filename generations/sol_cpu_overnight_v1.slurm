#!/bin/bash

# Resource directives are supplied only by the tracked zero-argument parent.
set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

[[ "$#" -eq 24 ]] || fail "expected twenty-four parent-supplied arguments; observed_count=$#"
repo_root=$1
python_path=$2
scratch_root=$3
config_path=$4
parent_script=$5
worker_script=$6
run_id=$7
plan_id=$8
attempt_id=$9
expected_job_name=${10}
expected_account=${11}
expected_partition=${12}
expected_qos=${13}
expected_nodes=${14}
expected_ntasks=${15}
expected_cpus_per_task=${16}
expected_mem_per_cpu_mib=${17}
expected_slurm_wall_time=${18}
scientific_wall_time_seconds=${19}
deadline_reserve_seconds=${20}
control_path=${21}
expected_config_sha256=${22}
expected_plan_sha256=${23}
expected_revision=${24}

[[ -n "${SLURM_JOB_ID:-}" ]] || fail "SLURM_JOB_ID is missing; overnight worker requires Slurm"
[[ "${SLURM_JOB_NAME:-}" == "$expected_job_name" ]] || fail "Slurm job name mismatch; expected=$expected_job_name observed=${SLURM_JOB_NAME:-missing}"
[[ "${SLURM_JOB_ACCOUNT:-}" == "$expected_account" ]] || fail "Slurm account mismatch; expected=$expected_account observed=${SLURM_JOB_ACCOUNT:-missing}"
[[ "${SLURM_JOB_PARTITION:-}" == "$expected_partition" ]] || fail "Slurm partition mismatch; expected=$expected_partition observed=${SLURM_JOB_PARTITION:-missing}"
[[ "${SLURM_JOB_QOS:-}" == "$expected_qos" ]] || fail "Slurm QoS mismatch; expected=$expected_qos observed=${SLURM_JOB_QOS:-missing}"
[[ "${SLURM_JOB_NUM_NODES:-}" == "$expected_nodes" ]] || fail "Slurm node count mismatch; expected=$expected_nodes observed=${SLURM_JOB_NUM_NODES:-missing}"
[[ "${SLURM_NTASKS:-}" == "$expected_ntasks" ]] || fail "Slurm task count mismatch; expected=$expected_ntasks observed=${SLURM_NTASKS:-missing}"
[[ "${SLURM_CPUS_PER_TASK:-}" == "$expected_cpus_per_task" ]] || fail "Slurm CPU count mismatch; expected=$expected_cpus_per_task observed=${SLURM_CPUS_PER_TASK:-missing}"
[[ "${SLURM_MEM_PER_CPU:-}" == "$expected_mem_per_cpu_mib" ]] || fail "Slurm memory mismatch; expected_MiB=$expected_mem_per_cpu_mib observed=${SLURM_MEM_PER_CPU:-missing}"
[[ -z "${SLURM_JOB_GPUS:-}" && -z "${SLURM_GPUS:-}" ]] || fail "overnight CPU worker unexpectedly received GPU resources"
[[ "$expected_ntasks" == 8 && "$expected_cpus_per_task" == 1 ]] || fail "overnight worker is pinned to eight one-CPU tasks; observed ntasks=$expected_ntasks cpus_per_task=$expected_cpus_per_task"

for command_name in date git hostname lscpu mv sha256sum scontrol srun; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is unavailable; command=$command_name"
done
[[ -x /usr/bin/time ]] || fail "GNU time is unavailable; expected=/usr/bin/time"
[[ -x "$python_path" ]] || fail "Python path is not executable; expected=$python_path"
for required_file in "$config_path" "$parent_script" "$worker_script"; do
    [[ -f "$required_file" ]] || fail "required tracked file is missing; expected=$required_file"
done

plan_dir="$scratch_root/plans/$plan_id"
run_root="$scratch_root/runs/$run_id/$attempt_id"
job_dir="$run_root/job"
log_root="$scratch_root/logs/$run_id/$attempt_id"
[[ -d "$plan_dir" ]] || fail "overnight plan directory is missing; expected=$plan_dir"
[[ -f "$plan_dir/plan.json" && -f "$plan_dir/manifest.json" && -f "$plan_dir/PLAN_COMPLETE" ]] || fail "overnight plan is incomplete; expected files below $plan_dir"
[[ -d "$run_root" ]] || fail "overnight run root is missing; expected=$run_root"
[[ ! -e "$job_dir/COMPUTE_COMPLETE" ]] || fail "overnight compute completion already exists; path=$job_dir/COMPUTE_COMPLETE"

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || fail "overnight worker requires a clean checkout; dirty_paths=$dirty_paths"
observed_revision=$(git rev-parse --verify HEAD)
[[ "$observed_revision" == "$expected_revision" ]] || fail "Git revision changed after submission; expected=$expected_revision observed=$observed_revision"
checksum_record=$(sha256sum -- "$config_path")
[[ "${checksum_record%% *}" == "$expected_config_sha256" ]] || fail "overnight configuration checksum changed after submission"
checksum_record=$(sha256sum -- "$plan_dir/plan.json")
[[ "${checksum_record%% *}" == "$expected_plan_sha256" ]] || fail "overnight plan checksum changed after submission"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONHASHSEED=0
export SLURM_EXPORT_ENV=ALL

mkdir -p -- "$job_dir" "$log_root" "$(dirname -- "$control_path")"
if [[ -e "$control_path" ]]; then
    fail "overnight control path already exists; inspect before reuse; path=$control_path"
fi
start_unix=$(date +%s)
deadline_unix=$((start_unix + scientific_wall_time_seconds))
{
    printf 'started_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'deadline_unix=%s\nscientific_wall_time_seconds=%s\n' "$deadline_unix" "$scientific_wall_time_seconds"
    printf 'deadline_reserve_seconds=%s\nslurm_wall_time=%s\n' "$deadline_reserve_seconds" "$expected_slurm_wall_time"
    printf 'control_path=%s\nrun_id=%s\nattempt_id=%s\n' "$control_path" "$run_id" "$attempt_id"
    printf 'slurm_job_id=%s\nslurm_job_name=%s\naccount=%s\npartition=%s\nqos=%s\n' "$SLURM_JOB_ID" "$SLURM_JOB_NAME" "$SLURM_JOB_ACCOUNT" "$SLURM_JOB_PARTITION" "$SLURM_JOB_QOS"
    printf 'nodes=%s\nntasks=%s\ncpus_per_task=%s\nmem_per_cpu_mib=%s\n' "$SLURM_JOB_NUM_NODES" "$SLURM_NTASKS" "$SLURM_CPUS_PER_TASK" "$expected_mem_per_cpu_mib"
    printf 'repo_root=%s\ngit_revision=%s\npython_path=%s\nconfig_path=%s\nplan_path=%s\n' "$repo_root" "$observed_revision" "$python_path" "$config_path" "$plan_dir/plan.json"
    sha256sum -- "$config_path" "$plan_dir/plan.json" "$parent_script" "$worker_script"
    "$python_path" --version
    "$python_path" -c 'import numpy; print("numpy=" + numpy.__version__)'
    hostname
    lscpu
    scontrol show job -dd "$SLURM_JOB_ID"
} > "$job_dir/slurm-context.txt"
printf 'PAUSE or STOP control path: %s\n' "$control_path"
printf 'Inspect: test -f %q && cat %q\n' "$control_path" "$control_path"
printf 'Request PAUSE: printf PAUSE > %q.tmp && mv -- %q.tmp %q\n' "$control_path" "$control_path" "$control_path"
printf 'Request STOP: printf STOP > %q.tmp && mv -- %q.tmp %q\n' "$control_path" "$control_path" "$control_path"

multiprog_path="$job_dir/overnight-multiprog.conf"
multiprog_tmp="$job_dir/.overnight-multiprog.conf.tmp"
[[ ! -e "$multiprog_tmp" ]] || fail "overnight multiprogram staging path already exists; inspect before reuse; path=$multiprog_tmp"
for ((worker_index = 0; worker_index < expected_ntasks; worker_index++)); do
    worker_dir="$run_root/worker-$(printf '%03d' "$worker_index")"
    [[ ! -e "$worker_dir" ]] || fail "overnight worker output already exists; worker_index=$worker_index path=$worker_dir"
    printf '%d %s -m retro_gol.overnight worker --config %s --plan %s/plan.json --output-dir %s --worker-index %d --worker-count %d --deadline-unix %s --control-path %s\n' \
        "$worker_index" "$python_path" "$config_path" "$plan_dir" "$worker_dir" "$worker_index" "$expected_ntasks" "$deadline_unix" "$control_path" >> "$multiprog_tmp"
done
mv -- "$multiprog_tmp" "$multiprog_path"
sha256sum -- "$multiprog_path" > "$job_dir/overnight-multiprog.sha256"

/usr/bin/time -v -o "$job_dir/gnu-time-generation.txt" \
    srun --export=ALL \
        --ntasks="$expected_ntasks" \
        --cpus-per-task="$expected_cpus_per_task" \
        --cpu-bind=verbose,cores \
        --kill-on-bad-exit=1 \
        --multi-prog "$multiprog_path"

for ((worker_index = 0; worker_index < expected_ntasks; worker_index++)); do
    worker_dir="$run_root/worker-$(printf '%03d' "$worker_index")"
    [[ -f "$worker_dir/WORKER_COMPLETE" && -f "$worker_dir/worker-manifest.json" ]] || fail "overnight worker did not publish completion evidence; worker_index=$worker_index expected=$worker_dir/WORKER_COMPLETE"
done
completion_tmp="$job_dir/.COMPUTE_COMPLETE.tmp"
{
    printf 'finished_at_utc=%s\nrun_id=%s\nattempt_id=%s\nslurm_job_id=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$attempt_id" "$SLURM_JOB_ID"
    printf 'deadline_unix=%s\nworker_count=%s\ncontrol_path=%s\n' "$deadline_unix" "$expected_ntasks" "$control_path"
    for ((worker_index = 0; worker_index < expected_ntasks; worker_index++)); do
        worker_dir="$run_root/worker-$(printf '%03d' "$worker_index")"
        sha256sum -- "$worker_dir/worker-manifest.json" "$worker_dir/WORKER_COMPLETE"
    done
} > "$completion_tmp"
mv -- "$completion_tmp" "$job_dir/COMPUTE_COMPLETE"
printf 'Completed overnight generation run_root=%s; finalizer may now aggregate and transfer artifacts\n' "$run_root"
