# Talos

> **SKILL.state: Scalable Long-Horizon Agent Skills**, by Badhe, Tiwari (Google Research) and
> Chung (Purdue University). [arXiv:2608.26263](https://arxiv.org/abs/2608.26263), accepted at
> EMNLP, CC BY 4.0.
>
> The paper describes the architecture (Algorithm 1, Appendix A.4) but released no code. Talos
> is an independent implementation built from the paper text alone, with three declared
> deviations. See `references/paper-findings.md`.

Talos is a [Claude Code](https://claude.com/claude-code) (and [Pi](https://github.com/badlogic/pi-mono))
skill that replaces an agent's growing chat transcript with a small, deterministic,
schema-validated piece of state. A long task fails because the transcript grows, not because
the model is weak. Past a few dozen steps the model spends its attention deciding which of two
contradictory facts in the transcript is still current.

Talos carries state, not history. At every step the model sees only the task spec, the current
state, and the latest observation. It emits a patch to that state, and its reasoning is thrown
away for good.

## Is this for you?

Read `SKILL.md`'s own "When this applies" and "When to keep the transcript instead" sections
first. They are the real answer, and they are now backed by measurement rather than theory.

Short version: long, sequential, single-agent jobs where an old fact stops being retrievable
once the step that produced it has passed. Audits, migrations, long debug hunts, batch and
backfill work. Not a fit for short jobs where re-reading a file is cheap, for work whose schema
you cannot name yet, or for anything driven by real tool-call APIs rather than a
shell-command-shaped action.

There is a measured threshold for "long". State's prompt grows about 10 characters per step
against a transcript's 69, and they cross at **T ≈ 31 steps**. Below thirty steps this skill is
more expensive than just keeping the transcript, and buys you nothing.

## Repo layout

```
SKILL.md                    the skill itself, what Claude Code / Pi actually loads
scripts/skillstate.py       the deterministic runtime: schema, merge, validation, journal
tests/test_skillstate.py    27 unit tests pinning the merge algebra and the stall notice
references/
  paper-findings.md         everything measured in the paper, plus our declared deviations
  schemas/                  ready-made state schemas: CTF, migration, audit, debug-hunt, ...
INSTALL.md                  how to put this where Claude Code / Pi will find it
BENCHMARKS.md               our own skill-vs-no-skill measurements
CHANGELOG.md                every real commit in this skill's history, and why it happened
LICENSE                     MIT
```

## Quick start

See `INSTALL.md`. Short version: clone this repo to `~/.claude/skills/talos` (personal) or
`.claude/skills/talos` inside a project (team-shared), then say "Talos", or describe a long
multi-step job, and Claude Code will load it on its own.

## How this differs from the paper

The paper specifies the architecture and one worked schema (CTF, 5 fields). It does not publish
code, and does not publish schemas for its own warehouse or τ-bench experiments, so those are
reconstructions here rather than transcriptions. Three deliberate runtime deviations, all
present since `v1-baseline`:

1. **`{"$append": [...]}`** for lists. The paper's merge replaces a list wholesale, which forces
   the model to re-emit a growing list to add one item. That is the direct trigger for its
   dominant failure mode on weak models, 68% "premature overwrite". An explicit append op
   removes the temptation.
2. **A merge-contract block printed in every step's prompt.** The paper's template says only
   "a dict of your state updates, set keys to null to delete". This implementation also states
   the full merge algebra and the schema's field list on every step. Measured effect: patch
   rejections dropped 18 to 2 with no change in solve rate, so it fixes a mechanical failure
   mode without being the thing that wins tasks.
3. **`patches.jsonl`.** Every patch, accepted or rejected, journaled to disk with its reason.
   The paper discards history outright and accepts that as a stated limitation. This keeps the
   full audit trail at zero prompt-token cost and makes `rollback` a replay instead of a
   snapshot restore.

## Benchmarks

Two passes, about 3,500 episodes, across InterCode CTF, a reconstruction of the paper's
warehouse, and τ-bench retail. Full tables, the dead ends, and the discarded runs are in
`BENCHMARKS.md`. The second pass overturned three conclusions from the first, so read that file
rather than trusting any summary of it, including this one.

| benchmark | control | best skill config | reading |
|---|---|---|---|
| InterCode CTF, 237 episodes/arm | 199/237 | 192/237 (`hybrid`) | Indistinguishable. Paired sign test over 79 tasks: 6 wins, 11 losses, p=0.33. |
| Warehouse T=200, 5 seeds | 0.72 | 0.60 (`hybrid`) | Control ahead on score, skill ahead on score per dollar by ~2x. |
| τ-bench retail, 36 episodes/arm | 27/36 | 13/36 (`state`) | Control ahead. Was reported as 0/36; that measured a driver bypassing the skill. |

Three things worth knowing before you use this:

1. **There is a crossover, and it is measured.** State's prompt grows about 10 characters per
   step against a transcript's 69, and they cross around T=31 steps. In cost per unit of result
   the crossover lands between T=100 and T=200. Below it this skill is more expensive than
   keeping the transcript and buys you nothing.
2. **The token saving is input-side.** Per solved CTF task the skill uses 14-16k input tokens
   against a control's 25.7k, but it emits a state patch every step, and output tokens cost
   about 2.8x input. On a fixed-length job the saving survives into the bill; on a job the agent
   can finish early it does not.
3. **Persistence prompting cuts both ways.** The line telling the model to commit an
   observation before it vanishes moved τ-bench from 0 to 13 solved tasks, and cost the
   warehouse about a quarter of its score. Sparse precious facts: it wins. A stream of
   observations: it loses.

## Development history
## Development history

`CHANGELOG.md` walks every real commit: the RED-phase test that refused to fail, the A/B that
looked like a regression but turned out to measure agent compliance rather than the runtime,
the blob-cache machinery that a higher step cap made redundant, the mutation that broke a
reliable task, and the eventual rename to Talos.

## License

MIT, see `LICENSE`. Use it, fork it, ship it, no attribution required beyond what MIT itself
asks for. If you improve the runtime, or find a benchmark that contradicts what is in
`BENCHMARKS.md`, that is what issues and PRs are for.
