# Changelog

Talos's own git history. This repo was extracted from a live skill repo with tags for the three
checkpoints below. Every entry is a real commit; dates and messages come from `git log`.

## v1-baseline, 2026-09-05

`feat(skill): state-over-history baseline from arXiv:2608.26263`

First working version. Deterministic execution-state runtime: `(P, Σ, O)` prompt, dict merge
with null-deletion, schema-owned validation, on-disk patch journal. Three deliberate deviations
from the paper from day one (see `references/paper-findings.md`, "Deviations in this
implementation"):

- `{"$append": [...]}` op for lists, so a growing list never has to be re-emitted whole
- a merge-contract block printed in every step's prompt (the paper only says "a dict of your
  state updates")
- `patches.jsonl`, a full on-disk journal of every patch, accepted or rejected, with reasons
  (the paper discards history outright and accepts that as a stated limitation)

This is the version most of the benchmark matrices ran against.

## v2-blob-runtime, 2026-09-05

`feat(runtime): per-field byte limits, enforced on the merged state`

Problem found while building the CTF schema: a field meant to cache file bodies needs a ceiling,
but checking the patch is not enough. Two individually-small artifacts can still overflow the
field once merged. Fix: run the size check after the merge, not on the incoming patch. Eviction
happens by patching a key to `null`, same as any other delete.

Worth recording that the machinery this enabled turned out to be a dead end. At a 15-step cap
the blob cache and manifest arms beat plain state, 20-22 solves against 19. At a 30-step cap
plain state reached 26 while they sat at 21-22, worse than the base they were built to fix and
more expensive. Raising the step cap cured the same failure better than the mechanism did. The
eviction path has never fired in 288+ episodes.

## v3-persistence-fixes, 2026-09-05

`feat(runtime): persistence-aware prompt + per-field patch atomicity`

The version shipped in this repo. Built after a read-only diagnosis pass over the benchmark
results identified why the skill was losing: a fact observed at step N had nowhere to survive to
step N+1 unless the model committed it immediately, and the prompt explained the merge mechanics
in full without ever saying why committing mattered.

1. **Urgency line in the prompt.** Tells the model that the current observation will not be
   shown again next turn, and names repeating a command as the observable sign that a fact was
   lost. The first wording fixed one traced task and left another unchanged; a second, directive
   wording fixed both.
2. **JSON-first response order.** The state patch and action block now come before the
   discardable reasoning, so a reply that runs long truncates the reasoning nobody reads rather
   than the JSON. Root cause of a format-reliability floor that scored unparseable replies as
   hard zeros.
3. **Per-field patch atomicity.** A field over its byte ceiling is dropped on its own; the rest
   of the patch still applies. Previously one oversized field voided the whole patch.
4. **`cmd_summary` from `str` to a byte-capped list** across the CTF schemas, so running notes
   accumulate instead of requiring one all-or-nothing scalar rewrite.

Result on the three traced tasks: from 3 of 9 pre-fix episodes solved to 3 of 3, at roughly a
third of the steps. One out-of-sample task that had never been solved in three pre-fix attempts
was solved post-fix; a second stayed unsolved. 22/22 unit tests.

The caveat stated in the commit message stands: these fixes were iterated against the same three
tasks used to validate them, and the benchmark matrices were never re-run against this version.

Also checked in schemas that existed as working files but had never been committed: `ctf-plan`,
`ctf-plan-blob`, `taubench`, `taubench-plan`, `warehouse`, `warehouse-plan`.

## Naming: state-over-history becomes Talos, 2026-09-05

Three commits, same evening.

- `docs: give the skill a short spoken name, Talos`. Wanted a one-word trigger like
  "superpowers" instead of spelling out "state-over-history" every time.
- `rename: skill is now named talos, not state-over-history`. Made it canonical rather than a
  nickname: frontmatter `name:`, directory and heading changed together. "state-over-history"
  survives as the name of the underlying idea.
- `fix(description): put the trigger condition back at the front`. An earlier edit put the
  naming story and the arXiv citation ahead of the actual trigger condition in the skill's
  `description:` frontmatter. That is backwards for a field whose only job is to be matched
  against a task description by the harness, where human-interesting context is noise.

## Schema catalogue reorganized by shape, 2026-09-05

`docs(schemas): pick by shape, not filename; add decision schema`

Question that prompted it: would an agent connect a file named `migration.json` to a plain
feature build-out, or `debug-hunt.json` to a GCP and Terraform incident that is not a "debug" in
the traditional sense? Realistic risk that it would not. Filenames were shaped by the paper's
own examples, not by the shape of the underlying problem. Rewrote schema selection around five
shapes (hunt, sweep, reversible multi-step change, queue, weigh options), each with an explicit
"also fits despite the name" column. Added `decision.json` for the one shape nothing else
covered.

## Benchmarking, 2026-09-03 to 2026-09-05

Roughly 2,300 episodes that count plus about 1,800 discarded, across InterCode CTF, a
reconstruction of the paper's warehouse, and τ-bench retail, on three models. Full tables in
`BENCHMARKS.md`.

Chronologically this came before `v3-persistence-fixes` and motivated it. The matrices measured
`v1-baseline` and `v2-blob-runtime`. Against those, a plain transcript won on solve rate at
short horizons while the skill cost 2 to 5 times less; the skill won on the warehouse at T=100;
and it failed completely on τ-bench, where it also cost more.

Two results reframed the rest. A crossover measured on the warehouse puts the break-even at
about 31 steps, and every solved CTF episode took 4 to 6, so the CTF benchmark sits entirely on
the wrong side of the line it was being used to test. And the warehouse win at T=100 is partly
the control degrading: its malformed-reply rate climbs to 48.5% at T=200 while the skill's stays
flat at 7 to 10%.

## What's next

- Re-run the matrices against `v3-persistence-fixes`. Nothing else in this list matters as much.
- Native tool-call support for the action contract, so environments like τ-bench are not
  structurally excluded.
- A runtime-level stall detector. Written and unit-tested, never validated against a live
  episode.
