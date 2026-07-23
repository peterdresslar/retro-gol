#!/bin/bash

# This common condition worker has no resource directives. The tracked
# zero-argument submitter supplies and records the complete allocation.

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

if [[ "$#" -ne 27 ]]; then
    fail "expected twenty-seven parent-supplied arguments; observed_count=$#. Submit only through calibrations/submit_sol_cpu_scaling_v1.sh"
fi

repo_root=$1
python_path=$2
scratch_root=$3
config_path=$4
parent_source_path=$5
worker_source_path=$6
run_id=$7
attempt_id=$8
thread_count=$9
expected_slurm_job_name=${10}
expected_slurm_account=${11}
expected_slurm_partition=${12}
expected_slurm_qos=${13}
expected_slurm_nodes=${14}
expected_slurm_ntasks=${15}
expected_slurm_cpus_per_task=${16}
expected_slurm_mem_per_cpu_mib=${17}
expected_slurm_wall_time=${18}
expected_revision=${19}
expected_config_sha256=${20}
expected_plan_sha256=${21}
expected_plan_manifest_sha256=${22}
expected_plan_marker_sha256=${23}
import_multiprog_path=${24}
expected_import_multiprog_sha256=${25}
generate_multiprog_path=${26}
expected_generate_multiprog_sha256=${27}

case "$expected_slurm_ntasks" in
    1|2|4|8) ;;
    *) fail "scaling condition must request W in {1,2,4,8}; observed W=$expected_slurm_ntasks" ;;
esac
printf -v condition_id 'w%02d' "$expected_slurm_ntasks"

[[ -n "${SLURM_JOB_ID:-}" ]] || \
    fail "SLURM_JOB_ID is missing; this script must run under Slurm"
[[ "${SLURM_JOB_NAME:-}" == "$expected_slurm_job_name" ]] || \
    fail "Slurm job name differs from the parent; expected=$expected_slurm_job_name observed=${SLURM_JOB_NAME:-missing}"
[[ "${SLURM_JOB_ACCOUNT:-}" == "$expected_slurm_account" ]] || \
    fail "Slurm account differs from the parent; expected=$expected_slurm_account observed=${SLURM_JOB_ACCOUNT:-missing}"
[[ "${SLURM_JOB_PARTITION:-}" == "$expected_slurm_partition" ]] || \
    fail "Slurm partition differs from the parent; expected=$expected_slurm_partition observed=${SLURM_JOB_PARTITION:-missing}"
[[ "${SLURM_JOB_QOS:-}" == "$expected_slurm_qos" ]] || \
    fail "Slurm QoS differs from the parent; expected=$expected_slurm_qos observed=${SLURM_JOB_QOS:-missing}"
[[ "${SLURM_JOB_NUM_NODES:-}" == "$expected_slurm_nodes" ]] || \
    fail "Slurm node allocation differs from the parent; expected=$expected_slurm_nodes observed=${SLURM_JOB_NUM_NODES:-missing}"
[[ "${SLURM_NTASKS:-}" == "$expected_slurm_ntasks" ]] || \
    fail "Slurm task allocation differs from the parent; expected=$expected_slurm_ntasks observed=${SLURM_NTASKS:-missing}"
[[ "${SLURM_CPUS_PER_TASK:-}" == "$expected_slurm_cpus_per_task" ]] || \
    fail "Slurm CPUs per task differ from the parent; expected=$expected_slurm_cpus_per_task observed=${SLURM_CPUS_PER_TASK:-missing}"
[[ "${SLURM_MEM_PER_CPU:-}" == "$expected_slurm_mem_per_cpu_mib" ]] || \
    fail "Slurm memory per CPU differs from the parent; expected_MiB=$expected_slurm_mem_per_cpu_mib observed=${SLURM_MEM_PER_CPU:-missing}"
[[ -z "${SLURM_JOB_GPUS:-}" && -z "${SLURM_GPUS:-}" ]] || \
    fail "CPU calibration unexpectedly received GPU resources; SLURM_JOB_GPUS=${SLURM_JOB_GPUS:-none} SLURM_GPUS=${SLURM_GPUS:-none}"
[[ "$thread_count" == 1 && "$expected_slurm_cpus_per_task" == 1 ]] || \
    fail "each process must use one requested CPU and one library thread; thread_count=$thread_count cpus_per_task=$expected_slurm_cpus_per_task"

[[ "$repo_root" == /* && -d "$repo_root" ]] || \
    fail "repo root must be an existing absolute directory; observed=$repo_root"
[[ "$python_path" == /* && -x "$python_path" ]] || \
    fail "Python path must be an executable absolute path; observed=$python_path"
[[ "$scratch_root" == /* && "$scratch_root" != / ]] || \
    fail "scratch root must be an absolute path other than /; observed=$scratch_root"
for required_file in \
    "$config_path" "$parent_source_path" "$worker_source_path" \
    "$import_multiprog_path" "$generate_multiprog_path"; do
    [[ "$required_file" == /* && -f "$required_file" ]] || \
        fail "required parent-recorded file is missing or not absolute; expected=$required_file"
done

for command_name in date git hostname lscpu sha256sum scontrol srun; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required command is unavailable; command=$command_name"
done
[[ -x /usr/bin/time ]] || \
    fail "required GNU time executable is unavailable; expected=/usr/bin/time"

plan_dir="$scratch_root/plans/$run_id"
run_root="$scratch_root/runs/$run_id/$attempt_id"
campaign_job_dir="$run_root/job"
case_root="$run_root/cases/$condition_id"
shard_root="$case_root/shards"
aggregate_dir="$case_root/aggregate"
aggregate_staging_dir="$case_root/.aggregate.staging"
case_job_dir="$case_root/job"

[[ -d "$campaign_job_dir" ]] || \
    fail "parent campaign record is missing; expected directory=$campaign_job_dir"
[[ ! -e "$case_root" ]] || \
    fail "condition path already exists and requires explicit inspection; expected absent=$case_root"
for required_plan_file in plan.json manifest.json PLAN_COMPLETE; do
    [[ -f "$plan_dir/$required_plan_file" ]] || \
        fail "materialized plan is incomplete; expected=$plan_dir/$required_plan_file"
done

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || \
    fail "Git checkout changed after submission; repo=$repo_root dirty_paths=$dirty_paths"
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
    fail "master plan changed after submission; expected_sha256=$expected_plan_sha256 observed_sha256=$observed_plan_sha256 path=$plan_dir/plan.json"
checksum_record=$(sha256sum -- "$plan_dir/manifest.json")
observed_plan_manifest_sha256=${checksum_record%% *}
[[ "$observed_plan_manifest_sha256" == "$expected_plan_manifest_sha256" ]] || \
    fail "plan manifest changed after submission; expected_sha256=$expected_plan_manifest_sha256 observed_sha256=$observed_plan_manifest_sha256 path=$plan_dir/manifest.json"
checksum_record=$(sha256sum -- "$plan_dir/PLAN_COMPLETE")
observed_plan_marker_sha256=${checksum_record%% *}
[[ "$observed_plan_marker_sha256" == "$expected_plan_marker_sha256" ]] || \
    fail "plan marker changed after submission; expected_sha256=$expected_plan_marker_sha256 observed_sha256=$observed_plan_marker_sha256 path=$plan_dir/PLAN_COMPLETE"
checksum_record=$(sha256sum -- "$import_multiprog_path")
observed_import_multiprog_sha256=${checksum_record%% *}
[[ "$observed_import_multiprog_sha256" == "$expected_import_multiprog_sha256" ]] || \
    fail "import multiprogram changed after submission; expected_sha256=$expected_import_multiprog_sha256 observed_sha256=$observed_import_multiprog_sha256 path=$import_multiprog_path"
checksum_record=$(sha256sum -- "$generate_multiprog_path")
observed_generate_multiprog_sha256=${checksum_record%% *}
[[ "$observed_generate_multiprog_sha256" == "$expected_generate_multiprog_sha256" ]] || \
    fail "generation multiprogram changed after submission; expected_sha256=$expected_generate_multiprog_sha256 observed_sha256=$observed_generate_multiprog_sha256 path=$generate_multiprog_path"

"$python_path" - \
    "$plan_dir/plan.json" "$plan_dir/manifest.json" "$plan_dir/PLAN_COMPLETE" \
    "$run_id" "$expected_config_sha256" "$observed_plan_sha256" \
    "$observed_plan_manifest_sha256" "$expected_revision" <<'PYTHON'
import hashlib
import importlib.metadata
import json
import sys

(
    plan_path,
    manifest_path,
    marker_path,
    expected_run_id,
    expected_config_sha256,
    expected_plan_sha256,
    expected_manifest_sha256,
    expected_revision,
) = sys.argv[1:]

with open(plan_path, "rb") as plan_file:
    plan_bytes = plan_file.read()
with open(plan_path, encoding="utf-8") as plan_file:
    plan = json.load(plan_file)
with open(manifest_path, encoding="utf-8") as manifest_file:
    manifest = json.load(manifest_file)
with open(marker_path, encoding="utf-8") as marker_file:
    marker = json.load(marker_file)

checks = {
    "plan run_id": (expected_run_id, plan.get("run_id")),
    "plan source_config_sha256": (
        expected_config_sha256,
        plan.get("source_config_sha256"),
    ),
    "manifest plan_sha256": (expected_plan_sha256, manifest.get("plan_sha256")),
    "marker manifest_sha256": (
        expected_manifest_sha256,
        marker.get("manifest_sha256"),
    ),
    "plan Git revision": (
        expected_revision,
        manifest.get("environment", {}).get("git", {}).get("revision"),
    ),
}
for context, (expected, observed) in checks.items():
    if observed != expected:
        raise SystemExit(
            f"ERROR: {context} mismatch; expected={expected!r} observed={observed!r}"
        )
if hashlib.sha256(plan_bytes).hexdigest() != expected_plan_sha256:
    raise SystemExit(
        "ERROR: Python checksum of the master plan differs from the parent; "
        f"expected={expected_plan_sha256} path={plan_path}"
    )
expected_python = manifest["environment"]["python"]
expected_numpy = manifest["environment"]["numpy"]
observed_python = sys.version
observed_numpy = importlib.metadata.version("numpy")
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
PYTHON

plan_uv_lock_sha256=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["environment"]["source_sha256"]["uv.lock"])' \
    "$plan_dir/manifest.json")
checksum_record=$(sha256sum -- "$repo_root/uv.lock")
observed_uv_lock_sha256=${checksum_record%% *}
[[ "$plan_uv_lock_sha256" == "$observed_uv_lock_sha256" ]] || \
    fail "uv.lock differs from the planned environment; expected_sha256=$plan_uv_lock_sha256 observed_sha256=$observed_uv_lock_sha256 path=$repo_root/uv.lock"

mkdir -p -- "$shard_root" "$case_job_dir"
for ((shard_index = 0; shard_index < expected_slurm_ntasks; shard_index++)); do
    printf -v shard_id 'shard-%03d' "$shard_index"
    mkdir -- "$shard_root/$shard_id"
done

failure_path="$case_job_dir/FAILURE.txt"
record_failure() {
    status=$?
    set +e
    if [[ ! -e "$failure_path" ]]; then
        failure_tmp="$case_job_dir/.FAILURE.txt.tmp"
        {
            printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            printf 'run_id=%s\ncondition_id=%s\nslurm_job_id=%s\n' \
                "$run_id" "$condition_id" "$SLURM_JOB_ID"
            printf 'exit_status=%s\nline=%s\ncommand=%s\n' \
                "$status" "${BASH_LINENO[0]:-unknown}" "${BASH_COMMAND:-unknown}"
        } > "$failure_tmp"
        mv -- "$failure_tmp" "$failure_path"
    fi
    exit "$status"
}
trap record_failure ERR

export OMP_NUM_THREADS="$thread_count"
export OPENBLAS_NUM_THREADS="$thread_count"
export MKL_NUM_THREADS="$thread_count"
export VECLIB_MAXIMUM_THREADS="$thread_count"
export NUMEXPR_NUM_THREADS="$thread_count"
export PYTHONHASHSEED=0
export SLURM_EXPORT_ENV=ALL

context_path="$case_job_dir/slurm-context.txt"
{
    printf 'started_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'run_id=%s\nattempt_id=%s\ncondition_id=%s\n' \
        "$run_id" "$attempt_id" "$condition_id"
    printf 'slurm_job_id=%s\nslurm_job_name=%s\n' \
        "$SLURM_JOB_ID" "${SLURM_JOB_NAME:-missing}"
    printf 'slurm_account=%s\nslurm_partition=%s\nslurm_qos=%s\n' \
        "${SLURM_JOB_ACCOUNT:-missing}" "${SLURM_JOB_PARTITION:-missing}" \
        "${SLURM_JOB_QOS:-missing}"
    printf 'slurm_nodelist=%s\nslurm_nodes=%s\nslurm_ntasks=%s\n' \
        "${SLURM_JOB_NODELIST:-missing}" "${SLURM_JOB_NUM_NODES:-missing}" \
        "$SLURM_NTASKS"
    printf 'slurm_cpus_per_task=%s\nslurm_mem_per_cpu_mib=%s\nslurm_gpus=0\n' \
        "$SLURM_CPUS_PER_TASK" "$SLURM_MEM_PER_CPU"
    printf 'slurm_wall_time=%s\nthread_count=%s\n' \
        "$expected_slurm_wall_time" "$thread_count"
    printf 'repo_root=%s\ngit_revision=%s\npython_path=%s\n' \
        "$repo_root" "$observed_revision" "$python_path"
    printf 'config_path=%s\nconfig_sha256=%s\n' \
        "$config_path" "$observed_config_sha256"
    printf 'master_plan_path=%s\nmaster_plan_sha256=%s\n' \
        "$plan_dir/plan.json" "$observed_plan_sha256"
    printf 'import_multiprog_path=%s\nimport_multiprog_sha256=%s\n' \
        "$import_multiprog_path" "$observed_import_multiprog_sha256"
    printf 'generate_multiprog_path=%s\ngenerate_multiprog_sha256=%s\n' \
        "$generate_multiprog_path" "$observed_generate_multiprog_sha256"
    "$python_path" --version
    "$python_path" -c \
        'import importlib.metadata; print("numpy={}".format(importlib.metadata.version("numpy")))'
    uname -a
    lscpu
    scontrol show job -dd "$SLURM_JOB_ID"
    sha256sum -- \
        "$config_path" "$parent_source_path" "$worker_source_path" \
        "$import_multiprog_path" "$generate_multiprog_path"
} > "$context_path"

printf 'Measuring independent Python/NumPy import for condition=%s W=%s\n' \
    "$condition_id" "$expected_slurm_ntasks"
/usr/bin/time -v -o "$case_job_dir/gnu-time-import-makespan.txt" \
    srun --export=ALL \
        --ntasks="$expected_slurm_ntasks" \
        --cpus-per-task="$expected_slurm_cpus_per_task" \
        --cpu-bind=verbose,cores \
        --kill-on-bad-exit=1 \
        --multi-prog "$import_multiprog_path"

printf 'Generating disjoint shards for condition=%s W=%s\n' \
    "$condition_id" "$expected_slurm_ntasks"
/usr/bin/time -v -o "$case_job_dir/gnu-time-generation-makespan.txt" \
    srun --export=ALL \
        --ntasks="$expected_slurm_ntasks" \
        --cpus-per-task="$expected_slurm_cpus_per_task" \
        --cpu-bind=verbose,cores \
        --kill-on-bad-exit=1 \
        --multi-prog "$generate_multiprog_path"

[[ ! -e "$aggregate_dir" ]] || \
    fail "condition aggregate already exists; expected absent=$aggregate_dir"
[[ ! -e "$aggregate_staging_dir" ]] || \
    fail "condition aggregate staging path already exists; expected absent=$aggregate_staging_dir"

printf 'Independently verifying and aggregating condition=%s exactly once\n' \
    "$condition_id"
/usr/bin/time -v -o "$case_job_dir/gnu-time-aggregate.txt" \
    "$python_path" -m retro_gol.scaling aggregate-case \
        --master-plan "$plan_dir/plan.json" \
        --shard-root "$shard_root" \
        --output-dir "$aggregate_dir" \
        --shard-count "$expected_slurm_ntasks"

[[ -f "$aggregate_dir/COMPLETE" ]] || \
    fail "condition aggregator returned without COMPLETE; expected=$aggregate_dir/COMPLETE"

completion_tmp="$case_job_dir/.COMPLETION.txt.tmp"
{
    printf 'finished_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'run_id=%s\nattempt_id=%s\ncondition_id=%s\n' \
        "$run_id" "$attempt_id" "$condition_id"
    printf 'slurm_job_id=%s\nshard_count=%s\n' \
        "$SLURM_JOB_ID" "$expected_slurm_ntasks"
    printf 'aggregate_dir=%s\n' "$aggregate_dir"
    sha256sum -- "$aggregate_dir/COMPLETE" "$aggregate_dir/manifest.json"
    du -sk -- "$case_root"
} > "$completion_tmp"
mv -- "$completion_tmp" "$case_job_dir/COMPLETION.txt"
trap - ERR

printf 'Completed condition=%s W=%s aggregate=%s\n' \
    "$condition_id" "$expected_slurm_ntasks" "$aggregate_dir"
printf '%s\n' \
    "After the job exits: sacct -j $SLURM_JOB_ID --units=K --format=JobIDRaw,JobName%28,Account,Partition,QOS,NodeList,AllocCPUS,NTasks,ElapsedRaw,TotalCPU,CPUTimeRAW,MaxRSS,State,ExitCode"
