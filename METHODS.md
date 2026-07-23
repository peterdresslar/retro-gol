# Methods and Reproduction Decision Record

Last updated: 2026-07-22

This is the living decision record required by `AGENTS.md`. It is normative for
implementation and execution. A later decision must supersede an accepted
decision explicitly; it must not silently rewrite the history on which an
existing run depends.

Decision statuses:

- **Accepted:** settled for the current experiment.
- **Provisional:** the current implementation default, subject to a named pilot
  or explicit review before production.
- **Pending:** intentionally unresolved and not yet safe to assume.
- **Superseded:** retained for provenance but replaced by another decision ID.

## RG-SIM-001 — Domain, rule, and primary strata

- **Status:** Accepted
- **Decision:** Simulate synchronous Conway Game of Life (B3/S23) on a square
  \(N \times N\) torus. Define \(M=N^2\). The initial production candidates are
  \(N \in \{10,32\}\), hence \(M \in \{100,1024\}\), crossed with target
  initial densities \(p \in \{0.20,0.325\}\).
- **Evidence:** These dimensions, densities, toroidal boundary conditions, and
  traditional rules were explicitly selected in the project discussion.
- **Alternatives considered:** \(M=1000\), additional board sizes, and
  nonperiodic boundaries. \(M=1000\) is incompatible with a square integer-
  sided board when \(M=N^2\). Additional sizes remain possible as later strata.
- **Likely sensitivity:** High. Board size and boundary conditions change the
  trajectory and predecessor distributions.
- **Affected configurations:** Simulator, corpus generator, recurrence
  detector, split manifests, training inputs, and evaluation.

## RG-INIT-001 — Fixed-population random initial states

- **Status:** Accepted
- **Decision:** Initial states are sampled uniformly without replacement from
  the \(M\) cell locations conditional on an integer live-cell count \(K\).
  They are not independent Bernoulli boards. Every trajectory records the
  requested \(p\), realized \(K\), realized density \(K/M\), trajectory index,
  RNG algorithm, and seed.
- **Evidence:** Fixed \(pM\) live-cell populations were explicitly preferred.
- **Alternatives considered:** Independent Bernoulli cells with probability
  \(p\), or one rounded value of \(K\) for every board in a stratum.
- **Likely sensitivity:** Moderate to high, especially for \(N=10\). Fixed-
  population sampling removes population-count variance and induces weak
  negative dependence between cell occupancies; this must be stated when the
  corpus is described.
- **Affected configurations:** Initial-state sampler, trajectory manifests,
  density comparisons, and reproduction fixtures.

## RG-INIT-002 — Integer realization of fractional \(pM\)

- **Status:** Provisional; review after the first manifest dry run
- **Decision:** When \(pM\) is not an integer, balance floor and ceiling counts
  deterministically across the ordered trajectories in a stratum. Let
  \(a=pM\), let \(i\geq0\) be the zero-based trajectory index within that
  stratum, and define

  \[
  K_i = \left\lfloor(i+1)a+\tfrac12\right\rfloor
        - \left\lfloor ia+\tfrac12\right\rfloor.
  \]

  Thus every board has an exact integer population, while the cumulative live-
  cell count after any prefix of trajectories differs from its target by at
  most half a cell. The four strata realize:

  - \(N=10,p=0.20\): \(K=20\);
  - \(N=10,p=0.325\): balanced \(K=32\) and \(K=33\);
  - \(N=32,p=0.20\): balanced \(K=204\) and \(K=205\) in a 1:4 ratio;
  - \(N=32,p=0.325\): balanced \(K=332\) and \(K=333\) in a 1:4 ratio.

- **Evidence:** \(pM\) is fractional in three of the four selected strata, so
  no individual binary board can have exactly the requested density there.
- **Alternatives considered:** Always floor, always ceil, nearest integer, or
  Bernoulli population counts. These create a persistent density offset or
  change the selected ensemble.
- **Likely sensitivity:** Low for \(N=32\), potentially measurable for \(N=10\).
- **Affected configurations:** Sweep-plan materialization, seed assignment,
  stratum summaries, and matched-density analyses.

## RG-STOP-001 — Exact recurrence and completion

- **Status:** Accepted
- **Decision:** A trajectory is complete at the first of:

  1. extinction;
  2. an exact fixed point;
  3. the first exact recurrence of the full coordinate-fixed board.

  If \(x_t=x_\mu\) for the first detected repeat, record transient length
  \(\mu\) and period \(\lambda=t-\mu\). A hash may index prior states, but any
  match must be verified by equality of the complete bit-packed board. The
  cycle-closing transition is a valid retained transition.

  Recurrence is not tested modulo translation, rotation, or reflection. A
  spaceship therefore receives time to wrap around the torus and interact or
  break. It counts as complete only if the complete board later repeats in the
  same coordinates; at that point determinism proves that its subsequent
  evolution will repeat as well. No component-level still-life or oscillator
  classifier is required for stopping.
- **Evidence:** The selected interpretation was to give translating structures
  the opportunity to wrap and change, while still accepting exact toroidal
  recurrence as completion.
- **Alternatives considered:** Component recognition, bounded-period activity
  tests, or recurrence modulo symmetry. These can terminate a board before a
  later toroidal interaction.
- **Likely sensitivity:** High for run length, storage, and the late-trajectory
  distribution.
- **Affected configurations:** Simulator, recurrence index, terminal metadata,
  transition extraction, and attractor analysis.

## RG-TIME-001 — Wall-time-bounded generation

- **Status:** Accepted
- **Decision:** The initial corpus is resource-bounded, not count-bounded and
  not generation-cap-bounded. A trajectory advances until RG-STOP-001 applies
  or its configured monotonic wall-time deadline is reached. No initial
  \(T_{\max}(N)\) is imposed. A wall-time stop is right-censoring and must be
  recorded as `wall_time`, never as recurrence completion.

  The first engineering milestone is a wall-time tester. It will determine how
  scheduler wall time is divided among workers and trajectories, the finalizer
  reserve, checkpoint cadence, shard packing, and the CPU/GPU execution mix.
  The later production objective is approximately one week of responsibly
  fair-share-allocated Sol work; exact submitted and consumed CPU-hours,
  GPU-hours, and calendar interval must be frozen in that campaign's manifest.
  There is no target such as one million trajectories.

  Because elapsed-time censoring depends on hardware and load, reproducing a
  released corpus means replaying its frozen trajectory IDs, seeds, and
  recorded generation endpoints. Running for the same number of hours is a
  resource-budget replication, not an identity reproduction.
- **Evidence:** Wall time was explicitly selected as the controller of the
  number of generations and corpus scale.
- **Alternatives considered:** A size-dependent generation cap or a fixed
  trajectory count. Both are rejected for the initial corpus.
- **Likely sensitivity:** High. Equal wall time can produce unequal trajectory
  and transition counts across size, density, implementation, and hardware.
- **Affected configurations:** Timing probe, worker deadline, Slurm request,
  terminal status, campaign manifest, and reproduction protocol.

## RG-ATOM-001 — Generation atomicity, interruption, and restart

- **Status:** Provisional; must pass forced-termination tests before a bounded
  pilot
- **Decision:** Compute \(x_{t+1}=F(x_t)\) into a separate buffer while \(x_t\)
  remains immutable. Publish the new state and transition only after the whole
  board has been computed and validated. A deadline, safeword, or scheduler
  warning observed before a generation prevents that generation from starting;
  one observed during a generation takes effect immediately after the atomic
  commit.

  Durable output uses checksummed append/chunk records in a staging path. On
  recovery, an incomplete trailing record or chunk is discarded and execution
  resumes or deterministically replays from the last validated state. A hard
  kill may lose unfinalized work, but it must never create a half-updated board
  or a transition whose target was only partly computed. Checkpoint frequency
  and the advance warning reserve are outputs of the wall-time tester, with the
  maximum possible recomputation recorded.

  Fully committed pairs in a wall-time-censored prefix remain causally valid.
  The trajectory remains marked incomplete and cannot supply an attractor ID or
  completion-time measurement.
- **Evidence:** Scheduler termination can occur during an update, and the
  project requires each generation to be atomic and long work to be resumable.
- **Alternatives considered:** In-place board mutation, per-cell persistence,
  or discarding every interrupted trajectory. These risk invalid states,
  excessive I/O, or informative completion bias and wasted compute.
- **Likely sensitivity:** Low scientifically if status is honored; potentially
  high operationally because checkpoint and sync I/O can dominate small boards.
- **Affected configurations:** Simulator buffers, state format, checkpoint
  cadence, signal handler, finalizer, and recovery tests.

## RG-DATA-001 — Raw trajectory records and training views

- **Status:** Provisional; sampler quotas and final state format depend on the
  wall-time/storage pilot
- **Decision:** Keep raw generation separate from training materialization.
  Every trajectory record must preserve at least:

  - trajectory and stratum IDs, seed and RNG identity;
  - requested and resolved configuration;
  - initial bit-packed state and realized population;
  - every retained complete state or enough checksummed state chunks to recover
    each retained adjacent transition without rerunning an unknown code version;
  - generation indices, population, and activity summaries;
  - terminal status, last valid generation, and, when known, \(\mu\) and
    \(\lambda\);
  - code revision, environment, hardware, timings, output paths, and checksums.

  A completed recurrence stores every unique state once plus a reference for
  the cycle-closing target; it never stores repeated laps around the attractor.
  The pilot initially retains full bit-packed state sequences so storage and
  replay tradeoffs can be measured directly.

  Training examples are produced by a named, configuration-hashed materializer,
  not by treating all raw transitions as the primary dataset. The primary view
  will cap each trajectory's contribution and sample deterministically across
  transient time, the first detected cycle when present, and censored prefixes.
  If a segment contains fewer eligible transitions than its quota, retain all
  of them. Record inclusion probabilities and the fraction of examples from
  censored trajectories. Freeze numeric quotas after the bounded pilot.

  Also materialize an all-eligible-transitions sensitivity view when raw
  storage permits. This exposes the effect of temporal autocorrelation and the
  overweighting of long trajectories rather than hiding it.
- **Evidence:** A versioned stratified view was accepted as preferable to
  allowing long runs and common late states to dominate training implicitly.
- **Alternatives considered:** Every transition as the sole dataset, one final
  transition per trajectory, or seeds without recoverable training states.
- **Likely sensitivity:** High. The materializer defines the empirical target
  distribution learned by the model.
- **Affected configurations:** Raw shard schema, state codec, dataset builder,
  training manifests, and sensitivity analyses.

## RG-VIEW-001 — Read-only trajectory and retrodiction viewer

- **Status:** Provisional; review after the first learned retrodiction artifact
- **Decision:** Provide a terminal viewer as a read-only diagnostic over one
  selected, checksum-valid trajectory artifact. The trajectory must remain in
  its completed run so the viewer can resolve `N`, terminal metadata, the
  planned unit, and the artifact checksum from `plan.json`, `manifest.json`,
  and `COMPLETE`. Startup validates only the selected artifact rather than
  rescanning every trajectory in the run.

  Forward and backward controls navigate the recorded trajectory; they do not
  solve an inverse problem, repeat a detected cycle, or simulate beyond the
  stored endpoint. Display generation \(g\) uses the state-table indices

  \[
  [0] + \mathtt{transition\_target\_index},
  \]

  so the final cycle-closing generation is visible even though recurrent runs
  store each unique state only once.

  Optional retrodictions use a separate NumPy `.npz` artifact with exactly
  these arrays:

  - `schema_version`: `uint32` scalar equal to 1;
  - `source_trajectory_sha256`: Unicode scalar matching the selected raw
    trajectory;
  - `transition_index`: nonempty, strictly increasing `uint32` vector;
  - `p_live`: finite `float32` array of shape `(R,N,N)` in `[0,1]`.

  For row \(r\), with \(k=\mathtt{transition\_index}[r]\), define

  \[
  \mathtt{p\_live}[r,i,j]
    = q_\theta\!\left(x_k[i,j]=1\mid x_{k+1}\right).
  \]

  Here \(x_{k+1}\) means the chronological target recorded by transition
  \(k\), including a cycle-closing target that refers to an earlier state-table
  row. The viewer labels this quantity `P(live)`: it is neither confidence in
  a complete predecessor nor proof of forward validity. It never thresholds,
  samples, or evolves a probability map. Red denotes `P(live) < 1/3`, yellow
  denotes `1/3 <= P(live) < 2/3`, and green denotes `P(live) >= 2/3`.

  When retrodictions are present, the actual source state may be overlaid as a
  glyph without changing the probability color. Sparse `transition_index`
  vectors are permitted. Retrodiction playback steps only through those
  recorded indices; switching from the actual layer selects the nearest
  available source generation, with an exact tie resolved toward the earlier
  index, and displays the selected index explicitly. `--retro-only` requires an
  explicit retrodiction artifact, disables that truth overlay, and locks the
  viewer to the retrodiction layer. A terminal that cannot display the complete
  \(N\)-by-\(N\) board at two columns per cell, the controls, or the required
  colors is an explicit startup failure.
- **Evidence:** The console viewer was requested for bidirectional trajectory
  inspection, speed-controlled playback, per-cell retrodiction colors, and an
  optional actual-history overlay. Current raw `.npz` files intentionally keep
  scientific metadata in the run manifest, and recurrent trajectories omit a
  duplicate closing state.
- **Alternatives considered:** Inferring `N` from a filename or packed byte
  width; running whole-run verification before viewing one trajectory;
  treating marginal probabilities as a binary predecessor; or evolving them
  through B3/S23. These would weaken provenance, make startup scale with corpus
  size, or misrepresent the learned distribution.
- **Likely sensitivity:** None to stored data because the viewer is read-only;
  high to interpretation if probability alignment or overlays are mislabeled.
- **Affected configurations:** `retroviewer`, retrodiction export artifacts,
  diagnostic documentation, and viewer tests.

## RG-SPLIT-001 — Leakage-resistant evaluation regimes

- **Status:** Accepted; split proportions remain Pending
- **Decision:** Assign the primary train/validation/test split by a stable hash
  of the complete trajectory identity before transition sampling, stratified by
  \(N\), requested \(p\), and realized \(K\). Adjacent states from one trajectory
  may never cross the primary split.

  Derive two stricter evaluations without weakening that rule:

  - **Exact-target-disjoint:** retain held-out pairs only where the coordinate-
    fixed target board does not appear among training targets.
  - **Attractor-disjoint:** retain completed held-out trajectories only where
    the exact canonical cycle ID is absent from training. Canonicalization is
    across cycle phase, not translation, rotation, or reflection.

  Report how much data each filter removes and its size, density, horizon, and
  terminal-status composition. A later symmetry-disjoint regime may be added
  under a new decision ID.
- **Evidence:** Both trajectory-disjoint and harder target/attractor-disjoint
  evaluation were selected.
- **Alternatives considered:** Random frame splits or silently canonicalizing
  spatial symmetries. The former leaks trajectories; the latter changes the
  meaning of exact equality.
- **Likely sensitivity:** High for apparent generalization, especially near
  common still lifes and short-period oscillators.
- **Affected configurations:** Split assignment, dataset materialization,
  attractor fingerprinting, and evaluation reports.

## RG-DENS-001 — Density as a crossed experimental sweep

- **Status:** Accepted
- **Decision:** Generate \(p=0.20\) and \(p=0.325\) as crossed strata at both
  board sizes. Within-density training and evaluation are the first priority.
  Cross-density transfer is a later, separately configured analysis. Record
  compute and yield per stratum, and compare density effects using both
  compute-matched and data-size-matched materializations so that a density
  result is not merely a data-volume result.
- **Evidence:** The two densities were selected as an experimental sweep, with
  density-specific learning effects identified as interesting but lower
  priority than corpus generation.
- **Alternatives considered:** Pooling densities without labels or making one
  density exclusively held out from the start.
- **Likely sensitivity:** High for any claimed density effect; low for the
  validity of individual forward transitions.
- **Affected configurations:** Sweep plan, quotas, dataset views, model
  experiments, and reports.

## RG-ORACLE-001 — Exhaustive \(N=5\) predecessor tier

- **Status:** Accepted
- **Decision:** Treat \(N=5\) as the first scientifically meaningful exhaustive
  tier. Enumerate all \(2^{25}=33,554,432\) source boards, apply the same
  toroidal B3/S23 implementation, and construct checksummed successor and
  predecessor-count/index artifacts. This exhaustive product is not conditioned
  on the two random-soup densities; live-count strata can be derived from the
  complete state space. Keep smaller boards as implementation fixtures.

  Walking upward in \(N\) is a future workstream. Naive \(N=6\) enumeration has
  \(2^{36}\) states, 2048 times as many as \(N=5\), so it requires a new scale
  decision or solver-based representation rather than automatic extension.
- **Evidence:** \(N=5\) was selected as the smallest board of scientific
  interest, and complete enumeration was identified as valuable in its own
  right.
- **Alternatives considered:** Beginning the scientific oracle at \(N=4\), or
  assuming exhaustive enumeration extends routinely to larger \(N\).
- **Likely sensitivity:** High for exact predecessor multiplicities; separate
  from the validity oracle, which remains exact at every size.
- **Affected configurations:** Exhaustive enumerator, predecessor index,
  oracle fixtures, evaluation, and artifact budget.

## RG-PROBE-001 — First bounded data-generation probe

- **Status:** Provisional; review after the first local run and before the Sol
  wall-time tester
- **Decision:** The first local data-generation probe crosses all four primary
  \((N,p)\) strata and runs ten trajectories in each stratum. Each trajectory
  advances until RG-STOP-001 applies or 100 complete transitions have been
  committed. Reaching 100 is recorded as `probe_generation_limit`, a censored
  engineering-test status, and never as scientific completion. This test-only
  limit does not supersede the wall-time production rule in RG-TIME-001.

  Ten trajectories are the smallest shared block that exercises complete
  periods of the provisional integer-population schedules in RG-INIT-002: the
  half-cell schedule has period two and the four-to-one floor/ceiling schedule
  has period five.

  The probe uses NumPy `bool` arrays of shape \((N,N)\) as its readable CPU
  representation and `numpy.random.PCG64` with every resolved trajectory seed
  materialized in `plan.json`. Coordinates are `(row, column)`. Persistent and
  recurrence states are flattened in C order and packed with little bit order;
  unused trailing bits must be zero. A scalar reference update and the
  vectorized NumPy update must agree bit-for-bit before probe output is trusted.
  These probe artifacts measure the candidate representation and do not freeze
  the production chunk format or production recurrence index.
- **Evidence:** A 100-generation first pass across all selected options was
  proposed. Ten trajectories per stratum remain inexpensive while testing the
  complete fixed-population balancing cadence.
- **Alternatives considered:** One trajectory per stratum, which does not test
  RG-INIT-002; forcing all trajectories through exactly 100 transitions, which
  would retain repeated attractor laps; or beginning with a GPU implementation
  before establishing the reference result.
- **Likely sensitivity:** None to the validity of an individual transition;
  high to whether this small probe estimates production throughput or terminal
  distributions. It is a correctness and format test, not a production-scale
  estimate.
- **Affected configurations:** First-probe configuration, sweep plan, reference
  simulator, artifact validator, and local test report.

## RG-CAL-001 — First Sol CPU fixed-workload calibration

- **Status:** Provisional; review after the first Sol result and before the
  deadline-aware wall-time tester
- **Decision:** Run one serial NumPy CPU warmup across all four primary strata.
  The immutable workload contains 1,000 trajectories per stratum, uses disjoint
  PCG64 seeds, and permits at most 10,000 committed transitions per trajectory;
  exact completion under RG-STOP-001 remains active. Request one task, one CPU,
  4 GiB of memory, and 20 minutes on Sol's `htc` partition with the `public`
  QoS under account `grp_bdaniel6`. The zero-argument parent launch script pins
  the repository-local `.venv/bin/python` interpreter and scratch root
  `/scratch/pdressla/retro-gol/calibrations`; invocation-time overrides are not
  supported.

  Tracked configuration and scheduler code live in `calibrations/`. The
  plan, result, GNU `time -v` records, hardware context, and Slurm logs live in
  disjoint directories below the supplied scratch root. The wrapper requires a
  clean Git checkout, runs the focused test suite, writes the plan before
  submission, records the Git revision and configuration checksum, and refuses
  existing plan or run paths. It rejects arguments and inherited `SBATCH_*`
  settings, and supplies every resource choice in its tracked internal `sbatch`
  call. The worker has no independent resource directives and fails when the
  parent record is absent. It requires the submission-time plan, manifest,
  marker, configuration, and Git hashes; compares the queued Python, NumPy, and
  lock identity with the planning environment; installs nothing; fixes NumPy
  and BLAS thread counts to one; uses the same Python entry point as local runs;
  and disables scheduler requeue.

  This is a fixed-work calibration of a standalone interpreter/NumPy import,
  reference checks, simulation, validation, artifact writing, full-process
  elapsed time, CPU utilization, and peak resident memory. It does not yet
  isolate every initialization or first-operation warm-up cost; the separate
  import probe and the warmed full run make that limitation visible. The
  workload is deliberately longer than RG-PROBE-001, whose complete local
  pipeline took only 0.208 seconds. It does not implement RG-TIME-001 wall-time
  censoring, checkpoint/restart, scheduler warning handling, safewords, private
  backup, CPU concurrency scaling, or a GPU comparison. If Slurm terminates it
  at the requested limit, the remaining staging directory is an explicit
  incomplete software run, not a valid `wall_time` scientific status and not
  completion of an RG-SCALE-001 gate.

  `backup_mode` is explicitly `none_sol_calibration`. These disposable warmup
  artifacts must not be represented as a production corpus or as durable
  scratch-to-private backup validation. No remote transfer occurs without a
  separately approved destination and invocation.
- **Evidence:** The first local probe established correctness and format, but
  was too short for cluster throughput measurement and left 19 of 20 \(N=32\)
  trajectories active at generation 100. Sol documents `htc` as the partition
  for jobs shorter than four hours. The `myaccounts` output recorded on
  2026-07-22 identifies `grp_bdaniel6` as the default account with `public` QoS
  access. The initial CPU baseline avoids mixing array concurrency or GPU
  transfer effects into the first measurement.
- **Alternatives considered:** Timing the 40-trajectory local probe on Sol,
  which would mostly measure startup; beginning with an array, which would mix
  scheduling and filesystem contention into the serial baseline; or labeling a
  scheduler kill as valid censoring before atomic checkpoint/finalization
  exists.
- **Likely sensitivity:** Operationally moderate. The workload controls the
  precision and duration of the measurement but does not change the validity of
  any completed B3/S23 transition.
- **Affected configurations:** `calibrations/sol_cpu_timing_v1.json`, its Slurm
  job and submission wrapper, the reference configuration validator, and the
  first Sol timing report.

## RG-SCALE-001 — Calibration and scale authorization

- **Status:** Accepted
- **Decision:** Scale only through these gates:

  1. plan-only manifest and deterministic fixtures;
  2. minimal local end-to-end smoke test;
  3. minimal Sol compute-and-private-backup smoke test;
  4. wall-time and storage tester across all four primary strata and viable
     CPU/GPU implementations;
  5. forced deadline, scheduler-signal, safeword, and restart tests;
  6. bounded balanced pilot;
  7. explicit review and authorization of a week-scale campaign.

  The wall-time tester separately reports initialization/warm-up, steady-state
  board generations per second, cell updates per second, trajectories and
  transitions per resource-hour, recurrence-index cost, checkpoint/finalizer/
  backup time, peak host/device memory, bytes per trajectory and transition,
  censoring/completion distributions, and uncertainty. CPU versus GPU is
  selected from these measurements rather than assumed.

  Promotion requires checksum-valid coverage, no duplicate or missing committed
  generations after restart, a materialized one-to-one array plan, and an
  explicit requested/consumed resource budget. Equal elapsed allocation can
  yield unequal data volumes, which must be visible in the report.
- **Evidence:** The arbitrary million-run target was rejected in favor of
  learning what a reasonable amount of fair-share clock time yields.
- **Alternatives considered:** Immediate large submission or a fixed corpus
  count chosen before measuring throughput and storage.
- **Likely sensitivity:** Operationally high; scientifically protective.
- **Affected configurations:** Benchmark plan, Slurm manifests, concurrency,
  campaign authorization, and reporting.

## RG-STORE-001 — Scratch, private mirror, manifests, and recovery

- **Status:** Accepted architecture; automatic retry clause superseded by
  RG-STORE-002; remote-base selection and source-control policy superseded by
  RG-STORE-003; exact production Sol paths are Pending
- **Decision:** Adapt the Hybrid Signal Lab router-experiment pattern:

  - separate checkout, scratch data, run, log, and cache roots;
  - one stable run ID used by both the Sol scratch root and private remote
    prefix;
  - scheduler-array tasks mapped one-to-one to disjoint unit directories;
  - per-unit running/completed manifests and append-only progress records;
  - resumable units that skip only validated completion markers;
  - incremental mirroring of finalized units to a private Hugging Face Bucket;
  - restore only from a declared prerequisite manifest.

  Strengthen that donor pattern to satisfy `AGENTS.md`:

  - write into a unit `.staging` path, validate expected coverage and SHA-256
    checksums, then atomically publish the unit and its completion marker;
  - give the run root and remote prefix one coordinator protected by an
    ownership lock; workers never mutate shared aggregates;
  - preflight `hf`, authentication, and destination access before expensive
    work; a required-backup failure is not a successful no-op;
  - inspect an allowlisted `hf buckets sync --dry-run` plan before applying it,
    and never use remote deletion during routine backup; RG-STORE-002 governs
    the single-attempt failure behavior;
  - sync only finalized artifacts and logs, then verify the remote manifest and
    checksums before marking required backup complete;
  - keep credentials, caches, temporary files, and incomplete output out of the
    sync set and all manifests/logs;
  - keep the scratch mirror private and distinct from any later curated or
    public dataset. Public promotion requires explicit approval.

  The original version of this decision left the remote base outside version
  control. RG-STORE-003 supersedes that clause: the destination is a tracked
  launch input, and only credentials remain external. No production run may
  silently default to a public destination or to an unrelated project's
  prefix.
- **Evidence:** The detailed Hybrid Signal Lab scratch-to-private-HF workflow
  was selected as the operational model. Its useful boundaries are retained,
  while missing atomicity, verification, and failure semantics are added here.
- **Alternatives considered:** Scratch-only output, direct writes from all
  array tasks into one shared remote tree, or automatic public dataset upload.
- **Likely sensitivity:** Low to Game of Life dynamics, high to auditability and
  survival of an expensive corpus.
- **Affected configurations:** Environment template, run layout, Slurm array,
  manifest schema, finalizer, sync filter, and restore command.

## RG-STORE-002 — Backup failures remain explicit

- **Status:** Accepted; supersedes only the automatic bounded-retry clause in
  RG-STORE-001
- **Decision:** Each backup invocation makes one explicit transfer attempt. An
  authentication, transport, object, checksum, or verification failure is
  recorded with its original error and exits nonzero. It is not automatically
  retried or converted into a successful no-op. An operator may launch a new
  backup invocation after inspecting the failure; that invocation receives a
  new attempt record and preserves the earlier failed record.
- **Evidence:** The project requires errors to remain visible and actionable
  rather than being paved over by implicit recovery behavior.
- **Alternatives considered:** Automatic bounded backoff or best-effort backup.
  Both can obscure the first failure and make elapsed-time behavior less clear.
- **Likely sensitivity:** None to Game-of-Life dynamics; moderate to operational
  completion time and high to auditability.
- **Affected configurations:** Backup command, attempt log, finalizer, and run
  completion status.

## RG-STORE-003 — Private Hugging Face destination for future runs

- **Status:** Accepted for future versioned Sol runs; not applied
  retroactively to RG-CAL-001
- **Decision:** Use the dedicated private Hugging Face Bucket
  `hf://buckets/peterdresslar/retro-gol-private` as the remote base for future
  calibration and experiment artifacts. Each zero-argument parent launch
  script must visibly fix a run-specific destination below that base, the
  repository-local executable `.venv/bin/hf`, and the backup mode. The
  destination is tracked configuration; credentials are not. Authentication
  uses the existing Hugging Face CLI credential store prepared outside the
  repository, and preflight must require `hf auth whoami --format json` to
  identify `peterdresslar` and
  `hf buckets info peterdresslar/retro-gol-private --format json` to report a
  private accessible bucket before expensive compute begins.

  The repository directly pins `huggingface-hub==1.24.0`, which supplies the
  `hf` executable. Environments are prepared with `uv sync --frozen` before
  submission; a compute or backup job must never install or update the client.
  Future backup workers sync only a finalized, checksummed export directory.
  They must write a plan with `hf buckets sync SOURCE DEST --plan PLAN`, inspect
  that every planned operation is allowlisted beneath the exact destination,
  and apply that same plan with `hf buckets sync --apply PLAN`. Routine sync
  never uses `--delete` and makes the single attempt required by RG-STORE-002.
  Remote completion requires an independently checked remote file manifest and
  SHA-256 coverage; compute outcome and backup outcome remain separate records.

  The completed `sol-cpu-timing-v1` calibration remains explicitly
  `backup_mode=none_sol_calibration` and is not retroactively uploaded or
  relabeled. Automatic sync begins only in a new tracked script/configuration
  version and run ID. A dependent post-compute backup job is preferred when
  closed Slurm logs are part of the export; its exact dependency and artifact
  allowlist must be fixed in that future parent script.
- **Evidence:** A dedicated private bucket was created under the authenticated
  `peterdresslar` account, and later-run automatic HF sync was selected while
  the completed first calibration will instead be reviewed from files pasted
  directly into this discussion. Pinning the client in the project environment
  and tracking the destination improve reproduction over job-time installation
  or command-line destination overrides.
- **Alternatives considered:** Retroactively uploading RG-CAL-001; reusing the
  unrelated Hybrid Signal Lab bucket; installing the client with `uvx` inside
  each job; or leaving the destination as an untracked shell input. Each would
  conflict with the immutable-run record, project separation, prepared-
  environment rule, or zero-argument launch contract.
- **Likely sensitivity:** None to Game-of-Life dynamics; high to provenance,
  recoverability, and operational completion.
- **Affected configurations:** Project dependency lock, future Sol launchers,
  backup workers, run manifests, remote verification records, and restore
  instructions.

## RG-CTRL-001 — Safeword and terminal finalization

- **Status:** Provisional; exact paths and grace interval depend on the Sol
  timing test
- **Decision:** Each long run prints a stable sweep-wide control-file path and
  exact inspect, request, and clear commands. Supported actions are:

  - `PAUSE`: finish the current atomic generation, persist a resumable
    checkpoint, prevent new work, sync stable artifacts, and exit paused;
  - `STOP`: finish the current atomic generation, preserve the valid prefix as
    stopped/censored, prevent new work and later phases, sync stable artifacts,
    and exit stopped.

  The marker is sticky and only the operator clears it. Workers never consume
  or delete a sweep-wide action. Poll before and after every generation, so the
  declared normal stop latency is at most one generation plus commit time.
  Scheduler warning signals enter the same `PAUSE` path and the coordinator
  propagates the action before finalization.

  One idempotent finalizer handles success, wall-time censoring, pause, stop,
  failure, and scheduler termination. It preserves valid state, records the
  original outcome and backup outcome separately, and never labels partial or
  unverified work complete.
- **Evidence:** `AGENTS.md` requires an operator-facing safeword contract and a
  unified finalizer; the donor router workflow supplies the control-file idea.
- **Alternatives considered:** Ad hoc process killing or a marker consumed by
  the first worker that sees it.
- **Likely sensitivity:** Operationally high; no intended scientific effect.
- **Affected configurations:** Control path, worker loop, Slurm signals,
  coordinator, finalizer, logs, and run status.

## Pending decisions before implementation or production

The following are intentionally unresolved rather than silently assumed:

- primary train/validation/test proportions;
- exact raw state/chunk format and primary transition-sampler quotas;
- recurrence-index implementation and its memory bound;
- worker-to-trajectory packing and whether interrupted trajectories resume in
  the same or a later scheduler unit;
- checkpoint/heartbeat cadence, deadline reserve, and worst-case recomputation;
- production Sol account, partition/QoS, maximum wall time, array limits, and
  fair-share concurrency; the RG-CAL-001 warmup selection does not resolve
  these production settings;
- exact production scratch roots; RG-STORE-003 fixes the private Hugging Face
  Bucket base, while each future parent must fix its run-specific prefix;
- the numeric CPU-hour/GPU-hour ceiling and calendar window for the week-scale
  campaign;
- CPU, GPU, or mixed production execution, determined by RG-SCALE-001.

Each of these must receive a decision ID, or explicitly update a Provisional
record above, before a production sweep depends on it.
