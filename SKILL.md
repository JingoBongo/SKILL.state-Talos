---
name: talos
description: Use when a task will run for many steps and has to stay correct across all of them: audits or migrations across dozens of files, data backfills, long refactors, multi-hour debug or CTF hunts, per-item batch work, or any job driven through subagents. Also use mid-run when a long task is going wrong: work being redone, findings that contradict earlier ones, decisions taken on facts that have since changed, or context filling before the job is finished. Also triggers on the name "Talos" or "Талос" alone, in either language.
allowed-tools: Bash(uv run --script ${CLAUDE_SKILL_DIR}/scripts/skillstate.py *) Read Write Edit Glob Grep Agent
---

# Talos

One word, easy in either language, not confusable with an IT term. Say it and this skill is
meant, the same way "superpowers" needs no unpacking. The bronze guardian that patrols on a
fixed loop and never carries more than what it currently knows is the reference. Formerly
named `state-over-history`; that phrase is still how the underlying idea (carry state, not
history) gets described throughout this file and the paper it implements.

A long task fails because the transcript grows, not because the model is weak. Every
observation, every discarded idea, every superseded fact stays in context and competes with
what is true right now. Past a few dozen steps the model spends its attention deciding which
of two contradictory facts is current.

So stop carrying history. Carry **state**.

Each step sees exactly three things: the task spec, the current state, and the latest
observation. The model reasons freely, then writes what survives into state, and the
reasoning is thrown away. State is the only thing that crosses a step boundary.

**The state is a sufficient statistic for the rest of the job.** If a fact matters later, it
goes into state the moment it is known. If it is not in state, it does not exist.

> ## Measured result: on everything tested, a plain transcript wins
>
> Warehouse at T=100, three seeds, control in the same harness: this skill and a transcript
> scored **identically on every seed**, and the skill cost **3.68x**. On InterCode CTF at full
> scale the two are statistically indistinguishable. On τ-bench the transcript leads 27/36 to
> 13/36.
>
> The reason is not that the architecture fails to do what it says. The state prompt genuinely
> does not grow with the horizon. But input is about 5% of the bill on a reasoning model, where
> 99% of the tokens are output and nearly all of that is reasoning, and handing the model a
> merge contract plus a four-key response format on every step **triples how much it
> deliberates**: 4686 output tokens per step against a transcript's 1527.
>
> Earlier versions of this file claimed a 2 to 5x cost saving. That came from a price table
> with the input/output ratio wrong by an order of magnitude. See `BENCHMARKS.md`.
>
> **Do not reach for this skill to save tokens.** The only case it was described for and that
> nothing here has measured is a job where the transcript would overflow the context window
> outright, or where the fact you need is thousands of steps back. There the alternative is not
> a cheaper transcript, it is losing the fact.

## When this applies

Read the box above first. Measured against a transcript, none of the cases below came out
ahead. They are the shapes the architecture was designed for, not shapes where it has been
shown to win.

- Work spanning many items: audits, migrations, backfills, batch edits, sweeps over files
- Long investigations: debugging, root-cause hunts, CTF, bisecting, log forensics
- Anything driven through subagents: each one starts blank, so state is the only carrier
- Tool calls that return a lot (DB rows, large file bodies, API dumps) and would otherwise sit
  in context forever
- A run already in trouble: repeated work, contradictions, stale assumptions, filling context

## When to keep the transcript instead

**By default.** Measured, a transcript matches this skill on quality and costs a third to a
quarter as much on every benchmark here. The cases below are the ones where state fails
outright rather than merely losing on cost.

| Situation | Why state fails |
|---|---|
| The trajectory is the deliverable: provenance, audit trail, "explain how you got here" | State keeps conclusions, not the path. Use the journal (below), or keep the transcript |
| No schema is knowable up front and structure must be discovered as you go | You cannot patch fields you cannot name yet. Explore first, then convert to state |
| An old observation turns out to matter, and was never committed | It is gone. If the task has this shape, over-collect into state early or keep history |
| An artifact must stay resident to reason over: source you are inverting, a spec you keep consulting | If the schema holds only its *path*, every step re-reads it and the run becomes a `cat` loop. Give the artifact its own field, or keep the transcript |

Also: one writer at a time. Concurrent patches to one state need conflict resolution this
runtime does not implement. Fan out reads, serialize writes.

## The loop

```bash
S="${CLAUDE_SKILL_DIR}/scripts/skillstate.py"

# once per run: pick a schema for the DOMAIN, not for this task
uv run --script "$S" init --schema "${CLAUDE_SKILL_DIR}/references/schemas/code-audit.json"

# each step
uv run --script "$S" prompt --instructions TASK.md --observation "<what just happened>"
#   -> the prompt shows the current state, a `state_hash: <hex>` line, and
#      `Latest Observation (obs#N): ...`
#   -> hand that text to a subagent, verbatim
#   -> it returns {"state_patch": {...}, "action": "...", "obs_ref": "obs#N", "state_hash": "<hex>"}
echo '<that json>' | uv run --script "$S" patch     # validates, merges, logs, prints new state
```

`patch` requires the full wrapper, which has exactly four keys:
`{"state_patch": …, "action": …, "obs_ref": …, "state_hash": …}`. The model echoes `obs_ref`
(e.g. `obs#7`) and `state_hash` (eight hex characters) verbatim from the prompt. A bare patch
with no wrapper is rejected for the missing fields; `action` is carried for the caller and the
runtime does not read it.

`obs_ref` names the **step**, not the render and not the text of the observation. It advances
when a patch is accepted, so rendering the prompt again (to log it, to retry a transport
error, to look at it) leaves the id the model echoed valid. A rejected patch does not advance
it, which is what lets the correction loop re-ask about the same step. A rejected patch exits non-zero, prints why, and
**leaves state exactly as it was**. Schema, type and limit errors are reported before
staleness: a patch can be both stale and malformed, and the malformed half is the half you can
act on. The schema and the merge live
in the script, so a bad generation cannot corrupt anything. Fix the patch and re-send; that
is the whole retry protocol. If the runtime rejects a patch as stale (obs_ref or state_hash
mismatch), the prompt is the authoritative source: re-derive the patch from it, and the runtime
re-prompts with the exact error (a correction), up to 2 times.

Each step produces three things and nothing else: the patch, the action, and the observation
you feed to the next step. The subagent's reasoning is not summarized, not appended, not kept.

## Commands

| Command | Use |
|---|---|
| `init --schema F` | create the store (`.state-over-history/`) |
| `show` | current state, compact; this is what goes in a prompt |
| `show --pretty` | current state for a human |
| `prompt --instructions F --observation S` | the full step prompt, state injected |
| `patch` (stdin) | validate + merge + journal one patch |
| `ids` | the `obs_ref` and `state_hash` a patch must echo right now |
| `journal [-n N]` | every patch including rejections and their reasons |
| `rollback --to N` | replay the journal to step N |

Run any of them with `--help` for flags.

Ask `ids` for the echo values rather than deriving them. Two harnesses here
recomputed the pair themselves and both drifted from the runtime the moment the
counter changed meaning, after which every patch was rejected.

Three flags matter on a long episode, where a per-step cost is paid hundreds of
times:

| Flag | On | Effect |
|---|---|---|
| `--no-state-hash` | `init` | drop the hash echo; the wrapper is three keys, not four |
| `--brief-after N` | `prompt` | teach the merge contract for N steps, then compress it to one line |
| `--no-stall-notice` | `prompt` | suppress the advisory stall notice |

`--no-state-hash` is for a single-writer loop, which is what an agent usually
is. The hash guards against a world that moved under the model between read and
write; if nothing else writes to the store, `obs_ref` already covers staleness
and the echo is paid for nothing. Measured at T=25: 13% less output.

`--brief-after` exists because the contract block is roughly half the prompt and
the model has read it N times by then. Measured: the rendered prompt drops 48%.

## Merge algebra

The script applies patches exactly this way, and the generated prompt states these rules so
the model is never guessing:

| Patch value | Effect |
|---|---|
| scalar or list | replaces what is there |
| nested object | deep-merges; siblings you omit are **kept** |
| `{"$append": [...]}` | extends an existing list without resending it |
| `{"$append": x}` | a bare value appends one element; the brackets are optional |
| `null` | deletes that key |
| key not in schema | whole patch rejected, state untouched |

`$append` exists because the dominant failure on weaker models is dropping existing keys while
rewriting a field. Never make the model re-emit a growing list to add one item.

The bare form is accepted because 94% of every patch rejection measured on a long
horizon was a model writing `{"$append": "one note"}` without the brackets. The
intent is not ambiguous and a correction round-trip costs more than the brackets
are worth. The prompt still teaches the list form.

## Choosing a schema

**Once per domain, not per task.** The paper reused one 5-field schema across all 100 CTF
challenges. Pick by **shape**, not by what the filename happens to be called. A schema
built for one kind of task fits any task with the same shape, regardless of domain.

| Shape | What it looks like | Reach for | Also fits, despite the name |
|---|---|---|---|
| **Hunt**: narrow an unknown by testing hypotheses, nothing to enumerate up front | debugging, root-causing, CTF, log forensics, bisecting | `debug-hunt`, `ctf` | any "what's actually true here" investigation: an incident across GCP Logging + `terraform plan`/state + wherever else, a flaky-test hunt, a security triage |
| **Sweep**: a known, boundable set of items, walked with a cursor, findings recorded per item | audits, reviews, reading N sources to synthesize | `code-audit` | evaluating N docs/vendors/candidates against the same checklist |
| **Reversible multi-step change**: phased, each step can fail or roll back, invariants must hold throughout | schema/data migrations | `migration` | **a feature build-out or a large refactor**, both are "touch N files, each can break something, verify the invariant (tests/build) after each, know how to back out" |
| **Queue**: independent items processed one at a time, each with its own outcome | batch edits, backfills | `batch-process` | any per-item job where items don't depend on each other |
| **Weigh options against criteria**, evidence gathered per option, no code involved | non-coding decisions, comparing approaches, "which of these" reasoning | `decision` | any long deliberation that has a bounded option set and needs to end in a pick, not open-ended musing |

A schema named `migration` full of `rollback_notes` and `invariants_verified` is exactly the
right shape for "add this feature across 12 files". The runtime does not care that the word
"feature" never appears in the filename. If you're picking one for a model to use
autonomously, say so explicitly in the instructions ("this is a reversible multi-step change,
use the migration-shaped fields even though this isn't a migration"), because a name mismatch
is a real risk: an agent skimming filenames will discard `migration.json` for a feature task
before it ever reads the fields.

**Nothing here fits a task whose structure you don't know yet**: first-pass research,
open-ended "figure out what even matters" exploration, discovery work. That's limitation #1
below: explore in a plain transcript first, and convert to a schema once you know what the
five fields would be. Forcing a schema onto that phase early is worse than not using this
skill at all.

Ready-made schemas are in `references/schemas/`: code-audit, migration, debug-hunt,
batch-process, decision, ctf. Copy the closest one and adjust.

Keep it small. State sits in the prompt at every single step, so each field costs tokens for
the entire run. Five fields is a normal size. Fifteen is a smell.

What belongs in state:

- Decisions taken, and what settled them
- Things already tried and ruled out. This is what stops repeated work
- Values discovered that cannot be cheaply re-derived (ids, credentials found, offsets, flags)
- A cursor: where you are in the work
- Findings so far, keyed by item

What does not:

- Anything re-obtainable by reading a file. Put the path in state, not the contents
- Raw tool output. Project it into a fact and drop the rest
- Narrative of what happened. That is history wearing a costume

## The journal

Every patch, accepted or rejected, is appended to `.state-over-history/patches.jsonl` with its
reason. The paper discards history outright and loses the audit trail; the journal keeps the
full trail **on disk, never in the prompt**. Zero token cost, complete provenance, and
`rollback --to N` replays it. This is what makes "the trajectory is the deliverable" survivable.

## Common failures

Straight from the paper's error taxonomy on weaker models, with what to do here:

| Failure | Share | Fix |
|---|---|---|
| Rewrites a field and silently drops existing keys | 68% | Deep merge handles siblings; use `$append` for lists; never re-emit whole collections |
| Confuses nested list vs object, wrong type | 20% | Schema declares the type and rejects mismatches. Read the rejection reason, don't retry blind |
| Malformed JSON, trailing commas | 12% | Rejected atomically; re-emit just the JSON block |

Two more, specific to running this inside an agent harness:

- **Keeping the transcript "just in case" alongside state.** Then you have both costs and
  none of the benefit. This is the paper's weakest baseline, not its strongest.
- **Compressing history instead of replacing it.** Budget-matched at equal tokens: sliding
  window 0.18, statistical compression 0.22, capped summary 0.52, structured state 0.94.
  Summarization is the thing this replaces.

## Why it works

Prompt size stays flat instead of growing with the step count: cumulative cost goes from
O(T²) to O(T). At 200 steps the paper measured 122k tokens against 6.1M for a summarizing
baseline, at equal or better accuracy. On 100 CTF tasks it raised pass@1 from 43.2% to 54.2%
while cutting tokens 60%. The gain came from state holding tested hypotheses, which stopped
the model re-running commands it had already tried.

When the world changes under the run, history-based agents kept acting on stale facts for 5–8
turns; state-based recovered in 0, because the decision reads current state and the correcting
observation lands in it immediately.

Full findings and numbers: `references/paper-findings.md`.

## Transcript policy: starting as a transcript and converting

Below about **31 steps** this architecture costs more than simply keeping the transcript, and
buys nothing. That crossover is measured, not assumed: state's prompt grows around 10
characters per step against a transcript's 69.

So on a job that may be short, do not choose up front. Pass `--hybrid-k K` to `prompt` and the
runtime announces which side of the crossover the episode is on:

```
transcript_policy: keep  (step 4 of K=31)
transcript_policy: drop  (step 31 of K=31)
```

While the policy is `keep`, hold your prior observations in context as well as the state;
state accumulates in parallel, so nothing is lost when it takes over. At `drop`, let them go.

This is the best-measured configuration in the project: on 237 InterCode CTF episodes it solved
192 against a transcript control's 199 and the plain state arm's 186, in 6.7 steps against 9.7,
hitting the step cap 18 times against 38. On the warehouse at T=200 it returned roughly twice
the score per dollar of the control. The runtime owns the policy because it is the only part
that knows the step number; the transcript itself stays with the caller, where it belongs.

`--hybrid-k 0`, the default, announces nothing and changes nothing.
