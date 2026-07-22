# Retrodictive Learning in Conway's Game of Life

## Experiment summary

This project asks whether a learner trained to infer the past of Conway's Game of Life can recover the ambiguity created by an irreversible process, rather than merely memorize one history that happened to produce a given future.

Game of Life is deterministic forward: for any state \(x_t\), the standard update rule \(F\) determines exactly one next state,

\[
x_{t+1} = F(x_t).
\]

The inverse is different. A state may have no predecessor, one predecessor, or many distinct predecessors. Given a proposed past \(\hat{x}_{t-1}\), however, validity is decidable by running it forward once and checking

\[
F(\hat{x}_{t-1}) = x_t.
\]

This gives the experiment an unusually strong candidate-level oracle. A prediction can be classified as a known past, a new but valid past, or an invalid past. On sufficiently small systems, where the complete predecessor set can be enumerated or solved exactly, we can also measure how much of the available causal ambiguity the learner recovers.

The central capacity sweep varies the number of learned parameters while holding the task and data construction fixed. The primary question is whether retrodictive performance exhibits the regimes associated with double descent: underfitting, a difficult interpolation region, and improved generalization after overparameterization.

## Research questions

1. As model capacity increases, does retrodictive test error follow a double-descent curve?
2. Near interpolation, can we distinguish generalized aliasing from ordinary memorization failure?
3. Do larger models recover valid but previously unseen predecessors, or only reproduce predecessors observed during training?
4. Where exhaustive predecessor sets are available, how well does the learned distribution cover them?
5. Do the proposed mechanisms around interpolation occur at one threshold or across a wider band?
6. How do information destruction and predecessor multiplicity change the location or shape of the observed regimes?

## Important distinctions

### Candidate validity is exact; exhaustive coverage is conditional

Every proposed predecessor can be validated exactly with the forward rule. That does not mean that every valid predecessor can be cheaply enumerated. Complete coverage measurements should therefore be reserved for board sizes or local windows where exhaustive search, SAT/SMT methods, or another exact solver are tractable. Larger systems can still support exact candidate-validity measurements and sampled estimates of diversity.

### Causal ambiguity is not automatically generalized aliasing

The many-to-one forward map creates intrinsic inverse ambiguity: information has been destroyed, so several pasts can be consistent with the same observed future. Generalized aliasing is a claim about the learner: its finite representation or data may make distinct explanations indistinguishable. The experiment should measure both without treating them as synonyms.

### A valid new past is not an error

Pixelwise disagreement with the recorded predecessor is insufficient as the main score. A generated state may differ greatly from the observed training predecessor and still be causally correct. Forward validity must therefore be evaluated before a retrodiction is labeled incorrect.

## Experimental design

### 1. Generate forward trajectories

Use standard Conway rules, B3/S23, on finite square grids with toroidal wrapping. Every run should be reproducible from a recorded random seed and configuration.

The accepted initial design contains four crossed simulation strata:

- initial live-cell density \(p = 0.20\)
- initial live-cell density \(p = 0.325\)
- \(N = 10\), hence \(M=N^2=100\)
- \(N = 32\), hence \(M=N^2=1024\)

Initial boards use fixed live-cell populations rather than independent Bernoulli cells. Where \(pM\) is fractional, floor and ceiling populations are balanced deterministically across the stratum as specified in `METHODS.md`. Every trajectory records its requested density and realized integer population.

The corpus has no fixed million-run target. Its initial scale is governed by measured Sol throughput and a reviewed fair-share wall-time budget. Direct comparisons across the four strata must account for the unequal trajectory and transition counts that equal compute time may produce.

Each run advances until the first applicable stopping condition:

- extinction;
- a fixed point;
- the first exact recurrence of the complete coordinate-fixed board;
- the configured wall-time deadline.

Exact recurrence does not quotient out translation, rotation, or reflection. Translating spaceships therefore have an opportunity to wrap around the torus and interact before an exact board recurrence proves periodicity. A wall-time stop is recorded as a censored trajectory, not as completed recurrence. The initial corpus has no generation cap; adding one would require a new recorded method decision.

At minimum, retain reproducible transition pairs \((x_{t-1}, x_t)\), run metadata, and summary statistics. Full trajectories should be bit-packed and retained only where their scientific value justifies the storage cost; seeds and deterministic replay can substitute for indiscriminate trajectory storage.

### 2. Construct leakage-resistant datasets

Split by complete simulation trajectory or seed, never by randomly mixing adjacent frames from the same trajectory across train and test sets. Construct several evaluation regimes:

- in-distribution held-out trajectories;
- held-out initial densities;
- held-out system sizes or horizons;
- targets grouped by activity, time, attractor proximity, and estimated predecessor multiplicity;
- small exact-oracle cases with complete predecessor sets.

Repeated or nearly identical late-stage states should be measured and controlled so that an apparent generalization result is not dominated by common still lifes or short-period oscillators.

### 3. Train a retrodictive learner

Train a conditional model for predecessor states,

\[
q_\theta(x_{t-1} \mid x_t),
\]

and sweep ordinary learned parameter count. Architectural width, depth, and channel count may be used to realize that sweep, but the experimental capacity variable is the resulting number of trainable weights.

The model must be able to generate multiple candidate predecessors or assign probability across alternatives. A single deterministic reconstruction cannot represent the true one-to-many inverse relation.

At least two training objectives are worth comparing:

- **Observed-history objective:** learn from the particular predecessor recorded in each trajectory.
- **Validity-aware objective:** add a forward-consistency term or exact constraint so that alternative valid predecessors are not automatically punished as wrong.

Sweep model capacity and training-data volume independently. Train multiple random seeds to a common convergence criterion so that parameter count is not silently confounded with training time.

### 4. Evaluate each generated predecessor

For target state \(x_t\), sample one or more candidates \(\hat{x}_{t-1}\) and assign each to one of three primary classes:

1. **Memorized/observed:** matches a predecessor seen with this target in training.
2. **Novel-valid:** was not observed for this target, but \(F(\hat{x}_{t-1}) = x_t\).
3. **Invalid:** fails the forward check.

Where the complete predecessor set \(P(x_t)\) is known, additionally measure support coverage and probability mass over

\[
P(x_t) = \{x : F(x) = x_t\}.
\]

## Primary measurements

- training loss and held-out loss across parameter count;
- the point or region where training error reaches zero;
- forward-validity rate;
- observed-predecessor recovery rate;
- novel-valid predecessor rate;
- invalid predecessor rate;
- number and diversity of unique valid predecessors recovered;
- coverage of the exact predecessor set, where available;
- probability mass assigned to valid predecessors, where estimable;
- calibration and conditional entropy of the learned predecessor distribution;
- Hamming or pixelwise error, retained only as a secondary descriptive metric;
- results stratified by predecessor multiplicity and other measures of information destruction.

Optional mechanistic diagnostics can track effective rank, singular spectra, representation geometry, and solution flatness across the same sweep. These would help connect an observed error curve to the proposed Wilson, Transtrum, and Schaeffer mechanisms rather than treating every peak as the same phenomenon.

## Hypotheses

1. Small models will underfit and produce a high rate of invalid predecessors.
2. Around interpolation, test performance and conditioning will worsen even as training error approaches zero.
3. Beyond interpolation, validity and novel-valid recovery will improve if overparameterization supports genuine generalization.
4. The different diagnostics may not align at one discrete threshold; interpolation may appear as a band containing several partially independent effects.
5. Targets with greater predecessor multiplicity will expose the difference between reproducing an observed history and learning a calibrated space of possible histories.

Failure to observe double descent is a valid result. It would constrain the portability of the proposed theory and identify which task, model, or measurement assumptions are required for the effect.

## Baselines

- nearest-neighbor or retrieval-based memorization;
- a deterministic reconstruction model;
- a capacity-matched conditional generative model;
- random candidate generation followed by forward validation;
- exact or solver-based predecessor sampling on tractable systems;
- a forward-consistency optimizer that searches separately for each target without learning across examples.

## Data and execution plan

Start with a reference CPU implementation and small deterministic fixtures. Benchmark vectorized NumPy against GPU execution before adopting CuPy; accelerator use should follow measured throughput rather than assumption.

Production runs on Sol should use Slurm job arrays with independent shards. Each shard should write atomically and include a manifest containing configuration, code version, seed range, counts, checksums, and completion state. Failed or preempted shards must be safely rerunnable.

Summary statistics should include:

- run length and stopping reason;
- initial and final live-cell density;
- population and activity over time;
- time to extinction, fixed point, or detected recurrence;
- detected period;
- counts of stored transitions and any dropped or invalid records;
- throughput, peak memory, and storage per run.

Scale through plan-only, smoke, wall-time/storage, forced-restart, and bounded-pilot stages before authorizing an approximately week-scale fair-share campaign. The campaign manifest, rather than an arbitrary trajectory count, defines its resource ceiling.

## Immediate pilot

1. Implement a deterministic bit-packed toroidal B3/S23 kernel together with the wall-time tester that is the first execution objective.
2. Verify exact recurrence, generation atomicity, wall-time censoring, forced termination, and restart behavior.
3. Benchmark runtime, recurrence memory, checkpoint cost, and storage across the four accepted strata on viable CPU and GPU paths.
4. Build the exhaustive \(N=5\) predecessor oracle as a separate exact tier.
5. Train one simple conditional model across a modest capacity sweep.
6. Report the three-way outcome rates: observed, novel-valid, and invalid.
7. Freeze transition-sampling quotas and use the bounded pilot to decide whether a week-scale forward corpus is justified.

## Success criterion

The strongest result is not merely low reconstruction error. It is evidence that, as capacity changes, a learner moves from invalid or memorized retrodictions toward a calibrated set of valid alternatives, including histories it was never shown. That would let us study whether a learner has recovered the causal ambiguity of an irreversible process rather than simply guessed one plausible past.

## Initial theoretical context

- Frank, S. A. (2026). *Generalization as the great leap in evolvability: Insights from machine learning.* https://doi.org/10.1093/evolut/qpag111
- Transtrum, M. K., Hart, G. L. W., Jarvis, T. J., & Whitehead, J. P. (2025). *Generalized aliasing explains double descent and informs model design.* https://doi.org/10.1103/qy5r-p5b7
- Schaeffer, R., et al. (2023). *Double descent demystified: Identifying, interpreting & ablating the sources of a deep learning puzzle.* https://doi.org/10.48550/arXiv.2303.14151
- Wilson, A. G. (2025). *Position: Deep learning is not so mysterious or different.* https://proceedings.mlr.press/v267/wilson25a.html
