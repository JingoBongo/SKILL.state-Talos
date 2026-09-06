# Benchmarks

## Read this first: which version was measured

The large benchmark matrices in this document measured the paper's architecture **as first
implemented**, tags `v1-baseline` and `v2-blob-runtime`. Every loss reported below belongs to
that version.

After those runs, a read-only diagnosis pass found two concrete mechanisms behind the losses,
and six fixes were built and tested against them. Those fixes are what became
`v3-persistence-fixes`, the version shipped in this repo. On the tasks that were traced and
diagnosed, they flipped the result:

| traced task | pre-fix (`v2`, 3 reps) | post-fix (`v3`, 1 rep) |
|---|---|---|
| 0 | 1/3 solved (5, 30, 30 steps) | solved, 7 steps |
| 6 | 0/3 solved (30, 30, 30) | solved, 9 steps |
| 96 | 2/3 solved (16, 23, 30) | solved, 5 steps |

The matrices were never re-run against `v3`. The OpenRouter balance ran out during the last
sweep, and post-fix testing was deliberately kept to 2-6 episodes per fix. So: the big numbers
describe the pre-fix version, the fixes demonstrably repair the failures those numbers were
caused by, and nobody has yet measured the fixed version at scale.
Do not cite the tables below as the current skill's score.

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

Two qualifications. It is the **plan field** doing the work, not the architecture alone: plain
`state` is noisy (0.83 / 0.61 / 0.71) and never clearly beats the control. And the T=100 result
is partly a control artifact, described under "the format floor" below.

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

**Two separate things went wrong here, and they are easy to confuse.** The first τ-bench run was
our bug, not a result: the driver rendered Σ through the skill's own prompt template, which
hardcodes `"action"` as a shell-command string, contradicting this benchmark's tool-call
contract. Every state episode emitted an unusable action. That run was discarded. The table
above is the corrected run, and it fails for a different reason: nothing gets written into Σ.

Both are real findings. The action contract belongs to the task, not to the skill, and that is
a design flaw in the runtime worth fixing. But it is not why the corrected run scored zero.

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
- **No loop or stall detection inside the runtime.** Every version of that in this project lived
  in the benchmark drivers. A runtime-level stall notice is written and unit-tested but only
  against synthetic journal entries.
- **The paper's second environment**, a simulated git repo with PRs and CI, was never
  reconstructed.

## Verdict

Against the pre-fix version, the transcript wins on solve rate almost everywhere, and the skill
costs 2-5× less except on τ-bench, where it costs more. The one clean win is the warehouse at
T=100, and part of that is the control degrading rather than the skill improving.

The measured explanation is not that state is a bad idea. It is that state was being tested
mostly below its own crossover point of about 31 steps, and that it had a real defect above it:
a fact observed at one step could not survive to the next unless the model chose to commit it,
and nothing in the prompt told the model that. Fixing that defect flipped every traced failure,
including one out-of-sample task that had never been solved in three attempts.

What nobody has yet is the fixed version measured at matrix scale. Until that exists, the
boundary written into `SKILL.md` under "When to keep the transcript instead" is the operational
summary: long horizons where the past is genuinely unrecoverable, and not short ones where
re-reading a file is cheap.
