# Benchmarks

Every number below is from a run we executed ourselves, on our own hardware, against a
skill/harness pair we could inspect line by line. None of it should be read next to the paper's
own table as if it were a replication at the same scale — see the caveats at the end before
citing any of this.

**Budget and models actually used:** ~2,500+ episodes total across four rounds, roughly
$15–20 in API spend, mostly on `deepseek-v4-flash-0731` (cheap enough to run matrices), with
`haiku-4.5` and `sonnet-4.5` used for smaller spot checks. The paper's headline number
(43.2% → 54.2% pass@1) was measured on Gemini-3-Flash across 100 CTF tasks with 5 seeds. We
never had access to that model or that sample size — direct comparison is not appropriate.

## Round 0 — the control that refused to fail

Before writing a line of `SKILL.md`, `writing-skills`' Iron Law requires a no-guidance baseline
that actually exhibits the failure the skill is supposed to fix. First attempt: a Kubernetes
manifest audit, 4 haiku reps, no skill.

All 4 reps flagged the injected `ZZ_POLICY_UPDATE.md` as a prompt-injection attempt and refused
to act on it, then produced the 100%-correct v1 answer anyway. Two design errors, not one:

- The fixture tested injection resistance, not state discipline — a file claiming
  "SUPERSEDES R1" reads as an attack and *should* be refused; that's not the kind of state
  drift a long task actually suffers from.
- Too small: 12 files, 2–5 tool calls, ~62k tokens. The paper's effects start at T≥50 *steps*,
  and item count is not step count — a mistake we made twice more before catching it for good.

No failure, no guidance to write. RED phase redone with the drift made intrinsic instead of
injected (see next round).

## Round 1 — first A/B, and the wrong conclusion for the right reason

Second fixture: 60 service manifests, a 6-rule policy where rule 6 requires walking a
dependency graph transitively. 3 control / 3 treatment, haiku.

| arm | services flagged | exact-match | TP | FP | FN | F1 |
|---|---|---|---|---|---|---|
| ctl1 | 57 | 57/57 | 97 | 0 | 0 | 1.00 |
| ctl2 | 57 | 57/57 | 97 | 0 | 0 | 1.00 |
| ctl3 | 57 | 57/57 | 97 | 0 | 0 | 1.00 |
| trt1 | 57 | 57/57 | 97 | 0 | 0 | 1.00 |
| trt2 | 57 | 27/57 | 67 | 0 | 30 | **0.82** |
| trt3 | 57 | 57/57 | 97 | 0 | 0 | 1.00 |

Control 3-for-3, treatment 2-for-3 — looked like the skill made things worse. It didn't measure
that at all: checking the state stores showed trt1/trt2 sent **one patch total** and trt3 sent
**zero**. The loop needed roughly 60. Reps were told to use the skill and drove the job exactly
like the control instead, dumping state once (or never). trt2's 30 misses were all rule 6,
every one — single-pass reasoning on a problem that needs iteration, same as the control would
have produced without the skill.

**The real finding wasn't capability, it was compliance.** A skill saying "drive this loop"
doesn't make an agent actually drive it — that's a discipline problem, not an architecture
problem, and it's why `SKILL.md` needed rewriting as an enforceable recipe rather than an
explanation before any accuracy claim could mean anything.

## Round 2 — InterCode CTF pilot (haiku, 12 tasks, react vs state)

First benchmark run against a real published environment: [InterCode
CTF](https://github.com/princeton-nlp/intercode) (picoCTF challenges in a Docker container),
their tasks and container, not a reconstruction. `claude -p --model haiku --tools ""` per step,
byte-identical system prompt across arms, 15-step cap, 4000-char observation cap. The only
difference: **react** carries the full appended transcript; **state** carries `Σ` only, via
`skillstate.py` unchanged, on the paper's published 5-field CTF schema.

| task | category | react solved | react steps | react chars | state solved | state steps | state chars |
|---|---|---|---|---|---|---|---|
| 0 | Reverse Eng | ✗ | 15 | 84,925 | ✗ | 15 | 38,947 |
| 2 | Forensics | ✓ | 6 | 7,727 | ✓ | 3 | 5,645 |
| 3 | Forensics | ✗ | 15 | 112,080 | **✓** | 6 | 17,045 |
| 4 | General | ✓ | 3 | 2,799 | ✓ | 3 | 5,392 |
| 5 | Crypto | ✓ | 2 | 1,712 | ✓ | 1 | 1,785 |
| 6 | General | ✗ | 15 | 70,849 | ✗ | 15 | 36,369 |
| 8 | Forensics | ✗ | 15 | 62,039 | **✓** | 8 | 18,404 |
| 10 | Reverse Eng | ✓ | 4 | 4,958 | ✓ | 6 | 11,465 |
| 12 | Crypto | ✗ | 15 | 91,448 | ✗ | 15 | 33,319 |
| 13 | Reverse Eng | **✓** | 9 | 80,003 | ✗ | 15 | 68,366 |
| 15 | Reverse Eng | **✓** | 10 | 25,379 | ✗ | 15 | 37,230 |
| 55 | Crypto | ✗ | 15 | 139,157 | ✗ | 15 | 39,736 |

| | react | state |
|---|---|---|
| solved | 6/12 | 6/12 |
| total prompt chars | 683,076 | **313,703** |
| mean avg prompt | 4,426 | **2,454** |
| wall clock | 44.7 min | **31.3 min** |

**What replicated:** token cost, 2.18× cheaper (paper claimed 60% at 100-task scale, close
agreement). Growth curve — capped 15-step episodes, react goes 809→13,120 chars (16.2×, 879
chars/step), state goes 1,761→3,782 (2.1×, 144 chars/step) — six times shallower, O(T²) vs
O(T) on real tasks. 30% less wall clock as a direct consequence. Zero patch rejections in 12
episodes — the paper's 68% premature-overwrite failure mode never appeared on haiku.

**What did not replicate:** accuracy — dead tie, 6/12 both arms, discordant pairs perfectly
symmetric (state won 3 and 8, react won 13 and 15). And the token win came from the wrong
place: on the 4 tasks *both* arms solved, state actually cost more (24,287 vs 17,196 chars,
driven almost entirely by task 10). **State's advantage is earned on episodes that fail — it
fails cheaply, react fails expensively. It does not succeed more cheaply.** `Σ` also isn't
O(1) in practice: avg prompt grew 1,761→3,782 over 15 steps as `tested_hypotheses` and
`cmd_summary` filled up. Flat relative to a transcript, not flat absolutely.

## Round 3 — 5-arm matrix (haiku, 12 tasks × 3 reps = 180 episodes)

Added two variants after task 13 (a 10KB file state kept re-reading in full every step, 0/3
solved) exposed a real gap: nowhere to pin an artifact. `push` = runtime auto-caches read-like
observations into `Σ.blobs` (byte-budgeted, LRU evict — this became `v2-blob-runtime`). `pull` =
model may request a file by name; driver `cat`s it into the *same* prompt without touching Σ.
`both` = manifest + retained cache together.

| arm | solved/36 | per-rep mean | sd | vs react (paired) |
|---|---|---|---|---|
| pull | 28 | 9.3 | 0.58 | 3 wins, 0 losses, 9 ties |
| react | 25 | 8.3 | 1.15 | — |
| both | 21 | 7.0 | 1.00 | 0 wins, 4 losses |
| push | 20 | 6.7 | 0.58 | 0 wins, 4 losses |
| state | 19 | 6.3 | 1.53 | 0 wins, 6 losses |

Task 13 specifically — the artifact-resident thrash case:

| arm | solved/3 | avg steps | input tokens |
|---|---|---|---|
| state | 0/3 | 15.0 (cap, every rep) | 32,677 |
| pull | 3/3 | 2.7 | 13,763 |
| push | 3/3 | 5.0 | 22,526 |
| react | 3/3 | 4.3 | 16,882 |

0/3 → 3/3 for both fixes — the cleanest causal result of the round. But `both` (manifest +
retained cache together) was *worse* than either component alone, thrashing on task 13 anyway
with 11–12 repeated actions per episode despite the blob sitting resident. Combining two fixes
made it worse than either one; this wasn't fully explained from the aggregate numbers.

**A labeling mistake caught on review:** `pull` was first reported as "the better state
variant." It isn't a state variant at all — `uses_sigma = arm in ("state", "push", "both")`
excludes `pull`, which falls through to the full appended transcript plus a manifest. So
"pull beats state 6–2" conflated two changes (transcript-vs-Σ, and the manifest) into one
ranking. Corrected reading: `push` is the best *cheap* option (fixes task 13, keeps state's
token saving); `pull` shows a manifest helps the *transcript* architecture, which is a separate
result, not evidence a state variant can match react's accuracy.

## Round 4 — full deepseek-v4-flash matrix (A/B/C/D/E, ~2,500 episodes)

Switched to `deepseek-v4-flash-0731` (~$0.0016/episode) to afford matrix-scale runs. Skill
version was `v1-baseline` → `v2-blob-runtime` (`HEAD` at the time; eviction never fired once
across 288+ episodes — the byte ceiling was never actually reached).

**A — CTF, step cap 15 vs 30, 12 tasks × 3 reps × 8 strategies:**

| strategy | cap15 | cap30 | in_tok (cap30) |
|---|---|---|---|
| transcript+filelist (control) | 28/36 | 29/36 | 1.57M |
| transcript (control) | 25/36 | 29/36 | 2.31M |
| state only | 19/36 | 26/36 | 489k |
| state+plan | 22/36 | 25/36 | 398k |
| state+plan+guard | – | 24/36 | 384k |
| state+list+cache | 21/36 | 22/36 | 620k |
| state+auto-cache | 20/36 | 22/36 | 510k |
| state+filelist | 21/36 | 21/36 | 638k |

Control wins clearly at cap15; the gap halves at cap30. cap15 was cutting off long skill
episodes before they finished — a survivorship-bias artifact in the earlier reasoning, not a
real cap-independent effect. Loop guard didn't help (24 vs 25/36 without it, despite 126
triggers).

**C — simulated warehouse (paper's Algorithm 2, our reconstruction — no code was released for
this benchmark), all horizons:**

| strategy | T25 | T50 | T100 | T200 |
|---|---|---|---|---|
| transcript | 0.89 | 0.97 | 0.67 | 0.49 |
| state+plan | 0.80 | 0.79 | **0.76** | 0.48 |
| state only | 0.83 | 0.61 | 0.71 | 0.29 |

**D — full InterCode CTF, all 79 self-contained tasks, cap30, 3 reps (237 episodes/arm,
1,659/1,659 clean):**

| strategy | solved/237 | in_tok | $ | rejected |
|---|---|---|---|---|
| transcript+filelist (control) | **199** | 5.11M | 0.633 | 0 |
| transcript (control) | 197 | 4.86M | 0.638 | 0 |
| state+auto-cache | 191 | 1.95M | 0.421 | 112 |
| state only | 186 | 2.03M | 0.463 | 27 |
| state+plan | 183 | 2.13M | 0.495 | 14 |
| state+list+cache | 180 | 2.29M | 0.490 | 54 |
| state+filelist | 179 | 2.22M | 0.463 | 76 |

Both controls beat every skill variant, at ~2.3–2.6× the tokens — same shape as the 12-task
pilot, now at 5× the sample.

**E — τ-bench retail, DONE, the worst result of the project:**

| strategy | solved/36 | reward | turns | tool calls |
|---|---|---|---|---|
| transcript (control) | 27 | 0.750 | 786 | 247 |
| state only | **0** | 0.000 | 1,080 | 947 |
| state+plan | 1 | 0.028 | 1,060 | 874 |

1,080 turns = exactly 36×30 — not one state episode ended on its own. Root cause: the runtime's
prompt template hardcodes `"action": "<string: shell command>"`, which doesn't fit τ-bench's
real tool-call contract at all — an architecture mismatch, not a tuning problem. Tool results
also had nowhere to live in the schema and vanished after one step (final `Σ` averaged 129
bytes against a 56-byte empty schema).

**B — 3-model sweep (12 tasks, control vs best skill variant):**

| model (↑ strength) | control | skill (best) | gap |
|---|---|---|---|
| haiku-4.5 | 63.6% | 58.3% | −5.3pp |
| deepseek-v4-flash | 80.6% | 72.2% | −8.4pp |
| sonnet-4.5 | 75.0% | 50.0% | **−25.0pp** |

The gap does **not** shrink on a stronger model, as the paper's Gemini result might suggest —
it's largest on sonnet, the strongest model tested. Task-level win/loss pattern was also nearly
model-independent: the same tasks the skill lost, it lost on every model.

**Composite — skill vs no-skill across every closed benchmark:**

| bench | control | best skill variant | gap (pp) | tokens (control→skill) |
|---|---|---|---|---|
| A cap15 | 77.8% | 61.1% | −16.7 | 660k→296k (2.2×) |
| A cap30 | 80.6% | 72.2% | −8.3 | 2.31M→489k (4.7×) |
| C warehouse T25 | 0.89 | 0.80 | −9.0 | – |
| C warehouse T50 | 0.97 | 0.79 | −18.0 | – |
| C warehouse T100 | 0.67 | **0.76** | **+9.0 (skill wins)** | 140k→51k (2.7×) |
| C warehouse T200 | 0.49 | 0.48 | −1.0 (near tie) | 1.96M→613k (3.2×) |
| D full CTF (n=237) | 84.0% | 80.6% | −3.4 (narrowest gap, largest sample) | 5.11M→1.95M (2.6×) |
| E τ-bench retail | 75.0% | 2.8% | **−72.2 (catastrophic)** | tool calls 247→874–947 (skill costs *more*) |
| B haiku | 63.6% | 58.3% | −5.3 | – |
| B deepseek | 80.6% | 72.2% | −8.4 | – |
| B sonnet | 75.0% | 50.0% | −25.0 | – |

Control wins almost everywhere. The gap narrows as step budget grows (A: −16.7→−8.3pp,
cap15→cap30) and as sample size grows (D's −3.4pp on 237 episodes is the narrowest of any
CTF-shaped run). The one real win is warehouse at T=100. The one real catastrophe is τ-bench.
Token cost favors the skill everywhere it runs at all (2–5× cheaper), which never compensates
for solve rate except at T=100.

## Why: two separate bugs, found by reading the runtime's own code

A follow-up pass read `skillstate.py` (376 lines) and all schemas directly rather than
theorizing from aggregates.

**1. Content-persistence gap.** `ctf.json` already has `discovered_flags` and
`tested_hypotheses` as append-only lists — exactly the mechanism needed to survive a step
boundary. The model just doesn't reliably use them. Traced live on task 96: the state arm
burned 27 steps re-discovering the same fact 6 times (`grep` → line 378 → forgotten → `grep`
again) because the one free-text field (`cmd_summary`) had replace semantics, and the prompt
explained the merge mechanics but never said *why* committing mattered. Control solved the same
task in 7 clean steps, because a transcript holds everything for free.

**2. A flat JSON-format ceiling that both arms share — but the control's blows up at
length.** The warehouse T=100 "win" looked at first like a skill advantage. Re-examined:
control's malformed-response rate exploded with horizon — 10.7% → 2.7% → 32% → 48.5% at
T25/50/100/200 — because a longer transcript makes the model reason longer before emitting
JSON, hits `max_tokens`, and gets scored 0 with no retry. The skill's malformed rate stayed flat
at 7–10% across every horizon. **This is a generic response-length problem that hits the
control harder at long horizons, not a virtue of state's content.** Correcting for it would
likely narrow or erase the T=100 "win."

τ-bench's failure is the same content-persistence gap taken to its limit: no field exists for
"an unprocessed tool result," so all of it has to be judged relevant-or-not in the same step
with no room for error.

## Fixes tried after diagnosis (this is `v3-persistence-fixes`), and what actually changed

Two of the four `v3` changes were spot-checked against baseline behavior, honestly, on 3
recurring tasks (0, 6, 96) — not a re-run of the matrix above:

| task | before | after urgency-line fix |
|---|---|---|
| 96 (the traced loop) | 27 steps, looped 6× on the same `grep` | **solved, 7 steps — matches control** |
| 6 | solved, 25 steps | solved, 21 steps (slightly better) |
| 0 | unsolved, 30/30 (hit cap) | **unsolved, 30/30 — unchanged** |

Task 0's episodes showed `cat unpackme.flag.py` repeated 10+ times despite the added urgency
line — the same disease as task 96, but the nudge didn't take. **The prompt-only fix is
probabilistic, not guaranteed**: it worked once and failed once on the same failure mode. The
JSON-first ordering fix was untested by this batch — no episode in it, before or after, ever
hit a malformed/truncated response; that only shows up on long horizons (warehouse T100+),
which wasn't re-run after the fix.

The strongest remaining lead, not yet re-benchmarked: `auto-cache` (`push`) already scores
highest in the full D sweep (191/237) but also has the most rejected patches (112) — traced to
`check_limits()` voiding an entire patch when *one* field goes over budget, even if the rest was
valid. Fixing that atomicity bug is flagged in-code as the top-priority next step, ahead of
inventing anything new.

## What's honestly still open

- **v3's fixes are validated on a 3-task sample that was also used to iterate on the fix.**
  Real risk of tuning to that exact sample; not confirmed out-of-sample. (A later out-of-sample
  check on two fresh tasks: one flipped from unsolved to solved, one stayed unsolved — consistent
  with "probabilistic, not guaranteed," not a refutation.)
- **No stop/loop-guard mechanism exists inside the runtime itself.** `skillstate.py` has no
  step counter, no cap, no repeat-action detector — every version of that in this project lives
  in the *benchmark drivers*, not the skill. In real (non-benchmarked) use, nothing built into
  Talos will stop a run that's looping; only the orchestrating model or a human watching does.
  A runtime-level stall detector (flag N identical patches in a row, surface it rather than act
  on it) is designed and unit-tested (27/27) but only validated against synthetic journal
  entries, not a live episode reacting to the notice.
- **No native tool-call support.** The τ-bench failure is partly architectural: the prompt
  template hardcodes the action field as a shell-command string. `cmd_prompt` already accepts
  `--instructions`, so the same mechanism could carry a different action contract — not done.
- **The driver's prompt sometimes diverged from the skill's own generated prompt** (an early
  `state` vs a later `statex` arm, built specifically to close that gap) — a real source of
  measurement noise across rounds that isn't fully disentangled from architecture effects.
- Raw episode data and the benchmark driver harness (Docker/InterCode setup, warehouse
  simulator, `ctf_ab2.py`) are not part of this repo — they lived in a separate local bench
  workspace and scratch directories. The numbers above are transcribed from that work, not
  re-derivable by running anything in this repository as-is.

## The honest one-line verdict

**The skill wins in exactly one of the three scenario shapes tested — the one it was built
for.** Long-horizon tasks where the past is genuinely unrecoverable (warehouse): wins on
accuracy, cheaper. Short-horizon tasks where a file can just be re-read (CTF): loses on
accuracy at every scale tested, 2–5× cheaper. Real multi-turn tool-call dialogues (τ-bench):
fails outright, an architecture gap rather than a tuning one. That is not a caveat found in the
paper — the paper doesn't run this comparison — but it lines up with, and independently
motivated, the "When to keep the transcript instead" section already in `SKILL.md`.
