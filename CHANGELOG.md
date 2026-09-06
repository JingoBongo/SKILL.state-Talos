# Changelog

Talos's own git history (this repo was extracted from `~/.claude/skills/talos`, a live git
repo with tags for the three checkpoints called out below). Every entry here is a real commit,
not a reconstruction — dates and messages are copied from `git log`.

## v1-baseline — 2026-09-05

`feat(skill): state-over-history baseline from arXiv:2608.26263`

First working version. Deterministic execution-state runtime: `(P, Σ, O)` prompt, dict merge
with null-deletion, schema-owned validation, on-disk patch journal. Three deliberate deviations
from the paper from day one (see `references/paper-findings.md` → "Deviations in this
implementation"):

- `{"$append": [...]}` op for lists, so a growing list never has to be re-emitted whole
- a merge-contract block printed in every step's prompt (the paper only says "a dict of your
  state updates")
- `patches.jsonl` — a full on-disk journal of every patch, accepted or rejected, with reasons
  (the paper discards history outright and accepts that as a stated limitation)

## v2-blob-runtime — 2026-09-05

`feat(runtime): per-field byte limits, enforced on the merged state`

Problem found while building the CTF schema: a field meant to cache binary/blob artifacts
(`references/schemas/ctf-blob.json`) needs a ceiling, but checking the *patch* isn't enough —
two individually-small artifacts can still overflow the field once merged together. Fix: run
the size check after the merge, not on the incoming patch. Eviction happens by patching a key
to `null`, same as any other delete.

## v3-persistence-fixes — 2026-09-05

`feat(runtime): persistence-aware prompt + per-field patch atomicity`

Four changes, all traced back to the paper's own stated limitation #2 — a fact dies one step
after it's observed unless it gets committed to state immediately:

1. **Urgency line in the prompt** — tells the model outright that the current observation
   will not be shown again next turn, and to notice when its next action would repeat a
   command it already ran (a symptom of having lost a result it should have kept).
2. **JSON-first response order** — the state patch + action block now comes *before* the
   discardable reasoning, so a reply that runs long only ever truncates the reasoning nobody
   reads, never the part that matters.
3. **Per-field patch atomicity** — a field that goes over its byte ceiling is dropped on its
   own; the rest of the patch still applies. Previously an oversized field voided the whole
   patch.
4. **`cmd_summary` changed from `str` to a byte-capped list** across the CTF schemas, so a
   partial note can accumulate across turns instead of demanding one full rewrite each time.

Also checked in five schemas that existed as working files but had never been committed:
`ctf-plan`, `ctf-plan-blob`, `taubench`, `taubench-plan`, `warehouse`, `warehouse-plan`.

Validation at this point was small-scale and explicitly not claimed as more: 22/22 unit tests,
plus a manual spot-check across 3 CTF tasks iterated on repeatedly while building the fix — a
real risk of tuning to that sample, called out as such in the commit message rather than
papered over.

## Naming: state-over-history → Talos — 2026-09-05

Three commits, same evening:

- `docs: give the skill a short spoken name, Talos` — wanted a one-word trigger like
  "superpowers" instead of spelling out "state-over-history" every time. Talos (the bronze
  automaton that patrols on a fixed loop, acting only on what it currently perceives) fits the
  mechanism, not just the vibe.
- `rename: skill is now named talos, not state-over-history` — made it the canonical name
  rather than a nickname: frontmatter `name:`, directory, and heading all changed together.
  "state-over-history" survives only as the name of the underlying idea, still used in prose.
- `fix(description): put the trigger condition back at the front` — an earlier edit had put
  the naming story and the arXiv citation ahead of the actual trigger condition in the
  skill's `description:` frontmatter. That's backwards for a field whose entire job is to be
  matched against a task description by the harness — human-interesting context is noise
  there. Trigger clause restored to the front; naming moved to a short trailing clause.

## Schema catalogue reorganized by shape — 2026-09-05

`docs(schemas): pick by shape, not filename; add decision schema`

Question that prompted it: would an agent actually connect a file named `migration.json` to a
plain feature build-out, or `debug-hunt.json` to a GCP + Terraform incident that isn't a
"debug" in the traditional sense? Realistic risk that it wouldn't — filenames are shaped by
the paper's own examples (CTF, migration), not by the shape of the underlying problem.
Rewrote the schema-selection section around five shapes — **hunt**, **sweep**, **reversible
multi-step change**, **queue**, **weigh options** — each with an explicit "also fits despite
the name" column. Added `decision.json` for the one shape nothing else covered: non-coding
deliberation over a bounded option set.

## First-party skill-vs-no-skill benchmarking — 2026-09-03 to 2026-09-05

Four rounds, ~2,500+ episodes, ~$15–20 spent, three models (haiku-4.5, deepseek-v4-flash,
sonnet-4.5 spot check). Full tables and methodology in `BENCHMARKS.md`. Headline result: the
skill beats a plain transcript on exactly one of three scenario shapes tested (long-horizon,
irrecoverable-past — e.g. the paper's own warehouse setup), loses on short-horizon
re-readable-file tasks (InterCode CTF, at every scale from 12 to 237 episodes) though 2–5×
cheaper, and fails outright on real multi-turn tool-call dialogues (τ-bench retail) due to an
architecture gap, not a tuning one. This directly motivated `v3-persistence-fixes` above, and
independently reproduces — the hard way, by losing benchmarks first — the boundaries already
written into `SKILL.md`'s "When to keep the transcript instead" section.

## What's next

- Per-field patch atomicity for `check_limits()` — flagged as the highest-confidence, not-yet-
  re-benchmarked fix (see `BENCHMARKS.md`).
- A runtime-level stall/loop detector — designed and unit-tested, not yet validated against a
  live episode.
- Native tool-call support for the action contract, so environments like τ-bench aren't
  structurally excluded.
