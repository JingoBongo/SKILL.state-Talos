# Benchmarks

## Read this first: the fourth pass settled it, and the answer is no

**Pass 4 (2026-10-06)** ran the warehouse at T=100 with the control in the same harness, which
no earlier pass did. Three seeds, three context strategies, nine episodes, no errors, no
rejected patches.

All three arms scored **identically on every seed**. The state arms cost 3.68x and 4.11x.

That run, plus the price correction that came with it, overturns the cost case this file spent
three passes building. The section "Pass 4" below has the numbers. Everything before it is kept
because it is where the numbers came from, but where it disagrees with pass 4, pass 4 is right.

The short version of why: **the architecture optimises input tokens, and input is about 5% of
the bill.** Output costs 15 to 25 times input on this model, 99% of an episode's tokens are
output, and the state arm produces three times as much of it, because a prompt that re-opens
the merge contract every step gives the model three times as much to deliberate about.

## Read this next: two passes, and the second one overturned the first

**Pass 1 (2026-09-03..05)** measured `v1-baseline` and `v2-blob-runtime` and produced most of
the tables below.

**Pass 2 (2026-09-06)** re-ran the same benchmarks against `v3-persistence-fixes` and found
three of pass 1's conclusions to be artefacts of our own harness, not properties of the
architecture. Where the two disagree, pass 2 is right and the text says so at the point of
disagreement.

| pass 1 said | pass 2 measured |
|---|---|
| The skill beats the control on the warehouse at T=100, 0.76 vs 0.67 | The control scores 0.97 there once its malformed replies are retried. The win was the control being scored zero on truncated JSON. |
| A flat state architecture cannot hold a tool-call dialogue: 0/36 on τ-bench | 13/36 once the skill's own prompt is used with a matching action contract. The 0/36 measured a driver that bypassed the skill. |
| Both controls beat every skill variant on CTF | A paired sign test over 79 tasks cannot distinguish them: best arm 6 wins / 10 losses, p=0.45. |

What survives from pass 1: the control is ahead on raw score nearly everywhere, and the token
advantage is real and grows with horizon.

## Pass 4, 2026-10-06: the warehouse at T=100 with a control in the same harness

Every earlier warehouse comparison either ran the control in a different harness or quoted one
across harnesses. This did neither. Run tag `T100-2026-10-06`.

| arm | n | score | input | output | out/step | reasoning | cost $ | $/point | malformed |
|-----|---|-------|-------|--------|----------|-----------|--------|---------|-----------|
| react | 3 | 0.657 | 140479 | 152745 | 1527 | 99.3% | **0.0486** | **0.0739** | 1.3 |
| statex | 3 | 0.657 | 161586 | 468599 | 4686 | 91.2% | 0.1789 | 0.2724 | 1.3 |
| lean-nh | 3 | 0.657 | 139558 | 509065 | 5091 | 82.2% | 0.1994 | 0.3037 | 5.0 |

Paired by seed:

| seed | react | statex | lean-nh |
|------|-------|--------|---------|
| 42 | 0.62 | 0.62 | 0.62 |
| 43 | 0.64 | 0.64 | 0.64 |
| 44 | 0.71 | 0.71 | 0.71 |

Identical to two decimals in every cell. There is no quality difference here to trade cost
against.

The benchmark still discriminates: always-Wait scores 0.00, an agent that answers only from the
visible observation scores 0.34 to 0.39, the oracle scores 1.00. All three arms sit well above
the memoryless floor. They use memory. They do not differ in how well.

### The price table was wrong, and that is what the old cost case rested on

`config.toml` priced the model at $0.14 in / $0.28 out per 1M, so output looked like 2x input.
The provider's own per-call figure says output is 15 to 25x input. There is also no single
price: OpenRouter serves one model id through several upstream providers and picks per request.
Two calls with the same token counts were billed $0.000047 and $0.000400, an 8.5x spread.
Episode totals average over 100 calls and are stable to within 3%, so episode comparisons hold;
per-call ones do not.

Every cost figure in the passes above was computed from that table. The ones that depended on
the input/output ratio, which is all of them, are wrong.

### The input saving does not exist at T=100

The transcript arm averages 1405 input tokens per step. The flat state prompt is about 1616.
**The state arm's input is 15% larger than the control's.**

The flat prompt is not small: the merge contract, the response format and the task spec come to
3458 bytes before Sigma is added. And bytes are not tokens. Prose tokenizes at roughly 4 bytes
per token; the JSON that stays (`item_77`, `shelf_391`) at roughly 1.7. The 4x saving reported
in earlier passes was measured in prompt bytes per step. In tokens per episode it is negative.

### Crossing the crossover does not rescue it

Transcript input grows quadratically in T and state input linearly, so state does win the input
column, near T=130 on this task.

| | T=200 | T=400 |
|---|---|---|
| input the state arm saves | 238,745 tokens | 1,601,325 tokens |
| output the state arm adds | 631,708 tokens | 1,263,416 tokens |
| net, cheapest route observed | **+$0.199** | **+$0.385** |
| net, dearest route observed | **+$0.330** | **+$0.636** |

The input column is won and the episode still costs more. The gap widens with T.

### Where the output actually goes

99.3% of the control's output tokens are reasoning. The state arms are not writing longer
patches, they are deliberating more: 4686 and 5091 tokens per step against 1527, at
`reasoning_effort=low`, with zero rejected patches to re-think. The prompt is the thing being
deliberated about.

### Fixes tried in this pass

| change | effect |
|---|---|
| `ids` subcommand | Fixed a drift bug that rejected every patch in the newer harness |
| scalar `$append` accepted | Removed 94% of every rejection ever recorded on a long horizon (116 of 124) |
| `--no-state-hash` | 13% less output at T=25; indistinguishable at T=100 |
| `--brief-after N` | **Failed.** Input down 24%, output up 9%, cost up 11%, malformed replies up from 1.3 to 5.0 |
| `$append` example built from the schema | The hardcoded example named `cmd_summary`, a `str` in the warehouse schema, so the prompt demonstrated the one patch the runtime rejects. Models copied it. Fixed after the run, so not in these numbers |

`--brief-after` was built specifically to test whether the contract block was what the model
kept re-reasoning about. It compressed the prompt 34% in bytes and 6% in tokens, and the model
produced more output rather than less, while holding the response format less firmly.

### What pass 4 does not establish

One task, one model (deepseek-v4-flash-0731 at `reasoning_effort=low`), three seeds, one
horizon. Zero disagreeing pairs, so no sign test applies at all. The build measured still
carried the broken `$append` example, which cost `statex` some corrections; fixing it narrows
the gap and does not close a 3.68x one.

The two largest findings, that output dominates the bill and that the state prompt triples
deliberation, are properties of the model and the price sheet rather than of the warehouse. They
have not been checked on another model.

## Setup

Model everywhere: `deepseek/deepseek-v4-flash-0731`, temperature 0, about $0.0016 per episode.
`haiku-4.5` and `sonnet-4.5` appear only in the model sweep. Roughly 2,300 episodes across the
runs that count, plus about 1,800 more in runs that were discarded (listed at the end).

The benchmark harness is a separate repository: driver, patched Dockerfile, the warehouse
simulator, and one JSON line per episode for every run. It is not vendored here.

Fidelity notes, because they bound what these numbers mean:

- InterCode CTF is used as published. `ic_ctf.json` is byte-identical to upstream (sha256
  `7d5c2972…`, 32,766 bytes). One file was added to the clone, `ctf-fixed.Dockerfile`, because
  the upstream recipe no longer builds on current `ubuntu:latest`.
- That Dockerfile also installs `binutils`, `file` and `xxd`, which upstream does not. Every
  arm shares the image, so it cannot bias arm-vs-arm comparison, but our absolute numbers are
  not directly comparable to the paper's.
- The warehouse is a reconstruction from the paper's Appendix B.1 and Algorithm 2. No code was
  released. 500 shelves, seeded events, deterministic scoring, 15 tests; a perfect agent scores
  1.00 at every horizon and an idle agent 0.00.

## The arms

Two of them contain no skill at all. Those are the controls.

| arm | what the model sees each step | skill? |
|---|---|---|
| `react` | the whole transcript, growing | no, control |
| `rlist` | transcript plus a manifest it can request files from | no, control |
| `state` | Σ only, rendered by the driver's own short prompt | yes |
| `statex` | Σ only, rendered by the skill's own prompt template | yes, as shipped |
| `splan` | `statex` plus a mutable `plan` list in the schema | yes |
| `push` | Σ plus runtime-cached file bodies | yes |
| `spull` | Σ plus a manifest, bodies handed over on request then dropped | yes |
| `both` | Σ plus manifest plus cache | yes |
| `bplan` | `splan` plus auto-cache | yes |
| *(modifier)* loop guard | a verbatim repeat is answered from memory, not re-run | orthogonal |

On the warehouse there are no files, so manifest and cache arms collapse into plain state. Only
transcript, state and plan apply there.

## The paper's rendering, ours, and no skill at all

`state` and `statex` differ in exactly one thing: how Σ is rendered into the prompt. `state`
uses the driver's own minimal rendering, which is the closest thing in this project to the
paper's bare A.4 template ("a dict of your state updates, set keys to null to delete").
`statex` uses this skill's prompt, which additionally prints the full merge algebra and the
schema's field list every step. That is deviation #2 in `references/paper-findings.md`.

12 tasks × 3 reps, cap 15:

| arm | solved/36 | rejected patches |
|---|---|---|
| `rlist`, no skill | 28 | 0 |
| `react`, no skill | 25 | 0 |
| `statex`, our rendering | 19 | **2** |
| `state`, minimal rendering | 19 | **18** |

Our deviation does what it was designed to do and nothing more. Patch rejections drop by a
factor of nine, and solve rate does not move by a single task: 3 wins, 3 losses, 6 ties at task
level. So the paper's dominant failure mode on weak models is real and this fixes it, but it
was never what stood between the skill and the control. The gap to the control is architectural,
not a formatting artifact.

Worth stating plainly: the other two deviations were never A/B tested. `$append` and the
`patches.jsonl` journal are in every arm, so nothing here measures them.

## A: InterCode CTF, 12 tasks × 3 reps, step cap 15 vs 30

| arm | solved/36 @ cap30 | @ cap15 | input tokens @ cap30 |
|---|---|---|---|
| `react` (control) | 29 | 25 | 2,313k |
| `rlist` (control) | 29 | 28 | 1,569k |
| `state` | 26 | 19 | 489k |
| `splan` | 25 | 22 | 398k |
| `splan` + loop guard | 24 | – | 384k |
| `both` | 22 | 21 | 620k |
| `push` | 22 | 20 | 510k |
| `spull` | 21 | 21 | 638k |

Worst spread between reps is 2 tasks, so differences at or under that are noise.

Raising the cap from 15 to 30 halved the deficit, 6 tasks down to 3. State gained seven solves
from the extra steps; the controls gained one and four. The earlier reasoning that "almost
nothing solves after step 12, so more steps will not help" was a censoring error: that
distribution was truncated by the very cap under test.

**The file-blob machinery is a measured dead end.** At cap 15 the cache and manifest arms beat
plain state, 20-22 against 19. At cap 30 plain state reaches 26 while they sit at 21-22, now
worse than the base they were built to fix, and more expensive (638k against 489k). More steps
cured the same failure better than our mechanisms did.

**The loop guard does not help.** 24/36 against 25/36 without it, inside noise, despite firing
126 times. Loops are real and frequent; suppressing them relocates the confusion rather than
resolving it.

The token advantage grew from 2.4× to 4.7× as episodes lengthened.

## The crossover, which explains most of the CTF result

State's prompt grows about 10 characters per step. The transcript's grows about 69. They cross
at **T ≈ 31 steps**, measured on the warehouse.

Below thirty steps the skill is simply more expensive than keeping the transcript. Every solved
CTF episode took 4-6 steps. The entire CTF benchmark sits on the wrong side of that line, which
is likely the single biggest reason the skill looks bad there. CTF is a short-horizon benchmark
being used to evaluate a long-horizon mechanism.

## C: the paper's warehouse

The benchmark designed for this architecture: there is no file to re-read, so a transcript buys
no lookup advantage.

| arm | T=25 | T=50 | T=100 |
|---|---|---|---|
| `react` (control) | 0.89 | 0.97 | 0.67 |
| `state` | 0.83 | 0.61 | 0.71 |
| `splan` | 0.80 | 0.79 | **0.76** |

This is the shape the paper predicts, and the only place we saw it. The transcript holds through
T=50 and then falls away, 0.97 to 0.67. The plan arm runs nearly flat, 0.80 → 0.79 → 0.76, and
passes it around T=100. Tokens at T=100: 140k for the transcript against 51k for state, 2.7×.

**This result did not survive pass 2. It was a control artefact, and the retraction is
measured, not argued.** The control here had no retry on a malformed reply, and an unparseable
reply scores a hard zero. Re-run with one retry, paired on the same seeds, the control goes
0.67 to 0.97 at T=100 and its malformed count drops from 96 to 8. The skill did not beat a
transcript at T=100; a transcript was being scored on truncated JSON. Full pass-2 numbers below.

T=10 discriminates nothing, both arms score 1.00. T=200 was discarded and re-run after 29% of
calls failed at 55 concurrent drivers.

## D: full InterCode CTF, all 79 self-contained tasks, cap 30, 3 reps

237 episodes per arm, 1,659 episodes total, zero errors.

| arm | solved/237 | per rep | input tokens | $ | rejected patches |
|---|---|---|---|---|---|
| `rlist` (control) | **199** | 65, 67, 67 | 5.11M | 0.633 | 0 |
| `react` (control) | 197 | 66, 67, 64 | 4.86M | 0.638 | 0 |
| `push` | 191 | 64, 61, 66 | 1.95M | 0.421 | 112 |
| `statex` | 186 | 63, 61, 62 | 2.03M | 0.463 | 27 |
| `splan` | 183 | 62, 60, 61 | 2.13M | 0.495 | 14 |
| `both` | 180 | 59, 62, 59 | 2.29M | 0.490 | 54 |
| `spull` | 179 | 62, 59, 58 | 2.22M | 0.463 | 76 |

Same shape as the 12-task run at five times the sample: both controls ahead of every skill arm,
at roughly 2.3-2.6× the tokens.

Broken down by InterCode's own task tags, control minus best skill arm, in percentage points:

| tag | gap |
|---|---|
| Reverse Engineering | +0.8 (parity, n=390/375) |
| General Skills | +5.7 |
| Cryptography | +10.2 |
| Forensics | +11.2 |
| Binary Exploitation | +26.7 (n=15, noisy) |

The skill is at parity on read-once, reason-once tasks. It loses progressively more as tasks
require correlating two observations across steps: a value found at step N and needed at step
N+5. That is the failure this architecture has to solve, and pre-fix it did not.

## E: τ-bench retail

| arm | solved/36 | mean reward | turns | tool calls | replies to user | episodes that ended |
|---|---|---|---|---|---|---|
| `react` (control) | **27** | 0.750 | 786 | 247 | 191 (44%) | 27/36 |
| `state` | **0** | 0.000 | 1,080 | 947 | 70 (7%) | 0/36 |
| `splan` | 1 | 0.028 | 1,060 | 874 | 108 (11%) | 1/36 |

1,080 turns is exactly 36 × 30: not one state episode ever terminated. The agent calls tools 947
times and speaks to the customer 70 times, so the simulated user is never satisfied and never
says STOP. Note that here the skill costs **more**, not less: 947 tool calls against the
control's 247.

Mean final Σ is 129 characters against an empty schema of 56. Across thirty turns the model
wrote almost nothing into state, so every tool result vanished the moment it arrived and the
same tool got called again. Same failure as CTF task 13, but the lost artifact is a tool result
rather than a file body.

**Everything above is now known to be an artefact.** Three runs happened here, not two.

The first was discarded because the driver rendered Σ through the skill's template, whose
hardcoded shell-string action contradicted the tool-call contract. The fix applied at the time
was to stop using the skill's template at all and render Σ by hand. The table above is that
second run. It means **the skill's own prompt was never exercised on this benchmark**: no
urgency line, no JSON-first ordering, no field-scoped `$append`. The 0/36 measured a
hand-rolled substitute.

Pass 2 fixed the actual cause instead. `skillstate.py prompt --action-contract` lets the caller
state the action shape, so the skill's template can be used with tool calls. Same driver, same
36 episodes:

| rendering of Σ | solved | reward | agent ended it | tool calls | replies to customer | final Σ |
|---|---|---|---|---|---|---|
| control, transcript | **27/36** | 0.750 | 27 | 247 | 191 | n/a |
| hand-rolled body (the table above) | 0/36 | 0.000 | 1 | 949 | 74 | 140 B |
| **the skill's own template** | **13/36** | **0.361** | 19 | 418 | 264 | **4,048 B** |

The whole behavioural signature inverts. The agent stops hammering tools, starts talking to the
customer, and writes 29x more into state. The claim that a flat state architecture cannot hold
a tool-call dialogue is withdrawn: it can, at half the control's rate, and the zero was ours.

## B: model sweep, 12 tasks, cap 30

| model | control | `statex` | `splan` | n |
|---|---|---|---|---|
| haiku-4.5 | 7/11 (63.6%)† | 7/12 (58.3%) | 6/12 (50.0%) | 12, 1 rep |
| deepseek-v4-flash | 29/36 (80.6%) | 26/36 (72.2%) | 25/36 (69.4%) | 36, 3 reps |
| sonnet-4.5 | 9/12 (75.0%) | 6/12 (50.0%) | 5/12 (41.7%) | 12, 1 rep |

† Task 55 is excluded from haiku's control count: 13 of 30 calls on that episode returned with
the `errors` field set, exactly when the OpenRouter balance hit its ceiling mid-run. Included
raw it reads 7/12. Scoring a model on malformed replies produced by a dead balance is not a
measurement.

Gap of control minus best skill arm: haiku −5.3pp, deepseek −8.4pp, sonnet −25.0pp. The
prediction was that a stronger model pays less of the structured-output tax, so the gap should
shrink or flip. It is widest on sonnet, the strongest model tested. Two of the three rows are
n=12 at 1 rep, so treat the exact percentages as noisy, but the direction held across two
passes.

The skill arm's win/loss pattern is nearly model-independent: tasks 0 and 6 are solved by the
control on every model and failed by the skill on every model. Structural, not a model-strength
artifact.


## Pass 2, 2026-09-06: v3 measured at scale

About 1,200 episodes, $6.94. Every arm below ran against `v3-persistence-fixes` plus the
`$append` prompt fix found during this pass.

### CTF, 79 tasks x 3 reps, cap 30, 237 episodes per arm

| arm | solved/237 | steps per solved | input tokens per solved | $ per solved |
|---|---|---|---|---|
| control `rlist` | **199** | 6.2 | 25,695 | 0.00318 |
| v3 `hybrid` | 192 | 8.3 | 16,144 | 0.00316 |
| v3 `appendfix` | 191 | 10.0 | 13,977 | 0.00338 |
| v3 `window` | 189 | 11.2 | 13,451 | 0.00370 |
| v3 `shipped` | 188 | 10.4 | 13,414 | 0.00325 |
| v2 (pass 1) | 186 | 12.4 | 10,889 | 0.00249 |

A paired sign test over the 79 tasks cannot separate any arm from the control. The skill and
the control agree on 59-63 tasks; the argument is over 16-20 discordant ones, and the control's
edge there is within chance: `appendfix` 6 wins / 10 losses p=0.45, `hybrid` 6/11 p=0.33, even
pass 1's v2 is 5/13 p=0.10. Pass 1's "both controls beat every skill variant" was never a
supported claim. Point estimates do favour the control in all five arms, so the direction is
consistent; the sample just cannot resolve a gap this small.

**The dollar advantage is input-side only.** Per solved task the skill uses 14-16k input tokens
against the control's 25.7k. But it must emit a state patch every step, so its output/input
ratio is 0.72-1.17 against the control's 0.33, and output costs 2.8x input. Net cost per solved
task is a wash. The paper's headline token saving is measured on context, not on a bill.

By InterCode tag, the skill is ahead on Reverse Engineering (82-83% vs 78%), level on General
Skills, and behind on Forensics (70-79% vs 85%) and Cryptography (62% vs 73%). It loses exactly
where a task needs two observations correlated across steps.

### The `$append` bug, found by running

The dominant rejection at scale was the model emitting a bare top-level `{"$append": [...]}`
instead of `{"field": {"$append": [...]}}`. The prompt caused it: the semantics block listed the
op with no owning field. Rejections 137 to 74 with everything else held constant. Solve rate
189 to 191, inside noise. Pass 1's 3-task validation could not have seen this.

### Warehouse, T=50/100/200, 5 seeds, retry active for every arm

| T | control | `plan` | `hybrid` | $/score control | $/score hybrid |
|---|---|---|---|---|---|
| 50 | **0.99** | 0.67 | 0.90 | 0.0090 | 0.0154 |
| 100 | **0.97** | 0.62 | 0.78 | 0.0371 | 0.0489 |
| 200 | **0.72** | 0.38 | 0.60 | 0.2171 | **0.1183** |

The control leads on score at every horizon. Cost-adjusted, the crossover is real and sits
between T=100 and T=200: at T=200 `hybrid` returns roughly twice the score per dollar.

**v3 regressed this benchmark.** Paired on seeds 42-44, the `plan` arm went 0.79 to 0.53 at
T=50, 0.76 to 0.51 at T=100, 0.48 to 0.46 at T=200. The retry can only add score, so the v3
changes cost more than it returned. Σ grew 148 to 211, 232 to 316, 429 to 511 bytes, and patch
rejections appeared where there had been none.

### The finding that ties the three benchmarks together

**Persistence prompting is domain-sensitive, and its sign flips.**

Where facts are sparse and expensive to re-derive, telling the model "commit this now or lose
it" is worth a great deal: on τ-bench it moved final Σ from 140 to 4,048 bytes and solved tasks
from 0 to 13. Where observations arrive as a stream, the same instruction makes the model
commit a firehose into a structure that sits in the prompt at every step, and it costs more
than it saves: on the warehouse it cost about a quarter of the score.

The same line, opposite signs, depending on whether the domain's observations are rare facts or
a feed. Nothing in the paper predicts this, and it is the practical rule this project ended up
with: pick the schema for the domain, and pick the prompting for how the domain emits facts.


## Third pass, 2026-09-07: two fixes attempted, both dropped

The second pass left the skill statistically level with the control on CTF and behind on the
other two benchmarks. This pass took the two largest remaining failure modes, built a fix for
each, and measured both out of sample. Neither survived. Recording them because a negative
result that cost $1.35 is cheaper than the next person rediscovering it.

### Failure mode 1: the value survives the step boundary, the model corrupts it on the way out

Traced in the episode stores, not inferred. On task 97 Σ held
`picoCTF{r0tat1on_d3crypt3d_a4b7d759}` correctly and the submitted answer was
`...c3rypt3d...`. On task 91 the model derived every character right in `cmd_summary`
(`322=0`, `285=0`) and then wrote `ROUND` instead of `R0UND`. On 82 `9C174346` came back as
`9c174346`; on 72 the middle of a long hex string vanished.

A transcript never has this failure, because the original text is still sitting there to copy
from. This is a real and previously unnamed cost of carrying state instead of history.

**The fix:** one prompt rule telling the model to copy exact values out of the state block
character for character rather than recalling or reconstructing them.

| | on the 8 tasks that showed the failure | on all 79 tasks, 237 episodes |
|---|---|---|
| baseline `hybrid` | 10/24 | 192/237, near-miss 15, no answer 18, $0.606 |
| with the rule | **16/24** | **190/237**, near-miss 11, no answer 25, $0.687 |

It does exactly what it was written to do: near-misses fall 15 to 11. And it buys nothing.
Solved does not move, episodes ending with no answer at all rise 18 to 25, and spend rises 13%.
Telling a model not to reconstruct a value it is unsure of trades a wrong answer for no answer.
Both score zero; only one of them wastes the remaining steps. Reverted.

A longer version of the rule, which also said to compute exact values with a command rather
than assembling them in reasoning, scored the same 16/24 on the small set but regressed the
skill's single best task (55: 3/3 to 1/3) and cost an extra step per episode. Dropped earlier.

### Failure mode 2: the `cat` loop, and it is not about memory

25 episodes end with no answer, every one of them at the 30-step cap. Inside them **25% of all
actions are verbatim repeats**: `cat message.txt` ten times, `cat chall_2.S` nine times.

`SKILL.md` predicts this: a schema holding only a file's *path* turns the run into a `cat` loop.
The CTF schema stores `active_files` as paths, so that reading is available. It is also wrong.
The loops happen at steps 2 through 11, and the `hybrid` arm carries the full transcript until
step 12, so the model had every previous `cat` output in front of it and re-read the file
anyway. **The content was never missing.** Compare the control on task 82: it read the file
once and then ran `python3 -c "n=3736234946; r=(3*n)%(2**32); print(hex(r))"`. The skill arm read
it nine times and computed nothing.

**The fix attempted:** give the schema somewhere to park file contents (`ctf-blob.json`, a
`blobs` dict with a 16 KB budget), tested both model-filled and runtime-auto-cached, five reps
on the four clearest loopers, identical skill, schema the only difference.

| | plain `ctf.json` | `ctf-blob.json` |
|---|---|---|
| solved | 5/18 | 4/19 |
| mean steps | 18.8 | 18.6 |
| task 93 | 2/5 | 1/5 |

Nothing. An early 3/3 on task 93 looked like a win and was noise; five reps put it at 1/5. The
runtime auto-cache variant filled the field correctly (`message.txt`, 54 bytes) and still
scored 1/3 at 22 steps, which independently confirms the pass-1 finding that the blob machinery
is a dead end, and sharpens it: the dead end is the caching, not the absence of a field.

### What this pass establishes

The transcription failure is real, diagnosable, and fixable in the narrow sense that the rule
removes it. It is not worth fixing, because what it converts into is a different way of scoring
zero.

The `cat` loop is not a memory failure and not a schema failure. The model has the content and
does not know what to do with it. Nothing in the state architecture addresses that, and neither
the loop guard (pass 1, no effect over 126 firings) nor a content field (this pass) changes it.
It is the largest remaining gap and it is not obviously the skill's problem to solve.

## Why it lost: two failure surfaces

Mined from the existing results, read-only, no new runs.

**1. The content-persistence gap.** Traced on task 96 against its matched control episode. The
state arm burns 27 steps re-discovering the same fact six times (`grep cultiris` → line 378,
forgotten, re-grepped) because `cmd_summary`, the only freeform slot in the CTF schema, was a
scalar under replace-not-merge semantics. A fact found at one step and needed at the next had
nowhere to persist unless committed to a structured field immediately. The control solves the
same task in 7 clean steps because the transcript keeps it visible for free. Patch rejections
were near zero here, so this is a retrieval-loop cost, not corruption. This is the paper's own
limitation #2 happening in practice.

The schema already had the right shape available. `discovered_flags` and `tested_hypotheses` are
append-able lists, exactly what "cultiris → line 378" needs. The prompt explained the merge
mechanics in full but never told the model why it should bother, and never said that the current
observation would not be shown again.

**2. A JSON format-reliability floor, which is mostly not the skill's problem.** The warehouse
T=100 result looked like the skill improving. It is not: Σ size and patch rejections stay flat
and near-zero across every horizon. What changes is the control's malformed-reply rate, which
explodes with horizon: 10.7% → 2.7% → 32.0% → 48.5% at T=25/50/100/200. A longer transcript
makes the model reason longer before emitting JSON, blowing the completion budget before the
JSON block, and the harness scores an unparseable reply as a hard zero with no retry. The
skill's own malformed rate stays a flat 7-10% regardless of horizon.

So there is a generic format floor that punishes both arms, and the control's version of it gets
much worse at long horizon. Part of the warehouse "win" is the control degrading, not the skill
improving.

## The fixes, in the order they were tried

Each verified with a 2-6 episode test before moving to the next, deliberately cheap. deepseek,
cap 30, tasks 0/6/96 unless noted.

| # | fix | result |
|---|---|---|
| 1 | Urgency line in the prompt, v1 | Task 96: 27-step loop → 7 steps. Task 0: unchanged, still 30/30. |
| 1b | Urgency line, v2, directive rather than descriptive | Task 0 → solved, 11 steps. Task 96 still solved, 13 steps. |
| 2 | JSON block before the discardable reasoning | No CTF episode exercised it; validated on the warehouse instead. |
| 3 | `check_limits` per-field atomicity | `push` rejections 1-2 per rep → 0. Did not fix `push`'s task 96. |
| 4 | Driver-managed 2-observation window | Mixed. Task 96 13 → 5 steps, task 0 11 → 17, **task 6 regressed from solved to 30/30.** |
| 5 | `cmd_summary` from scalar to byte-capped list | Recovered the regression and improved everything: 0 in 7 steps, 6 in 9, 96 in 5. Best of the session. |
| 6 | One local retry on a malformed reply (harness, not skill) | Warehouse T=100 plan arm 0.76 → **0.98**, 10 of 12 would-be-malformed replies recovered. |

Fix 4 is the clearest example of a mutation that looked good and was not. It helped two tasks
and broke a third that had been reliable all session. It was kept only because fix 5 recovered
the regression, and the two together beat either alone.

Fix 6 needs its caveat stated: on that single seed the control scored 1.00 with zero malformed
replies, which cannot be compared to its 0.67 three-seed baseline. The skill's 0.76 → 0.98 is
the real signal; the control's 1.00 is plausibly just that seed. Token gap on the same seed:
80.7k for the skill against 155.7k for the control.

**A combination that did not work.** `bplan`, plan field plus auto-cache, built on top of every
fix above, solved task 0 in 12 steps and task 96 in 6, but failed task 6 at 30/30, which plain
`statex` with the same fixes solved in 9. Blob-caching's extra complexity had already been seen
derailing `push` on task 96. Mechanically combining two winning mechanisms does not inherit both
wins.

## Out-of-sample check

Fixes 1-5 were built and iterated against tasks 0, 6 and 96, so they cannot be validated on
those same tasks. Two fresh tasks were run post-fix, against their own pre-fix baselines:

| task | pre-fix `statex` (3 reps) | post-fix `statex` (1 rep) | control |
|---|---|---|---|
| 70 | 0/3, all capped at 30 | **solved, 21 steps** | 3/3 (11, 11, 14) |
| 86 | 1/3 (6, 30, 30) | unsolved, 30 | 3/3 (10, 11, 7) |

One clean flip from never-solved to solved, one no-change on a task that was already unstable.
n=2. This is a signal, not a result.

## Discarded and invalidated runs

Listed because leaving them out would make the methodology look cleaner than it was.

| run | why |
|---|---|
| D at cap 15, 575/1,659 episodes | Killed once A proved the cap biases the outcome. |
| D at cap 30, 1,078/1,659 episodes | 60.7% of API calls failing. Diagnosed as concurrency, actually an empty OpenRouter balance returning HTTP 402. A dead balance looks exactly like a stupid model. |
| τ-bench first pass, ~105 episodes | The action-contract bug described above. Our bug, not a result. |
| Warehouse T=200, first pass | 29% call failures at 55 concurrent drivers. One episode failed every call, recorded 0.00, and was averaged in before anyone looked at the `errors` field. |

Two process lessons came out of these, both cheap and both learned late: check the endpoint with
one curl before theorising about failures, and check the `errors` field before reading any score.

A separate bug killed 7 of 84 shards mid-run in the final D pass: `docker exec` output from
binary CTF files can contain a raw `\x00`, which raises `ValueError: embedded null byte` when
passed as a subprocess argument to the skill's `prompt` CLI. Fixed by stripping nulls from
captured output. All 7 shards resumed for their missing task ids, no data lost.

## What is still open

- **The matrices were never re-run against the fixed version.** This is the single largest gap.
  Everything after "The fixes" rests on samples of 2-6 episodes.
- **Fixes 1-5 were iterated against tasks 0, 6 and 96,** so those three cannot validate them.
  The out-of-sample evidence is two tasks.
- **Eviction has never fired**, across 288+ episodes. The LRU path is unit-tested and unexercised
  in the wild; the byte ceiling was never reached.
- **Tasks 12 and 55 are unsolved by every arm.** The slate has a floor.
- **No native tool-call support.** The action contract is hardcoded as a shell-command string in
  a domain-agnostic template. `cmd_prompt` already takes `--instructions`, so the same mechanism
  could carry the action contract, defaulting to today's behaviour. Not done.
- **Stall detection is now in the runtime, and unmeasured.** Until recently every loop detector
  in this project lived in the benchmark drivers, so nothing in the skill itself noticed a run
  going in circles. There is now an advisory stall notice in the step prompt, but it is
  validated against synthetic journal entries only. No live episode has reacted to it, so its
  effect on solve rate is unknown. The one loop mechanism that *was* measured, the driver's
  loop guard, did not help.
- **The paper's second environment**, a simulated git repo with PRs and CI, was never
  reconstructed.

## Verdict

**The skill does not pay for itself.** On the one benchmark run with a control in the same
harness, it scored identically to a plain transcript on every seed and cost 3.68x. The variant
built to fix that cost 4.11x.

The architectural claim is true and reproduces cleanly: a state prompt does not grow with the
horizon, 505 bytes per step at T=25 against 567 at T=200, where a transcript goes from 565 to
2271. It is worth about 5% of the bill, because input is about 5% of the bill.

The mechanism that costs money is deliberation per step, and this architecture increases it.
Every step hands the model a merge contract, a field allowlist, an echo requirement and a
four-key response format, and the model reasons about all of it before answering. Three times
the output tokens, for the same answers.

Three passes of this file argued that the skill lost on score but won on cost. The cost half
was computed from a price table that had the input/output ratio wrong by an order of magnitude.
With the provider's own figures it loses on both.

What would change this conclusion, in the order worth trying:

- **A model where reasoning is not the bill.** Everything here rests on 99% of output being
  reasoning tokens. On a non-reasoning model the arithmetic is different and untested.
- **A task whose state genuinely cannot be carried in a transcript.** The warehouse can be. A
  job where the relevant fact is thousands of steps back, or where the transcript would exceed
  the context window outright, is the case this architecture was described for and the case
  nothing here has measured.
- **A prompt that does not re-teach itself.** `--brief-after` was the obvious attempt and it
  failed. Something that removes the contract from the step prompt entirely, rather than
  compressing it, has not been tried.

Until one of those exists, keep the transcript.
