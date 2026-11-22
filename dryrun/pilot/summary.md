# TavernBench parity: `pilot`

> **Dry run.** Both sides used a dummy API key, so every agent stopped at its
> first request and every task scores 0. This tests the workflow, not the adapter.

Model `anthropic/claude-parity-dry-run` · Claude Code `2.1.330` · reasoning effort `default` · Harbor `0.27.0` (`docker`) · tasks: `pilot`

| Side | Complete runs | Accuracy per run (%) | Mean ± SEM (%) | Cost (USD) |
|---|---|---|---|---|
| Original (AutoTavern `run_agent.py` + upstream scorer) | 1 | run 1: 0.0 | 0.0 | – |
| Harbor (adapter + `claude-code` agent) | 1 | run 1: 0.0 | 0.0 | 0.00 |

Run-score ranges overlap: **yes** (the match criterion, meant for three runs per side).

## Errors

- original, run 1 (10 tasks, shards 1/1, job success): container could not be run: 10
- harbor, run 1 (10 tasks, shards 1/1, job success): agent ended with an error result: 10, exception: AgentAuthenticationError: 10, reward not valid (counted as 0): 10

## Per task

10 tasks have complete runs on both sides; the share of runs answered correctly differs on 0.

| Task | Scenario | Category | Original (correct/runs) | Harbor (correct/runs) |
|---|---|---|---|---|
| B3-000 | B3 | combat | 0/1 | 0/1 |
| B4-000 | B4 | combat | 0/1 | 0/1 |
| B5-000 | B5 | combat | 0/1 | 0/1 |
| D1-000 | D1 | selection | 0/1 | 0/1 |
| E3-000 | E3 | economy | 0/1 | 0/1 |
| N1-000 | N1 | strategy | 0/1 | 0/1 |
| O2-000 | O2 | ordering | 0/1 | 0/1 |
| P1-000 | P1 | positioning | 0/1 | 0/1 |
| R1-000 | R1 | rules | 0/1 | 0/1 |
| T2-000 | T2 | targets | 0/1 | 0/1 |

## By scenario (all complete runs pooled)

| Scenario | Original n | Original acc. (%) | Harbor n | Harbor acc. (%) |
|---|---|---|---|---|
| B3 | 1 | 0.0 | 1 | 0.0 |
| B4 | 1 | 0.0 | 1 | 0.0 |
| B5 | 1 | 0.0 | 1 | 0.0 |
| D1 | 1 | 0.0 | 1 | 0.0 |
| E3 | 1 | 0.0 | 1 | 0.0 |
| N1 | 1 | 0.0 | 1 | 0.0 |
| O2 | 1 | 0.0 | 1 | 0.0 |
| P1 | 1 | 0.0 | 1 | 0.0 |
| R1 | 1 | 0.0 | 1 | 0.0 |
| T2 | 1 | 0.0 | 1 | 0.0 |

Workflow runs: [45040092525](https://github.com/mingyin1/adapters/actions/runs/45040092525)

