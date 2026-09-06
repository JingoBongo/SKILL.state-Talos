# Talos

> **SKILL.state: Scalable Long-Horizon Agent Skills** — Badhe, Tiwari (Google Research), Chung
> (Purdue University). [arXiv:2608.26263](https://arxiv.org/abs/2608.26263), accepted at
> EMNLP, CC BY 4.0.
>
> The paper describes the architecture (Algorithm 1, Appendix A.4) but released no code. Talos
> is an independent implementation built from the paper text alone, with three declared
> deviations (see `references/paper-findings.md`).

Talos is a [Claude Code](https://claude.com/claude-code) (and [Pi](https://github.com/badlogic/pi-mono))
skill that replaces an agent's growing chat transcript with a small, deterministic,
schema-validated piece of state. A long task fails because the transcript grows, not because
the model is weak. Past a few dozen steps the model spends its attention deciding which of two
contradictory facts in the transcript is still current.

Talos carries state, not history: at every step the model sees only the task spec, the current
state, and the latest observation. It emits a patch to that state, and its reasoning is thrown
away for good.

The name is one word, unambiguous in English and Russian ("Талос"), and not an IT-jargon
collision. It's named after the bronze automaton that patrols on a fixed loop, acting only on
what it currently perceives. The skill was originally called `state-over-history`; that phrase
still describes the underlying idea in the prose throughout this repo.

## Is this for you?

Read `SKILL.md`'s own "When this applies" and "When to keep the transcript instead" sections
first. They're the real answer, and now backed by measurement instead of just theory (see
Benchmarks below).

Short version: long, sequential, single-agent jobs where an old fact stops being retrievable
once the step that produced it has passed (audits, migrations, long debug hunts, batch or
backfill work). Not a fit for short jobs where re-reading a file is cheap, for work whose
schema you can't name yet, or for anything driven by real tool-call APIs rather than a
shell-command-shaped action.

## Repo layout

```
SKILL.md                    the skill itself — what Claude Code / Pi actually loads
scripts/skillstate.py       the deterministic runtime: schema, merge, validation, journal
tests/test_skillstate.py    22 unit tests pinning the merge algebra
references/
  paper-findings.md         everything measured in the paper, plus our declared deviations
  schemas/                  ready-made state schemas — CTF, migration, audit, debug-hunt, ...
INSTALL.md                  how to put this where Claude Code / Pi will find it
BENCHMARKS.md               our own skill-vs-no-skill measurements, the honest version
CHANGELOG.md                every real commit in this skill's history, and why it happened
LICENSE                     MIT
```

## Quick start

See `INSTALL.md`. Short version: clone this repo to `~/.claude/skills/talos` (personal) or
`.claude/skills/talos` inside a project (team-shared), then say "Talos", or describe a long
multi-step job, and Claude Code will load it on its own.

## How this differs from the paper

The paper specifies the architecture and one worked schema (CTF, 5 fields). It does not
publish code, and does not publish schemas for its own warehouse or τ-bench experiments —
those are reconstructions here, not transcriptions. Three deliberate runtime deviations, all
present since `v1-baseline`:

1. **`{"$append": [...]}`** for lists. The paper's merge replaces a list wholesale, forcing the
   model to re-emit a growing list to add one item — the direct trigger for its dominant
   failure mode on weak models (68% "premature overwrite"). An explicit append op removes the
   temptation.
2. **A merge-contract block printed in every step's prompt.** The paper's template says only
   "a dict of your state updates, set keys to null to delete." This implementation also states
   the full merge algebra and the schema's field list on every step. Zero patch rejections
   across the first 12 haiku CTF episodes we ran, against the paper's reported 68% on
   comparable open-weight models — though our own larger deepseek runs later did see
   rejections (up to 112 in one 237-episode arm), so this reduces the failure mode but doesn't
   eliminate it.
3. **`patches.jsonl`.** Every patch, accepted or rejected, journaled to disk with its reason.
   The paper discards history outright and accepts that as a stated limitation; this keeps the
   full audit trail at zero prompt-token cost and makes `rollback` a replay instead of a
   snapshot restore.

Full detail, including the paper's own benchmark tables (warehouse, InterCode CTF, τ-Bench,
the error taxonomy), is in `references/paper-findings.md`.

## Benchmarks — the short version

We ran our own skill-vs-no-skill comparisons across four rounds (~2,500+ episodes, ~$15–20,
three models) instead of taking the paper's numbers on faith. Full tables, every dead end, and
the two root-caused bugs behind the pattern are in `BENCHMARKS.md`. Headline:

| scenario shape | result |
|---|---|
| Long horizon, past genuinely unrecoverable (simulated warehouse) | skill wins — 0.76 vs 0.67 at T=100, cheaper |
| Short horizon, a file can just be re-read (InterCode CTF, 12→237 episodes) | skill loses on solve rate at every scale, 2–5× cheaper |
| Real multi-turn tool-call dialogue (τ-bench retail) | skill fails outright — 0–1 solved of 36, an architecture gap |

The paper doesn't run this exact comparison, so this isn't a caveat it states either — it
independently reproduces, the hard way, the boundaries `SKILL.md` already documents under
"When to keep the transcript instead."

## Development history

`CHANGELOG.md` walks every real commit: the RED-phase test that refused to fail, the A/B that
looked like a regression but turned out to measure agent compliance rather than the runtime,
the blob-cache fix for an artifact-thrash failure, the mutation that made things worse when two
good fixes were combined, and the eventual rename to Talos.

## License

MIT, see `LICENSE`. Use it, fork it, ship it — no attribution required beyond what MIT itself
asks for. If you improve the runtime or find a benchmark that contradicts what's in
`BENCHMARKS.md`, that's exactly what issues and PRs are for.
