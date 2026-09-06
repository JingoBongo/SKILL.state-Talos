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

## When this applies

- Work spanning many items: audits, migrations, backfills, batch edits, sweeps over files
- Long investigations: debugging, root-cause hunts, CTF, bisecting, log forensics
- Anything driven through subagents: each one starts blank, so state is the only carrier
- Tool calls that return a lot (DB rows, large file bodies, API dumps) and would otherwise sit
  in context forever
- A run already in trouble: repeated work, contradictions, stale assumptions, filling context

## When to keep the transcript instead

Three cases, from the paper's own limitations. In these, history *is* the work:

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
#   -> hand that text to a subagent, verbatim
#   -> it returns {"state_patch": {...}, "action": "..."}
echo '<that json>' | uv run --script "$S" patch     # validates, merges, logs, prints new state
```

`patch` accepts either the bare patch or the whole `{"state_patch":…,"action":…}` object.
A rejected patch exits non-zero, prints why, and **leaves state exactly as it was**. The
schema and the merge live in the script, so a bad generation cannot corrupt anything. Fix the
patch and re-send; that is the whole retry protocol.

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
| `journal [-n N]` | every patch including rejections and their reasons |
| `rollback --to N` | replay the journal to step N |

Run any of them with `--help` for flags.

## Merge algebra

The script applies patches exactly this way, and the generated prompt states these rules so
the model is never guessing:

| Patch value | Effect |
|---|---|
| scalar or list | replaces what is there |
| nested object | deep-merges; siblings you omit are **kept** |
| `{"$append": [...]}` | extends an existing list without resending it |
| `null` | deletes that key |
| key not in schema | whole patch rejected, state untouched |

`$append` exists because the dominant failure on weaker models is dropping existing keys while
rewriting a field. Never make the model re-emit a growing list to add one item.

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
