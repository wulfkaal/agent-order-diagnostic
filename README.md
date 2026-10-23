# Path Dependence Order Diagnostic

A run can change the machine that executes it. It may leave a model in memory, warm a cache, or change a file. The next result may reflect execution order as well as the tested system. This package tests for that carryover in a randomized evaluation trial.

Later runs are compared by whether the tested model or harness configuration appeared in the preceding execution block. The diagnostic pools the within-treatment differences and compares the observed result with 999 seeded re-draws of the recorded order. Results cover the model and harness-configuration channels. It never changes a score.

Nine synthetic calibration fixtures cover a null case, planted carryover, limited variation, incomplete execution, use of more than one machine, and retries. They are not evidence about live systems.

## Requirements

The package requires only the Python 3 standard library.

## Verify the Release

Run this command from the repository root:

```text
python3 verify.py
```

This checks the ordering and null-beacon vectors, regenerates all nine fixtures in a temporary directory, verifies every file and manifest hash, and tests every released input. A successful run ends with `verify: PASS`.

To run one input directly:

```text
python3 order_diagnostic.py run fixtures/01-null-w1.input.json
```

## Input Format

Each input is a JSON object with four top-level fields:

- `design_digest`: the recorded design digest as lowercase hexadecimal text.
- `beacon`: the 32-byte randomization beacon as 64 hexadecimal characters.
- `concurrency`: the number of slots in one execution block.
- `slots`: the complete list of scheduled slots.

Each slot records `instance`, `treatment_id`, `replicate`, `model_key`, `hp_digest`, `nodes`, `attempts`, and `outcome`. The `nodes` value is a sorted list of unique machine labels. The `attempts` value is an integer from zero through three. The `outcome` is `0`, `1`, or `null`. A null outcome means that the slot produced no eligible result.

## Output Format

The output repeats `design_digest`, identifies `version`, reports `null_draws`, and contains one row per channel in `channels`. Each row gives warm and cold counts, contributing treatment cells, state, and any reason. A computed row also gives the exact contrast in `delta_num` and `delta_den`, the rounded `delta_milli`, the number of null draws at least as extreme, and `flagged`.

Four reasons can replace a computed result:

- `incomplete`: at least one scheduled slot lacks an eligible outcome.
- `retries_present`: at least one slot required more than one attempt.
- `multi_node`: the recorded slots ran on more than one machine.
- `too_few_units`: the contrast is undefined, or the channel has fewer than 20 warm units or fewer than 20 cold units in contributing treatment cells.

The first three reasons apply to the full trial. The last can differ by channel. The recorded order cannot support the same inference when execution is incomplete, retried, spread across machines, or too thin for the contrast.

## Citation

Please cite Wulf A. Kaal, *Testing for Path Dependence in Agent Reputation Measurement: A Randomization Diagnostic* (draft 2026). `CITATION.cff` contains the metadata.

## License

The code and fixtures are released under the MIT License. See `LICENSE`.
