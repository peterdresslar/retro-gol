#!/bin/bash

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

if [[ "$#" -ne 0 ]]; then
    fail "this versioned scaling launcher accepts no arguments; observed_count=$#. Usage: bash calibrations/submit_sol_cpu_scaling_v1.sh"
fi
inherited_sbatch_variables=$(compgen -A variable SBATCH_ || true)
[[ -z "$inherited_sbatch_variables" ]] || \
    fail "inherited SBATCH_* variables are forbidden; variables=$inherited_sbatch_variables"

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
python_path="$repo_root/.venv/bin/python"
hf_path="$repo_root/.venv/bin/hf"
config_path="$script_dir/sol_cpu_scaling_v1.json"
parent_script="$script_dir/submit_sol_cpu_scaling_v1.sh"
worker_script="$script_dir/sol_cpu_scaling_v1.slurm"
finalizer_script="$script_dir/sol_cpu_scaling_finalize_v1.slurm"

run_id=sol-cpu-scaling-v1
plan_id=sol-cpu-scaling-v1-plan-003
attempt_id=run-attempt-001
scratch_root=/scratch/pdressla/retro-gol/calibrations
remote_uri="hf://buckets/peterdresslar/retro-gol-private/calibrations/$run_id/backup-attempt-001/export"
smoke_run_root="$scratch_root/runs/sol-private-backup-smoke-v1"

slurm_account=grp_bdaniel6
slurm_partition=htc
slurm_qos=public
slurm_nodes=1
slurm_cpus_per_task=1
slurm_mem_per_cpu_mib=1024
compute_wall_time=00:30:00
finalizer_ntasks=1
finalizer_memory_mib=2048
finalizer_wall_time=02:00:00
slurm_open_mode=truncate
slurm_export_mode=NONE
thread_count=1
worker_counts=(1 2 4 8)

for command_name in cp git mv sbatch sha256sum; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required command is unavailable; command=$command_name"
done
[[ -x "$python_path" ]] || \
    fail "embedded Python interpreter is not executable; expected=$python_path"
[[ -x "$hf_path" ]] || \
    fail "pinned HF executable is not available; expected=$hf_path"
for required_file in \
    "$config_path" "$parent_script" "$worker_script" "$finalizer_script"; do
    [[ -f "$required_file" ]] || \
        fail "required tracked scaling file is missing; expected=$required_file"
done
for path in "$repo_root" "$python_path" "$scratch_root"; do
    case "$path" in
        *[[:space:]]*) fail "scaling path must not contain whitespace; observed=$path" ;;
    esac
done

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || \
    fail "scaling submission requires a clean Git checkout; repo=$repo_root dirty_paths=$dirty_paths"
revision=$(git rev-parse --verify HEAD)
checksum_record=$(sha256sum -- "$config_path")
config_sha256=${checksum_record%% *}

"$python_path" - "$config_path" "$run_id" <<'PYTHON'
import json
import sys

config_path, expected_run_id = sys.argv[1:]
with open(config_path, encoding="utf-8") as config_file:
    config = json.load(config_file)
expected = {
    "purpose": "sol_cpu_scaling_calibration",
    "run_id": expected_run_id,
    "implementation": "numpy_reference",
    "topology": "square_torus",
    "rule": "B3/S23",
    "board_sizes": [10, 32],
    "densities": ["0.20", "0.325"],
    "trajectories_per_stratum": 1000,
    "max_probe_generations": 10000,
    "seed_start": 202607230000,
    "backup_mode": "required_private_hf",
}
for field, expected_value in expected.items():
    observed = config.get(field)
    if observed != expected_value:
        raise SystemExit(
            "ERROR: scaling configuration differs from RG-CAL-002; "
            f"field={field} expected={expected_value!r} observed={observed!r}"
        )
PYTHON

smoke_marker="$smoke_run_root/BACKUP_COMPLETE"
smoke_outcome="$smoke_run_root/backup/outcome.json"
[[ -f "$smoke_marker" && -f "$smoke_outcome" ]] || \
    fail "RG-CAL-003 completion evidence is missing; expected_marker=$smoke_marker expected_outcome=$smoke_outcome"
"$python_path" - "$smoke_marker" "$smoke_outcome" <<'PYTHON'
import hashlib
import json
import sys

marker_path, outcome_path = sys.argv[1:]
marker = {}
for line in open(marker_path, encoding="utf-8"):
    key, separator, value = line.rstrip("\n").partition("=")
    if not separator or not key or key in marker:
        raise SystemExit(
            f"ERROR: invalid RG-CAL-003 completion marker line={line!r}"
        )
    marker[key] = value
outcome = json.load(open(outcome_path, encoding="utf-8"))
observed_outcome_sha256 = hashlib.sha256(
    open(outcome_path, "rb").read()
).hexdigest()
expected_remote = (
    "hf://buckets/peterdresslar/retro-gol-private/calibrations/"
    "sol-private-backup-smoke-v1/backup-attempt-001/export"
)
if marker.get("outcome_sha256") != observed_outcome_sha256:
    raise SystemExit(
        "ERROR: RG-CAL-003 outcome checksum differs from BACKUP_COMPLETE; "
        f"expected={marker.get('outcome_sha256')!r} "
        f"observed={observed_outcome_sha256}"
    )
if outcome.get("status") != "complete" or outcome.get("remote_uri") != expected_remote:
    raise SystemExit(
        "ERROR: RG-CAL-003 outcome is not the accepted private-backup smoke; "
        f"observed={outcome!r}"
    )
PYTHON

plan_dir="$scratch_root/plans/$plan_id"
plan_staging_dir="$scratch_root/plans/.$plan_id.staging"
run_root="$scratch_root/runs/$run_id/$attempt_id"
log_root="$scratch_root/logs/$run_id/$attempt_id"
for required_plan_file in plan.json manifest.json PLAN_COMPLETE; do
    [[ -f "$plan_dir/$required_plan_file" ]] || \
        fail "current scaling plan is missing; expected=$plan_dir/$required_plan_file. Run: bash calibrations/plan_sol_cpu_scaling_v1.sh"
done
[[ ! -e "$plan_staging_dir" ]] || \
    fail "scaling plan staging path exists and requires inspection; path=$plan_staging_dir"
for forbidden_path in "$run_root" "$log_root"; do
    [[ ! -e "$forbidden_path" ]] || \
        fail "scaling attempt path already exists and requires explicit inspection; path=$forbidden_path"
done

checksum_record=$(sha256sum -- "$plan_dir/plan.json")
plan_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$plan_dir/manifest.json")
plan_manifest_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$plan_dir/PLAN_COMPLETE")
plan_marker_sha256=${checksum_record%% *}

"$python_path" - \
    "$config_path" "$plan_dir/plan.json" "$plan_dir/manifest.json" \
    "$plan_dir/PLAN_COMPLETE" "$repo_root/uv.lock" "$run_id" \
    "$revision" "$config_sha256" "$plan_sha256" \
    "$plan_manifest_sha256" <<'PYTHON'
import hashlib
import importlib.metadata
import json
import sys

(
    config_path,
    plan_path,
    manifest_path,
    marker_path,
    lock_path,
    expected_run_id,
    expected_revision,
    expected_config_sha256,
    expected_plan_sha256,
    expected_manifest_sha256,
) = sys.argv[1:]
config = json.load(open(config_path, encoding="utf-8"))
plan = json.load(open(plan_path, encoding="utf-8"))
manifest = json.load(open(manifest_path, encoding="utf-8"))
marker = json.load(open(marker_path, encoding="utf-8"))
checks = {
    "plan run_id": (expected_run_id, plan.get("run_id")),
    "plan resolved_config": (config, plan.get("resolved_config")),
    "plan source_config_sha256": (
        expected_config_sha256,
        plan.get("source_config_sha256"),
    ),
    "plan unit_count": (4000, plan.get("unit_count")),
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
expected_python = manifest["environment"]["python"]
expected_numpy = manifest["environment"]["numpy"]
expected_lock_sha256 = manifest["environment"]["source_sha256"]["uv.lock"]
observed_lock_sha256 = hashlib.sha256(open(lock_path, "rb").read()).hexdigest()
if sys.version != expected_python:
    raise SystemExit(
        "ERROR: Python identity changed after planning; "
        f"expected={expected_python!r} observed={sys.version!r}"
    )
observed_numpy = importlib.metadata.version("numpy")
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

mkdir -p -- "$run_root/job" "$run_root/backup" "$run_root/multiprog" "$log_root"
job_dir="$run_root/job"
cp -a -- "$smoke_marker" "$job_dir/rg-cal-003-BACKUP_COMPLETE"
cp -a -- "$smoke_outcome" "$job_dir/rg-cal-003-outcome.json"

"$hf_path" version --format json > "$job_dir/hf-version-preflight.json"
"$hf_path" auth whoami --format json > "$job_dir/hf-whoami-preflight.json"
"$hf_path" buckets info peterdresslar/retro-gol-private --format json \
    > "$job_dir/hf-bucket-preflight.json"
"$hf_path" buckets list "$remote_uri" --recursive --format json \
    > "$job_dir/hf-remote-prefix-preflight.json"
"$python_path" - \
    "$job_dir/hf-version-preflight.json" \
    "$job_dir/hf-whoami-preflight.json" \
    "$job_dir/hf-bucket-preflight.json" \
    "$job_dir/hf-remote-prefix-preflight.json" <<'PYTHON'
import json
import sys

version_path, whoami_path, bucket_path, listing_path = sys.argv[1:]
version = json.load(open(version_path, encoding="utf-8"))
whoami = json.load(open(whoami_path, encoding="utf-8"))
bucket = json.load(open(bucket_path, encoding="utf-8"))
if version != {"version": "1.24.0"}:
    raise SystemExit(f"ERROR: pinned HF version check failed; observed={version!r}")
if whoami.get("user") != "peterdresslar":
    raise SystemExit(f"ERROR: HF identity check failed; observed={whoami!r}")
if bucket.get("id") != "peterdresslar/retro-gol-private" or bucket.get("private") is not True:
    raise SystemExit(f"ERROR: private HF bucket check failed; observed={bucket!r}")
listing_text = open(listing_path, encoding="utf-8").read().strip()
if listing_text and json.loads(listing_text) != []:
    raise SystemExit(
        "ERROR: remote scaling prefix is not empty; "
        f"observed_listing={listing_text}"
    )
PYTHON

for worker_count in "${worker_counts[@]}"; do
    printf -v condition_id 'w%02d' "$worker_count"
    import_multiprog_path="$run_root/multiprog/import-$condition_id.conf"
    generate_multiprog_path="$run_root/multiprog/generate-$condition_id.conf"
    import_tmp="$run_root/multiprog/.import-$condition_id.conf.tmp"
    generate_tmp="$run_root/multiprog/.generate-$condition_id.conf.tmp"
    for ((shard_index = 0; shard_index < worker_count; shard_index++)); do
        printf -v shard_id 'shard-%03d' "$shard_index"
        shard_root="$run_root/cases/$condition_id/shards/$shard_id"
        printf "%d /usr/bin/time -v -o %s/gnu-time-import.txt %s -c '__import__(\"numpy\")'\n" \
            "$shard_index" "$shard_root" "$python_path" >> "$import_tmp"
        printf '%d /usr/bin/time -v -o %s/gnu-time-generation.txt %s -m retro_gol --mode run --config %s --input-plan %s/plan.json --output-dir %s/result --shard-index %d --shard-count %d\n' \
            "$shard_index" "$shard_root" "$python_path" "$config_path" \
            "$plan_dir" "$shard_root" "$shard_index" "$worker_count" \
            >> "$generate_tmp"
    done
    mv -- "$import_tmp" "$import_multiprog_path"
    mv -- "$generate_tmp" "$generate_multiprog_path"
done

compute_job_numbers=()
previous_job_number=
for worker_count in "${worker_counts[@]}"; do
    printf -v condition_id 'w%02d' "$worker_count"
    slurm_job_name="retro-gol-scale-$condition_id"
    import_multiprog_path="$run_root/multiprog/import-$condition_id.conf"
    generate_multiprog_path="$run_root/multiprog/generate-$condition_id.conf"
    checksum_record=$(sha256sum -- "$import_multiprog_path")
    import_multiprog_sha256=${checksum_record%% *}
    checksum_record=$(sha256sum -- "$generate_multiprog_path")
    generate_multiprog_sha256=${checksum_record%% *}
    dependency_options=()
    dependency_record=none
    if [[ -n "$previous_job_number" ]]; then
        dependency_options=(--dependency="afterany:$previous_job_number")
        dependency_record="afterany:$previous_job_number"
    fi

    if ! job_id=$(sbatch --parsable \
        "${dependency_options[@]}" \
        --job-name="$slurm_job_name" \
        --account="$slurm_account" \
        --partition="$slurm_partition" \
        --qos="$slurm_qos" \
        --nodes="$slurm_nodes" \
        --ntasks="$worker_count" \
        --cpus-per-task="$slurm_cpus_per_task" \
        --mem-per-cpu="${slurm_mem_per_cpu_mib}M" \
        --time="$compute_wall_time" \
        --open-mode="$slurm_open_mode" \
        --export="$slurm_export_mode" \
        --no-requeue \
        --chdir="$repo_root" \
        --output="$log_root/%x-%j.out" \
        --error="$log_root/%x-%j.err" \
        "$worker_script" \
        "$repo_root" "$python_path" "$scratch_root" "$config_path" \
        "$parent_script" "$worker_script" "$run_id" "$plan_id" \
        "$attempt_id" "$thread_count" "$slurm_job_name" "$slurm_account" \
        "$slurm_partition" "$slurm_qos" "$slurm_nodes" "$worker_count" \
        "$slurm_cpus_per_task" "$slurm_mem_per_cpu_mib" \
        "$compute_wall_time" "$revision" "$config_sha256" "$plan_sha256" \
        "$plan_manifest_sha256" "$plan_marker_sha256" \
        "$import_multiprog_path" "$import_multiprog_sha256" \
        "$generate_multiprog_path" "$generate_multiprog_sha256"); then
        fail "scaling sbatch submission failed; condition=$condition_id previous_job_id=${previous_job_number:-none} run_root=$run_root"
    fi
    job_number=${job_id%%;*}
    [[ "$job_number" =~ ^[0-9]+$ ]] || \
        fail "scaling sbatch returned an invalid job ID; condition=$condition_id observed=$job_id"
    submission_tmp="$job_dir/.submission-$condition_id.txt.tmp"
    {
        printf 'condition_id=%s\nworker_count=%s\njob_id=%s\n' \
            "$condition_id" "$worker_count" "$job_number"
        printf 'dependency=%s\njob_name=%s\nmemory_per_cpu_mib=%s\nwall_time=%s\n' \
            "$dependency_record" "$slurm_job_name" "$slurm_mem_per_cpu_mib" \
            "$compute_wall_time"
        printf 'import_multiprog_sha256=%s\ngenerate_multiprog_sha256=%s\n' \
            "$import_multiprog_sha256" "$generate_multiprog_sha256"
    } > "$submission_tmp"
    mv -- "$submission_tmp" "$job_dir/submission-$condition_id.txt"
    compute_job_numbers+=("$job_number")
    previous_job_number=$job_number
done

finalizer_job_name=retro-gol-scale-final
if ! finalizer_job_id=$(sbatch --parsable \
    --dependency="afterany:$previous_job_number" \
    --job-name="$finalizer_job_name" \
    --account="$slurm_account" \
    --partition="$slurm_partition" \
    --qos="$slurm_qos" \
    --nodes="$slurm_nodes" \
    --ntasks="$finalizer_ntasks" \
    --cpus-per-task="$slurm_cpus_per_task" \
    --mem="${finalizer_memory_mib}M" \
    --time="$finalizer_wall_time" \
    --open-mode="$slurm_open_mode" \
    --export="$slurm_export_mode" \
    --no-requeue \
    --chdir="$repo_root" \
    --output="$log_root/%x-%j.out" \
    --error="$log_root/%x-%j.err" \
    "$finalizer_script" \
    "$repo_root" "$python_path" "$hf_path" "$scratch_root" "$config_path" \
    "$parent_script" "$worker_script" "$finalizer_script" "$run_id" \
    "$plan_id" "$attempt_id" "$remote_uri" "$thread_count" \
    "$finalizer_job_name" "$slurm_account" "$slurm_partition" "$slurm_qos" \
    "$slurm_nodes" "$finalizer_ntasks" "$slurm_cpus_per_task" \
    "$finalizer_memory_mib" "$finalizer_wall_time" "$revision" \
    "$config_sha256" "$plan_sha256" "$plan_manifest_sha256" \
    "$plan_marker_sha256" "${compute_job_numbers[0]}" \
    "${compute_job_numbers[1]}" "${compute_job_numbers[2]}" \
    "${compute_job_numbers[3]}"); then
    fail "finalizer sbatch submission failed after compute submissions; compute_job_ids=${compute_job_numbers[*]} run_root=$run_root"
fi
finalizer_job_number=${finalizer_job_id%%;*}
[[ "$finalizer_job_number" =~ ^[0-9]+$ ]] || \
    fail "finalizer sbatch returned an invalid job ID; observed=$finalizer_job_id"

finalizer_submission_tmp="$job_dir/.submission-finalizer.txt.tmp"
{
    printf 'job_id=%s\ndependency=afterany:%s\njob_name=%s\n' \
        "$finalizer_job_number" "$previous_job_number" "$finalizer_job_name"
    printf 'memory_mib=%s\nwall_time=%s\nremote_uri=%s\n' \
        "$finalizer_memory_mib" "$finalizer_wall_time" "$remote_uri"
} > "$finalizer_submission_tmp"
mv -- "$finalizer_submission_tmp" "$job_dir/submission-finalizer.txt"

all_job_ids="${compute_job_numbers[0]},${compute_job_numbers[1]},${compute_job_numbers[2]},${compute_job_numbers[3]},$finalizer_job_number"
printf 'Submitted RG-CAL-002 compute_job_ids=%s finalizer_job_id=%s\n' \
    "${compute_job_numbers[*]}" "$finalizer_job_number"
printf 'Monitor: squeue -j %s\n' "$all_job_ids"
printf 'Completion marker: %s/BACKUP_COMPLETE\n' "$run_root"
