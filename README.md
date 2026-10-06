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

> **Measured result, 2026-10-06: it does not pay for itself.** On the warehouse at T=100 with
> the control in the same harness, three context strategies scored identically on every seed and
> the state arms cost 3.68x and 4.11x. The architecture saves input tokens, and input is about
> 5% of the bill on a reasoning model. Read `BENCHMARKS.md` before adopting this. It is kept
> public as a complete, measured negative result, not as a recommendation.

## Is this for you?

Read `SKILL.md`'s own "When this applies" and "When to keep the transcript instead" sections
first. They are the real answer, and they are now backed by measurement rather than theory.

**Probably not.** On everything measured so far, a plain transcript is as good and cheaper.

The one case this architecture was described for and nothing here has tested: a job where the
transcript would overflow the context window outright, or where the fact you need is thousands
of steps back. If you are there, the alternative is not "keep the transcript", it is "lose the
fact", and that is a different comparison from any run in this repo.

Everywhere else, including long, sequential, single-agent jobs of 100 to 200 steps, keep the
transcript. The threshold this README used to quote (**T ≈ 31 steps**) is the point where the
state prompt becomes smaller than the transcript. It is real, and it is not a cost crossover:
state wins the input column near T=130 and is still $0.39 to $0.64 per episode behind at T=400,
because it produces three times the output and output costs 15 to 25 times input.

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

Four passes, roughly 7,000 episodes, across InterCode CTF, a reconstruction of the paper's
warehouse, and τ-bench retail. Full tables, the dead ends, the retractions and the discarded
runs are in `BENCHMARKS.md`. Each pass overturned something from the one before, so read that
file rather than trusting any summary of it, including this one.

**Pass 4 is the one that matters**, because it is the only warehouse comparison whose control
ran in the same harness. T=100, three seeds:

| arm | score | cost $ | $/point |
|-----|-------|--------|---------|
| react (transcript) | 0.657 | **0.0486** | **0.0739** |
| statex (skill) | 0.657 | 0.1789 | 0.2724 |
| lean-nh (skill, compressed prompt) | 0.657 | 0.1994 | 0.3037 |

Identical score on every individual seed: 0.62, 0.64, 0.71 across all three arms. The benchmark
is not degenerate; always-Wait scores 0.00 and the oracle 1.00, and all three arms sit well
above a memoryless agent's 0.34 to 0.39.

Earlier passes, which still stand on score:

| benchmark | control | best skill config | reading |
|---|---|---|---|
| InterCode CTF, 237 episodes/arm | 199/237 | 192/237 (`hybrid`) | Indistinguishable. Paired sign test over 79 tasks: 6 wins, 11 losses, p=0.33. |
| τ-bench retail, 36 episodes/arm | 27/36 | 13/36 (`state`) | Control ahead. Was reported as 0/36; that measured a driver bypassing the skill. |

Three things worth knowing:

1. **The cost case was arithmetic error.** Passes 1 to 3 reported the skill as 2 to 5x cheaper.
   That came from a price table saying output costs 2x input. It costs 15 to 25x. With the
   provider's own per-call figures the skill loses on cost as well as score.
2. **The input saving is real and small.** The state prompt genuinely does not grow with the
   horizon. Input is about 5% of the bill on a reasoning model, where 99% of the tokens are
   output and nearly all of that is reasoning.
3. **The skill triples deliberation.** 4686 output tokens per step against a transcript's 1527,
   at `reasoning_effort=low`, with zero rejected patches. Handing the model a merge contract, a
   field allowlist, an echo requirement and a four-key response format on every step gives it
   that much more to think about before it answers.

A fourth thing, for anyone building something similar: compressing the prompt was tried
(`--brief-after`) and made it worse. Input fell 24%, output rose 9%, cost rose 11%, and
malformed replies went from 1.3 to 5.0 per episode.

## Development history

`CHANGELOG.md` walks every real commit: the RED-phase test that refused to fail, the A/B that
looked like a regression but turned out to measure agent compliance rather than the runtime,
the blob-cache machinery that a higher step cap made redundant, the mutation that broke a
reliable task, and the eventual rename to Talos.

The measurement history, including which published claims were later retracted and why, is in
`BENCHMARKS.md`. The harness, every episode record and the cost analysis live in the bench
repo alongside this skill.

## License

MIT, see `LICENSE`. Use it, fork it, ship it, no attribution required beyond what MIT itself
asks for. If you improve the runtime, or find a benchmark that contradicts what is in
`BENCHMARKS.md`, that is what issues and PRs are for.
