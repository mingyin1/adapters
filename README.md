# TavernBench parity results

This branch holds the results of the TavernBench parity runs for the Harbor adapter
([harbor-index/adapters#34](https://github.com/harbor-index/adapters/pull/34)). The workflow on the
[`parity-ci`](https://github.com/mingyin1/adapters/tree/parity-ci) branch writes them; see `parity/README.md`
there for how the runs are made.

- `runs/<label>/`: the runs made under one set of settings, with `summary.md` (accuracy per run and side,
  mean ± SEM, range overlap, per-task agreement, errors, cost) and `summary.json`.
- `dryrun/<label>/`: the latest dry run, made with a dummy API key to test the workflow. Every task scores 0.

Inside a label:

| Path | Content |
|---|---|
| `fingerprint.json` | the settings shared by all runs under the label |
| `original/run<N>/shard<K>/` | `answers.jsonl` from `run_agent.py`, `graded_answers.jsonl` (Harbor's error policy applied), `score.json` from the upstream scorer |
| `harbor/run<N>/shard<K>/` | `trials.jsonl` (one row per trial), Harbor's `job_result.json` and `job_config.json` |
| `*/run<N>/shard<K>/meta.json` | settings, task ids, commits and workflow run of the job |
| `*/run<N>/shard<K>/logs/` | agent and verifier logs (pilot runs only; all runs keep them in the workflow artifacts) |
