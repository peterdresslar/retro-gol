# AGENTS.md

## Project Objective

This project measures retrodictive prediction in Conway's Game of Life in the
hope of making learned causal ambiguity more explicit than it is in ordinary
predictive learning.

`METHODS.md` is the living method and reproduction decision record.
`docs/experiment-summary.md` is the broader experimental overview. This file is
the working contract for code and experiment execution; where it conflicts with
the overview, follow this file and record any scientific consequence in
`METHODS.md`.

## Scientific Code

- Prefer clear, conventional scientific Python. Use widely accepted packages
  such as NumPy and scikit-learn when their methods are appropriate and can be
  stated plainly in a Methods section or appendix. Do not reimplement a
  standard estimator merely to avoid a dependency.
- Keep a small readable reference implementation of scientifically important
  operations. An optimized CPU or GPU path must be checked against the
  reference path on deterministic fixtures before its results are trusted.
- Use standard package abstractions when they clarify the method, such as a
  scikit-learn `Pipeline`. Avoid custom frameworks, registries, factories,
  plugin systems, inheritance trees, or generalized backends without a
  concrete second use case.
- Prefer a few direct functions and plain data structures. A little visible
  duplication is better than an abstraction that hides the scientific
  procedure. Do not optimize for production-scale extensibility.
- Align code names with mathematical notation when the mapping is real and
  documented, for example `x_t`, `x_next`, `delta_v`, `mu`, or
  `period_lambda`. Use descriptive names such as `neighbor_count` and
  `output_dir` where mathematical shorthand would be less clear. Avoid opaque
  abbreviations.
- Document array shapes, dtypes, units, bit ordering, coordinate conventions,
  and the correspondence between code names and mathematical symbols. Comments
  should explain scientific meaning, assumptions, or invariants rather than
  narrate obvious syntax.

## Explicit Inputs and Outputs

- Do not provide implicit defaults for experimental or scientifically
  consequential parameters, random seeds, input datasets, output locations,
  run IDs, artifact prefixes, overwrite behavior, or backup requirements.
  Require them in a configuration or command invocation and fail if they are
  missing.
- Defaults are acceptable for inconsequential interface settings when they make
  the code easier to use. Preserve every resolved setting in the run manifest.
  For third-party estimators, record the package version and complete resolved
  parameter set; do not assume a library default is stable across versions.
- Validate the complete configuration before compute begins. Materialize the
  resolved configuration and planned outputs without silently substituting
  another path, dataset, implementation, device, or parameter.
- Refuse to write into a completed or unexpected nonempty output directory.
  Overwrite and restart-from-scratch must be separate explicit operations.
- Never emit placeholder, empty, synthetic, or default scientific results in
  response to missing input or failed computation.

## Errors and Failure Semantics

- Fail loudly and close to the source of an error. Error text should identify
  the failed condition, expected value, observed value, and relevant run,
  trajectory, unit, or path.
- Use ordinary explicit exceptions such as `ValueError`, `FileNotFoundError`,
  and `RuntimeError`. Do not build a custom exception hierarchy without a
  demonstrated need. Do not use `assert` for configuration, input, or data
  validation because assertions can be disabled.
- Do not silently catch exceptions, drop records, skip missing inputs, replace
  invalid values, retry failed operations, fall back to another implementation
  or device, or continue after an invariant fails. A top-level handler may
  record failure context and preserve valid artifacts, but it must re-raise or
  exit nonzero. Any future retry policy must be an explicit method decision and
  must retain every failed attempt in the log.
- Expected scientific terminal states such as extinction, exact recurrence,
  operator stop, and wall-time censoring are explicit data statuses rather than
  software errors.
- Treat numerical warnings or convergence failures as errors unless a specific
  warning has been reviewed and its handling recorded in `METHODS.md`.
- A failed unit may preserve an error manifest and validated partial output,
  but it is not complete. Aggregation must verify expected coverage and refuse
  to proceed when required units, fields, or checksums are absent.

## Research and Reproduction Standards

- Before implementing a consequential ambiguous choice, give it a stable
  decision ID in `METHODS.md` and record the decision, status, rationale,
  likely sensitivity, and affected configurations. Record evidence and
  alternatives when they materially clarify the choice. Supersede decisions
  explicitly after runs depend on them.
- Preserve every run's resolved configuration, seeds, code revision,
  dependency versions, environment, hardware, thread settings, data selection,
  and local and remote output locations.
- Do not overwrite completed runs. Keep raw results auditable and derive
  summaries or training views from identified run-level artifacts.
- Predefine reproduction criteria before expensive sweeps. Comparable results
  require comparable data, axes, metrics, and regimes, not merely similar
  plots.
- Keep GitHub focused on code, small fixtures, configurations, tests,
  manifests, and documentation. Store generated corpora, large checkpoints,
  and bulk metrics in the approved private artifact store.
- Do not publish data, checkpoints, or external artifacts without explicit
  approval.

## Experiment Operations

- Scale in explicit stages: plan-only dry run, minimal smoke test, wall-time
  and storage probe, forced-failure/restart test, bounded pilot, then an
  authorized full sweep. A plan-only run validates and writes the plan but does
  not allocate expensive hardware, construct the full dataset, or sync
  artifacts.
- Treat calibration as data. Measure initialization and warm-up separately from
  steady-state compute, recurrence or sampling overhead, checkpoint and backup
  time, peak memory, and I/O. Use those measurements to choose wall time,
  checkpoint cadence, resources, and array concurrency.
- Materialize the complete sweep plan before submission with stable unit IDs,
  configurations, seeds, and expected outputs. Derive scheduler-array bounds
  from that manifest. A launched plan is immutable; revisions receive new IDs.
- Use the same direct experiment entry point locally and under Slurm. Bound
  processes and library threads to the requested allocation and cap array
  concurrency to responsible fair-share use.
- Give each array unit a disjoint staging directory. Avoid shared mutable state
  by construction. If a shared run manifest or aggregate is necessary, give it
  one simple coordinating writer and fail when another active writer is
  detected; do not introduce a distributed coordination service.
- Persist validated unit results as they finish. Write state, manifests, and
  completion markers through a temporary path followed by atomic replacement.
  Resume only from validated checkpoints and skip only units whose expected
  outputs and checksums are complete.
- Long units emit a timestamped heartbeat at an explicit configured interval
  containing the unit ID, completed and planned work, throughput, and the
  latest checkpoint and backup state.
- Use one visible file-based operator control with the small vocabulary and
  semantics defined in `METHODS.md`. Print the control path and inspect,
  request, and clear commands. Keep the marker sticky, check it at the declared
  atomic boundary, and route scheduler warning signals through the same path.
- Use one straightforward finalization path for success, censoring, operator
  stop, failure, and scheduler termination. Preserve the latest valid state,
  record computation and backup outcomes separately, and re-raise or preserve
  the original nonzero failure outcome. Do not build a generalized finalizer
  framework.
- Do not let concurrent jobs install packages or mutate a shared checkout.
  Prepare and pin the environment before the sweep and record its identity.
- Treat cluster scratch as working storage, never the sole copy. Production
  runs require an explicit approved private destination. Preflight the backup
  client, authentication, and destination; inspect an allowlisted dry-run sync;
  sync only finalized artifacts; and verify the remote manifest and checksums.
  Missing backup tools, authentication, required objects, or verification are
  explicit failures, never successful skips. Do not delete remote data during
  routine sync, and never expose credentials in commands, source, manifests, or
  logs.

## Verification

- Add focused tests for scientific invariants, explicit failures, and the
  changed path. Prefer small deterministic fixtures whose expected states can
  be inspected directly.
- Check optimized implementations bit-for-bit against the reference
  implementation on supported shapes and boundary cases.
- Test failure behavior, including missing parameters, unexpected shapes,
  nonempty outputs, corrupted checkpoints, forced interruption, and incomplete
  aggregation.
- Do not launch costly compute, training, uploads, or large downloads unless
  explicitly authorized. Report exactly what was and was not run.

## Working Style and Communication

- Read the repository, `METHODS.md`, and relevant source material before
  proposing structural changes.
- Keep edits narrow and leave unrelated code, prose, formatting, and metadata
  alone.
- Add a dependency or abstraction only when it makes the scientific method
  clearer, enables faithful reproduction, or removes demonstrated complexity.
- Use structured parsers and data formats directly; do not introduce a custom
  schema or parser framework without a concrete need.
- Surface uncertainty, failed assumptions, warnings, and source discrepancies
  directly. Do not smooth them into confident prose.
- Ask before destructive operations, broad refactors, substantial compute
  expenditure, publication, or changes to scientific scope.
- Communicate concisely and technically. Lead with findings and decisions,
  state assumptions, and distinguish reproduced results from interpretation.
