# Source findings

SKILL.state: Scalable Long-Horizon Agent Skills, by Badhe, Tiwari (Google) and Chung (Purdue).
arXiv:2608.26263v3, accepted at EMNLP, CC BY 4.0. No code was released; the LaTeX source
contains no repository link. This skill is an implementation from the paper's Algorithm 1 and
Appendix A.4, with two deliberate deviations noted at the bottom.

## The architecture

At step `t` the model receives `(P, Σ_t, O_t)`, the procedural spec, execution state and latest
observation, and emits `(R_t, ΔΣ_t, a_t)`: reasoning, state patch, action. Then
`Σ_{t+1} = Σ_t ⊕ ΔΣ_t`, where `⊕` is dictionary merge with null-deletion. `R_t` is discarded
permanently once the patch validates.

Within-step reasoning stays fully intact; multi-step chain of thought is not curtailed. What
is dropped is its *persistence*.

Complexity: conversational runtimes have `|C_t| = O(t)`, so `Σ|C_t| = O(T²)`. Here
`|P_t| = O(|P| + |Σ| + |O|)`, independent of `t`, so cumulative cost is `O(T)`.

Schema ownership and validation sit in the deterministic runtime, not the model. An invalid
patch triggers rollback-retry; malformed output cannot corrupt `Σ`.

## Baselines compared

| Runtime | Behavior |
|---|---|
| Prompt (ReAct) | appends every observation, reasoning trace and action |
| Memory (summarization) | rolling 3-step window plus a periodically updated natural-language summary |
| Stateful (LangGraph) | structured state block **plus** the full rolling transcript |

Models: Gemini-3-Flash, Gemma-4-31B-it, Qwen-3-8B-it. Temperature 0.0, top-p 1.0. Five
generator seeds per synthetic experiment; differences at T≥50 significant by paired t-test.

## Experiment 1: long-horizon scaling (warehouse)

| Horizon | SKILL.state | Baseline |
|---|---|---|
| T=100 | 65,408 tokens | Stateful 1,062,387 tokens (16.2× more) |
| T=200 | 0.94 score, 122k tokens | Memory 6.1M tokens (50× more) |

## Experiment 2: noise robustness (T=50)

Distractor events per turn: telemetry, irrelevant git branch activity, rule overrides.

| Noise | Prompt | Memory | Stateful | SKILL.state |
|---|---|---|---|---|
| 5 events | 0.68 | 1.00 | 1.00 | 1.00 |
| 20 events | 0.61 | 1.00 | 0.98 | 0.97 |
| 50 events | 0.53 | 0.96 | 0.98 | 0.98 |

Distractors are filtered at patch-generation time and never enter a later prompt.

## Experiment 3: state recovery

World state changed outside the agent's action loop. History-based runtimes hallucinated for
**5–8 consecutive turns** because obsolete facts in the prompt overpowered contradictory new
observations. SKILL.state recovered in **0 steps**. One scenario (a canceled order) failed for
every runtime.

## Experiment 4: public benchmarks (Gemini-3-Flash)

| Runtime | InterCode CTF pass@1 | tokens | τ-Bench Retail | τ-Bench Airline |
|---|---|---|---|---|
| Prompt (ReAct) | 43.2% | 977k | 48.2% | 21.8% |
| Memory | 46.4% | 1.03M | 29.9% | 23.6% |
| Stateful | 41.8% | 1.13M | 51.7% | 28.1% |
| SKILL.state | **54.2%** | **387k** | **58.3%** | **32.4%** |

CTF used one static 5-field schema across all 100 challenges: `discovered_flags`,
`tested_hypotheses`, `active_files`, `working_dir`, `cmd_summary`. Holding tested hypotheses
is what stopped repeated failed commands.

On τ-Bench Airline, large database responses pushed baseline prompts above 11,000 tokens per
step; state stayed flat near 2,800. Lesson: big tool output is exactly what must be projected
into state and dropped.

## Experiment 5: budget-matched controls (T=100, all pinned to ~1,800 tokens)

| Configuration | Score | Avg prompt | Total tokens |
|---|---|---|---|
| Full ReAct, unbounded | 0.84 | 36,362 | 1,245,413 |
| Truncated sliding window | 0.18 | 1,800 | 62,100 |
| Summary-capped | 0.52 | 1,840 | 63,400 |
| ReAct + LLMLingua | 0.22 | 1,810 | 62,350 |
| **SKILL.state** | **0.94** | 1,905 | 65,408 |

This is the load-bearing result: the gain is not from shorter prompts. Sliding window evicts
early allocations; statistical compression strips slot identifiers that look redundant and are
semantically vital. Structured state preserves the exact relational dependencies both destroy.

## Error taxonomy: weaker models

Gemma-4-31B at T=100 scored 0.42. Failure logs:

| Mode | Share |
|---|---|
| Premature state overwrite / deletion, omits existing keys instead of merging in place | 68% |
| Schema comprehension / type coercion, nested list vs dict confusion | 20% |
| JSON syntax slips, malformed delimiters, trailing commas | 12% |

The authors' reading: small-model degradation comes from structured-output adherence, not
reasoning capacity. Their proposed fix is grammar-constrained decoding.

## Stated limitations

State must be a sufficient statistic for future execution. Where it is, discarding history is
lossless. It fails when:

1. No fixed schema is known in advance and structure must be discovered during execution
2. A correct update depends on an earlier observation whose relevance was unrecognized at the
   time, so it was never committed to state
3. The task objective is defined over the trajectory itself: auditing, debugging provenance,
   explaining past actions

Single-agent only. Multi-agent extension needs deterministic conflict resolution in `⊕` for
concurrent writes, which the paper does not exercise.

## Deviations in this implementation

1. **`{"$append": [...]}`**. The paper's `⊕` replaces lists wholesale, forcing the model to
   re-emit a growing list to add one item. That is the direct trigger for the 68% failure mode.
   An explicit append op is deterministic, cheaper, and removes the temptation.
2. **A merge-contract block in the prompt**. The paper's A.4 template states only that
   `state_patch` is "a dict of your state updates, set keys to null to delete". This
   implementation additionally prints the full merge algebra and the schema's field list on
   every step. Measured consequence: zero rejected patches across 12 haiku CTF episodes, where
   the paper reports 68% premature-overwrite on comparable open-weight models. It costs roughly
   600 characters per step, which is why short episodes are cheaper under an appended
   transcript than under state.
3. **`patches.jsonl` journal**. The paper discards history entirely and accepts limitation 3.
   Writing every patch (and every rejection, with its reason) to disk keeps full provenance at
   zero prompt cost, and makes `rollback` a replay rather than a snapshot restore.
