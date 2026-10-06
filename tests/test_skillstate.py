"""Tests for the deterministic state runtime.

The runtime owns the schema and the merge. A malformed patch must never reach
Sigma. These tests pin the merge algebra the prompt template promises.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "skillstate.py"
SCHEMA = {
    "fields": {
        "checked": "list",
        "findings": "dict",
        "cursor": "str",
        "round": "int",
    }
}


def run(args: list[str], stdin: str = "", cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA))
    res = run(["init", "--dir", str(tmp_path / "st"), "--schema", str(schema)])
    assert res.returncode == 0, res.stderr
    return tmp_path / "st"


def sigma(store: Path) -> dict:
    return json.loads((store / "state.json").read_text())


def current_ids(store: Path):
    """Echo the runtime's current obs id and the short state hash.

    Duplicates the runtime's formula so tests can build a correct wrapper.
    The id names the step in flight: accepted patches so far, plus one. It
    does not move when a prompt is rendered.
    """
    obs_file = store / "obs_count"
    done = int(obs_file.read_text().strip()) if obs_file.exists() else 0
    sigma = json.loads((store / "state.json").read_text())
    canonical = json.dumps(sigma, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"obs#{done + 1}", h[:8]


def send(store: Path, body: dict):
    """Send `body` (already a full wrapper or a bare patch) to `patch`."""
    return run(["patch", "--dir", str(store)], stdin=json.dumps(body))


def mk_wrapper(store: Path, state_patch: dict, obs=None, sh=None, drop=()):
    """Build a full response wrapper echoing the current ids.

    `obs`/`sh` override the echoed values (to test stale echoes); `drop`
    removes keys entirely (to test missing-field rejections).
    """
    cur_obs, cur_hash = current_ids(store)
    w = {
        "state_patch": state_patch,
        "action": "test action",
        "obs_ref": obs if obs is not None else cur_obs,
        "state_hash": sh if sh is not None else cur_hash,
    }
    for k in drop:
        w.pop(k, None)
    return w


def patch(store: Path, body: dict):
    """Send a state patch wrapped in the full response wrapper.

    Existing tests never call `prompt` first, so they run at the first step;
    the wrapper echoes that id and the current short state hash, which the
    runtime accepts. This keeps the merge-algebra tests exercising the merge
    path, not the echo/CAS machinery (covered by the new tests below).
    """
    return send(store, mk_wrapper(store, body))


def test_init_seeds_empty_sigma_from_schema_defaults(store):
    assert sigma(store) == {"checked": [], "findings": {}, "cursor": "", "round": 0}


def test_show_emits_compact_json_without_spaces(store):
    out = run(["show", "--dir", str(store)]).stdout.strip()
    assert " " not in out
    assert json.loads(out) == sigma(store)


def test_patch_with_scalar_replaces_value(store):
    assert patch(store, {"cursor": "svc-04"}).returncode == 0
    assert sigma(store)["cursor"] == "svc-04"


def test_patch_with_nested_dict_deep_merges_and_keeps_siblings(store):
    patch(store, {"findings": {"api": ["R2"]}})
    patch(store, {"findings": {"web": ["R5"]}})
    assert sigma(store)["findings"] == {"api": ["R2"], "web": ["R5"]}


def test_patch_with_list_replaces_wholesale_not_appends(store):
    patch(store, {"checked": ["a", "b"]})
    patch(store, {"checked": ["c"]})
    assert sigma(store)["checked"] == ["c"]


def test_patch_with_append_op_extends_list_without_resending_it(store):
    patch(store, {"checked": ["a", "b"]})
    patch(store, {"checked": {"$append": ["c"]}})
    assert sigma(store)["checked"] == ["a", "b", "c"]


def test_patch_with_null_deletes_nested_key(store):
    patch(store, {"findings": {"api": ["R2"], "web": ["R5"]}})
    patch(store, {"findings": {"api": None}})
    assert sigma(store)["findings"] == {"web": ["R5"]}


def test_patch_with_unknown_top_level_key_is_rejected_and_sigma_unchanged(store):
    before = sigma(store)
    res = patch(store, {"bogus_field": 1})
    assert res.returncode != 0
    assert "bogus_field" in res.stderr
    assert sigma(store) == before


def test_patch_with_wrong_type_is_rejected_and_sigma_unchanged(store):
    before = sigma(store)
    res = patch(store, {"round": "three"})
    assert res.returncode != 0
    assert sigma(store) == before


def test_patch_with_malformed_json_is_rejected_and_sigma_unchanged(store):
    before = sigma(store)
    res = patch(store, {})
    res = run(["patch", "--dir", str(store)], stdin="{not json,}")
    assert res.returncode != 0
    assert sigma(store) == before


def test_rejected_patch_is_recorded_in_journal_for_audit(store):
    patch(store, {"bogus_field": 1})
    entries = [
        json.loads(x) for x in (store / "patches.jsonl").read_text().splitlines() if x
    ]
    assert entries[-1]["accepted"] is False
    assert entries[-1]["patch"] == {"bogus_field": 1}


def test_journal_records_every_accepted_patch_in_order(store):
    patch(store, {"cursor": "a"})
    patch(store, {"cursor": "b"})
    accepted = [
        json.loads(x)
        for x in (store / "patches.jsonl").read_text().splitlines()
        if x and json.loads(x)["accepted"]
    ]
    assert [e["patch"]["cursor"] for e in accepted] == ["a", "b"]


def test_rollback_replays_journal_to_earlier_step(store):
    patch(store, {"cursor": "a"})
    patch(store, {"cursor": "b"})
    patch(store, {"cursor": "c"})
    assert run(["rollback", "--dir", str(store), "--to", "1"]).returncode == 0
    assert sigma(store)["cursor"] == "a"


def test_prompt_includes_sigma_and_both_required_response_keys(store):
    patch(store, {"cursor": "svc-04"})
    out = run(["prompt", "--dir", str(store), "--observation", "file X read"]).stdout
    assert "svc-04" in out
    assert "state_patch" in out
    assert "action" in out
    assert "file X read" in out


def test_patch_is_atomic_when_write_target_is_unwritable(store, monkeypatch):
    before = sigma(store)
    (store / "state.json").chmod(0o444)
    res = patch(store, {"cursor": "x"})
    (store / "state.json").chmod(0o644)
    assert res.returncode != 0
    assert sigma(store) == before


# --- byte limits: blobs must not grow Sigma without bound ---

LIMITED = {
    "fields": {"blobs": "dict", "notes": "str"},
    "limits": {"blobs": 200, "notes": 50},
}


@pytest.fixture()
def limited(tmp_path: Path) -> Path:
    schema = tmp_path / "lim.json"
    schema.write_text(json.dumps(LIMITED))
    res = run(["init", "--dir", str(tmp_path / "lim"), "--schema", str(schema)])
    assert res.returncode == 0, res.stderr
    return tmp_path / "lim"


def test_patch_within_field_limit_is_accepted(limited):
    res = patch(limited, {"notes": "x" * 40})
    assert res.returncode == 0
    assert sigma(limited)["notes"] == "x" * 40


def test_patch_exceeding_field_limit_is_rejected_and_sigma_unchanged(limited):
    before = sigma(limited)
    res = patch(limited, {"notes": "x" * 500})
    assert res.returncode != 0
    assert "limit" in res.stderr.lower()
    assert sigma(limited) == before


def test_limit_applies_to_merged_result_not_just_the_patch(limited):
    """Two small blobs that individually fit must still be rejected once their
    merged total exceeds the field's ceiling."""
    assert patch(limited, {"blobs": {"a": "x" * 90}}).returncode == 0
    res = patch(limited, {"blobs": {"b": "y" * 150}})
    assert res.returncode != 0
    assert sigma(limited)["blobs"] == {"a": "x" * 90}


def test_evicting_a_blob_frees_room_for_a_new_one(limited):
    patch(limited, {"blobs": {"a": "x" * 150}})
    assert patch(limited, {"blobs": {"b": "y" * 100}}).returncode != 0
    assert patch(limited, {"blobs": {"a": None, "b": "y" * 100}}).returncode == 0
    assert sigma(limited)["blobs"] == {"b": "y" * 100}


def test_schema_without_limits_still_accepts_large_values(store):
    assert patch(store, {"cursor": "x" * 10000}).returncode == 0


def prompt(store: Path, observation: str = "obs"):
    return run(["prompt", "--dir", str(store), "--observation", observation])


# --- stall notice: advisory only, never blocks anything ---


def test_no_notice_on_a_fresh_store(store):
    res = prompt(store)
    assert "Notice:" not in res.stdout


def test_notice_after_a_streak_of_rejections(store):
    for _ in range(3):
        patch(store, {"cursor": 123})  # wrong type, rejected every time
    res = prompt(store)
    assert "Notice:" in res.stdout
    assert "rejected" in res.stdout


def test_no_notice_when_rejections_are_not_consecutive(store):
    patch(store, {"cursor": 123})  # rejected
    patch(store, {"cursor": "ok"})  # accepted, breaks the streak
    patch(store, {"cursor": 123})  # rejected
    res = prompt(store)
    assert "Notice:" not in res.stdout


def test_notice_when_sigma_stops_changing(store):
    patch(store, {"cursor": "a"})
    for _ in range(5):
        patch(store, {"cursor": "a"})  # accepted, but identical - no real change
    res = prompt(store)
    assert "Notice:" in res.stdout
    assert "has not changed" in res.stdout


def test_no_notice_when_sigma_keeps_changing(store):
    for i in range(6):
        patch(store, {"cursor": str(i)})  # accepted, genuinely different each time
    res = prompt(store)
    assert "Notice:" not in res.stdout


def test_oversized_field_does_not_sink_a_valid_field_in_the_same_patch(limited):
    """One field over its ceiling must not discard an unrelated valid update
    proposed in the same patch — only that field is dropped."""
    res = patch(limited, {"notes": "ok", "blobs": {"a": "x" * 500}})
    assert res.returncode == 0
    assert sigma(limited) == {"notes": "ok", "blobs": {}}


def test_partially_applied_patch_is_logged_as_accepted_with_a_reason(limited):
    patch(limited, {"notes": "ok", "blobs": {"a": "x" * 500}})
    journal = (limited / "patches.jsonl").read_text().splitlines()
    entry = json.loads(journal[-1])
    assert entry["accepted"] is True
    assert "blobs" in entry["reason"]
    assert entry["patch"] == {"notes": "ok"}


def test_prompt_no_stall_notice_suppresses_the_notice(tmp_path):
    """--no-stall-notice must silence an otherwise-firing notice."""
    run(["init", "--schema", json.dumps(SCHEMA), "--dir", str(tmp_path)])
    for _ in range(3):  # three rejections in a row is one of the two signals
        run(["patch", "--dir", str(tmp_path)], stdin=json.dumps({"nope": 1}))

    with_notice = run(["prompt", "--dir", str(tmp_path), "--observation", "x"])
    without = run(["prompt", "--dir", str(tmp_path), "--observation", "x",
                   "--no-stall-notice"])

    assert "Notice:" in with_notice.stdout
    assert "Notice:" not in without.stdout
    assert without.returncode == 0


def test_prompt_shows_append_scoped_to_a_field(tmp_path):
    """A bare top-level {"$append": [...]} was the dominant rejection cause at scale
    (137 rejections in 237 episodes). The prompt must show it owned by a field."""
    run(["init", "--schema", json.dumps(SCHEMA), "--dir", str(tmp_path)])
    out = run(["prompt", "--dir", str(tmp_path), "--observation", "x"]).stdout

    assert '{"field": {"$append": [...]}}' in out
    assert "never a patch key on its own" in out


def test_action_contract_defaults_to_a_shell_string(tmp_path):
    """The default must stay byte-identical: every published benchmark used it."""
    run(["init", "--schema", json.dumps(SCHEMA), "--dir", str(tmp_path)])
    out = run(["prompt", "--dir", str(tmp_path), "--observation", "x"]).stdout
    assert '"action": "<string: the exact next step you want executed>"' in out


def test_action_contract_is_swappable(tmp_path):
    """The action shape belongs to the task. A tool-calling caller must be able to
    say so, or the template contradicts its own system prompt - which is what kept
    the skill off tau-bench entirely."""
    run(["init", "--schema", json.dumps(SCHEMA), "--dir", str(tmp_path)])
    contract = '{"tool": "<name>", "args": {...}} OR {"respond": "<text>"}'
    out = run(["prompt", "--dir", str(tmp_path), "--observation", "x",
               "--action-contract", contract]).stdout
    assert contract in out
    assert '"<string: the exact next step you want executed>"' not in out


# --- P1: obs echo + state_hash CAS + nested type contracts ---

HASH_RE = re.compile(r"^state_hash: ([0-9a-f]{8})$", re.MULTILINE)


def _hash_of(prompt_out: str) -> str:
    m = HASH_RE.search(prompt_out)
    assert m, f"no short state_hash line in prompt:\n{prompt_out}"
    return m.group(1)


def test_first_prompt_assigns_obs1_and_hash_line(store):
    r1 = prompt(store, "first")
    assert "Latest Observation (obs#1): first" in r1.stdout
    _hash_of(r1.stdout)
    # The id names the step, so a second render of the same step keeps it.
    r2 = prompt(store, "second")
    assert "Latest Observation (obs#1): second" in r2.stdout
    assert patch(store, {"cursor": "done"}).returncode == 0
    r3 = prompt(store, "third")
    assert "Latest Observation (obs#2): third" in r3.stdout


def test_prompt_hash_matches_canonical_state(store):
    r = prompt(store, "x")
    sigma = json.loads((store / "state.json").read_text())
    canonical = json.dumps(sigma, sort_keys=True, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    assert _hash_of(r.stdout) == expected


def test_prompt_hash_changes_after_committed_patch(store):
    h1 = _hash_of(prompt(store, "x").stdout)
    assert patch(store, {"cursor": "changed"}).returncode == 0
    h2 = _hash_of(prompt(store, "x").stdout)
    assert h1 != h2


def test_full_wrapper_with_correct_echo_is_accepted(store):
    prompt(store, "x")  # advance to obs#1
    res = send(store, mk_wrapper(store, {"cursor": "ok"}))
    assert res.returncode == 0
    assert sigma(store)["cursor"] == "ok"


def test_stale_obs_ref_is_rejected(store):
    # Steps advance by completing them, so two accepted patches put the store
    # on step 3. An echo of step 1 is then genuinely stale.
    assert patch(store, {"cursor": "a"}).returncode == 0
    assert patch(store, {"cursor": "b"}).returncode == 0
    before = sigma(store)
    res = send(store, mk_wrapper(store, {"cursor": "x"}, obs="obs#1"))
    assert res.returncode == 2
    assert "obs_ref mismatch" in res.stderr
    assert "obs#3" in res.stderr
    assert sigma(store) == before


def test_wrong_state_hash_is_rejected(store):
    prompt(store, "a")  # obs#1
    _, cur_hash = current_ids(store)
    before = sigma(store)
    res = send(store, mk_wrapper(store, {"cursor": "x"}, sh="0" * 64))
    assert res.returncode == 2
    assert "state_hash mismatch" in res.stderr
    assert cur_hash in res.stderr
    assert sigma(store) == before


def test_missing_obs_ref_is_rejected(store):
    prompt(store, "a")
    res = send(store, mk_wrapper(store, {"cursor": "x"}, drop=("obs_ref",)))
    assert res.returncode == 2
    assert "missing obs_ref" in res.stderr


def test_missing_state_hash_is_rejected(store):
    prompt(store, "a")
    res = send(store, mk_wrapper(store, {"cursor": "x"}, drop=("state_hash",)))
    assert res.returncode == 2
    assert "missing state_hash" in res.stderr


def test_init_force_resets_obs_counter(store, tmp_path):
    assert patch(store, {"cursor": "a"}).returncode == 0
    assert patch(store, {"cursor": "b"}).returncode == 0
    assert (store / "obs_count").read_text().strip() == "2"
    schema = tmp_path / "schema.json"
    res = run(["init", "--dir", str(store), "--schema", str(schema), "--force"])
    assert res.returncode == 0, res.stderr
    assert not (store / "obs_count").exists()
    r = prompt(store, "again")
    assert "obs#1" in r.stdout
    assert not (store / "obs_count").exists()  # rendering is not an event


def test_type_contract_rejects_wrong_nested_type(store):
    assert patch(store, {"findings": {"a": {"n": 1}}}).returncode == 0
    before = sigma(store)
    res = patch(store, {"findings": {"a": {"n": "1"}}})
    assert res.returncode == 2
    assert "expected number, received string" in res.stderr
    assert sigma(store) == before


def test_type_contract_int_float_are_interchangeable(store):
    assert patch(store, {"findings": {"a": {"n": 1}}}).returncode == 0
    assert patch(store, {"findings": {"a": {"n": 1.5}}}).returncode == 0
    assert sigma(store)["findings"]["a"]["n"] == 1.5
    assert patch(store, {"findings": {"a": {"n": 2}}}).returncode == 0
    assert sigma(store)["findings"]["a"]["n"] == 2


def test_type_contract_bool_and_str_are_strict(store):
    assert patch(store, {"findings": {"a": {"b": True, "s": "x", "n": 1}}}).returncode == 0
    before = sigma(store)
    res = patch(store, {"findings": {"a": {"b": 1}}})
    assert res.returncode == 2
    assert "expected boolean, received number" in res.stderr
    res = patch(store, {"findings": {"a": {"s": 5}}})
    assert res.returncode == 2
    assert "expected string, received number" in res.stderr
    assert sigma(store) == before


def test_type_contract_new_subkey_is_allowed(store):
    assert patch(store, {"findings": {"a": {"n": 1}}}).returncode == 0
    res = patch(store, {"findings": {"a": {"fresh": "x"}}})
    assert res.returncode == 0
    assert sigma(store)["findings"]["a"]["fresh"] == "x"


def test_type_contract_reports_nested_dot_path(store):
    assert patch(store, {"findings": {"a": {"b": {"n": 1}}}}).returncode == 0
    res = patch(store, {"findings": {"a": {"b": {"n": "x"}}}})
    assert res.returncode == 2
    assert "findings.a.b.n" in res.stderr
    assert "expected number, received string" in res.stderr


def test_type_contract_null_still_deletes_without_type_check(store):
    assert patch(store, {"findings": {"a": {"n": 1}}}).returncode == 0
    res = patch(store, {"findings": {"a": {"n": None}}})
    assert res.returncode == 0
    assert "n" not in sigma(store)["findings"]["a"]


def test_type_contract_does_not_interfere_with_append(store):
    assert patch(store, {"checked": ["a", "b"]}).returncode == 0
    res = patch(store, {"checked": {"$append": ["c"]}})
    assert res.returncode == 0
    assert sigma(store)["checked"] == ["a", "b", "c"]


def test_rejection_is_journaled_with_reason(store):
    prompt(store, "a")  # obs#1
    send(store, mk_wrapper(store, {"cursor": "x"}, obs="obs#0"))  # stale
    entries = [
        json.loads(x)
        for x in (store / "patches.jsonl").read_text().splitlines()
        if x
    ]
    assert entries[-1]["accepted"] is False
    assert "obs_ref mismatch" in entries[-1]["reason"]


# --- P2 ------------------------------------------------------------------
# The observation id identifies a STEP, not a render, and not the text of the
# observation. Rendering is not an event: a caller may render the prompt twice
# (to log it, to retry a transport error, to look at it) and the id the model
# echoed must still be valid. The counter advancing on render is what broke the
# correction loop, whose whole job is to re-ask about the same step.

def _wrapper(store, patch_body, obs=None, h=None):
    """mk_wrapper returns a dict; run() wants text on stdin."""
    return json.dumps(mk_wrapper(store, patch_body, obs=obs, sh=h))


def test_prompt_does_not_advance_the_observation_id(store):
    first = run(["prompt", "--dir", str(store), "--observation", "same"]).stdout
    second = run(["prompt", "--dir", str(store), "--observation", "same"]).stdout
    assert "obs#1" in first
    assert "obs#1" in second


def test_accepted_patch_advances_the_observation_id(store):
    ok = run(["patch", "--dir", str(store)], stdin=_wrapper(store, {"round": 1}))
    assert ok.returncode == 0, ok.stderr
    assert "obs#2" in run(["prompt", "--dir", str(store), "--observation", "b"]).stdout


def test_rejected_patch_does_not_advance_the_observation_id(store):
    bad = run(["patch", "--dir", str(store)], stdin=_wrapper(store, {"nope": 1}))
    assert bad.returncode == 2
    assert "obs#1" in run(["prompt", "--dir", str(store), "--observation", "a"]).stdout


# A patch can be both stale and malformed. The malformed part is wrong either
# way and is the actionable half, so it is reported first; staleness still
# blocks the merge.

def test_schema_error_is_reported_before_staleness(store):
    r = run(["patch", "--dir", str(store)],
            stdin=_wrapper(store, {"nope": 1}, obs="obs#999"))
    assert r.returncode == 2
    assert "unknown key" in r.stderr
    assert "obs_ref" not in r.stderr


def test_state_hash_is_echoed_as_a_short_prefix(store):
    """64 hex characters retyped every step, by a model measured to corrupt long
    literals, is a failure mode bought for nothing. Eight is plenty here."""
    out = run(["prompt", "--dir", str(store), "--observation", "x"]).stdout
    shown = [l for l in out.splitlines() if l.startswith("state_hash:")][0].split(": ", 1)[1]
    assert len(shown) == 8

    ok = run(["patch", "--dir", str(store)], stdin=_wrapper(store, {"round": 1}))
    assert ok.returncode == 0, ok.stderr


def test_full_length_hash_is_still_accepted(store):
    """Forgiving in the right direction: a caller that echoes all 64 still works."""
    import hashlib
    sigma = json.loads((store / "state.json").read_text())
    full = hashlib.sha256(json.dumps(sigma, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    ok = run(["patch", "--dir", str(store)],
             stdin=_wrapper(store, {"round": 1}, h=full))
    assert ok.returncode == 0, ok.stderr


def test_append_items_are_type_checked(store):
    """$append used to bypass the contract entirely, so a dict could be appended
    into a list of strings unchecked."""
    run(["patch", "--dir", str(store)], stdin=_wrapper(store, {"checked": ["a"]}))
    r = run(["patch", "--dir", str(store)],
            stdin=_wrapper(store, {"checked": {"$append": [{"not": "a string"}]}},
                           obs="obs#2"))
    assert r.returncode == 2
    assert "expected string" in r.stderr


def test_hybrid_policy_is_announced_and_flips_at_the_crossover(store):
    """The best arm ever measured kept the transcript while the episode was
    short and dropped it past the crossover: 192/237 against the control's 199,
    at 6.7 steps and 18 cap-outs, the closest any state arm came. It lived in a
    benchmark driver. The runtime knows the step number, so the policy belongs
    here and the caller is simply told which mode it is in."""
    early = run(["prompt", "--dir", str(store), "--observation", "x",
                 "--hybrid-k", "3"]).stdout
    assert "transcript_policy: keep" in early

    for _ in range(2):
        assert patch(store, {"round": 1}).returncode == 0
    late = run(["prompt", "--dir", str(store), "--observation", "x",
                "--hybrid-k", "3"]).stdout
    assert "transcript_policy: drop" in late


def test_hybrid_policy_is_off_unless_asked_for(store):
    out = run(["prompt", "--dir", str(store), "--observation", "x"]).stdout
    assert "transcript_policy" not in out


# --- P3: dict keys must not drift ----------------------------------------
# A dict keyed freely is a transcript with extra steps: the model writes
# api-notes, then api_note, then api_notes, and nothing is ever overwritten.
# Fields listed in the schema's key_discipline reject a key that is too close
# to one already there, and say which one to use instead.

FACTS_SCHEMA = {
    "fields": {"files": "dict", "cursor": "str"},
    "key_discipline": ["files"],
}


@pytest.fixture()
def facts(tmp_path: Path) -> Path:
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(FACTS_SCHEMA))
    res = run(["init", "--dir", str(tmp_path / "st"), "--schema", str(schema)])
    assert res.returncode == 0, res.stderr
    return tmp_path / "st"


def _send(store, patch_body, obs=None):
    return send(store, mk_wrapper(store, patch_body, obs=obs))


def test_near_duplicate_key_is_rejected_and_names_the_original(facts):
    assert _send(facts, {"files": {"api-notes": "first"}}).returncode == 0
    r = _send(facts, {"files": {"api_notes": "second"}}, obs="obs#2")
    assert r.returncode == 2
    assert "api-notes" in r.stderr
    assert "too close" in r.stderr


def test_the_existing_key_still_takes_an_update(facts):
    assert _send(facts, {"files": {"api-notes": "first"}}).returncode == 0
    r = _send(facts, {"files": {"api-notes": "second"}}, obs="obs#2")
    assert r.returncode == 0, r.stderr
    assert sigma(facts)["files"]["api-notes"] == "second"


def test_a_genuinely_different_key_is_fine(facts):
    assert _send(facts, {"files": {"/ctf/70/suspicious.dd": "ext3 image"}}).returncode == 0
    r = _send(facts, {"files": {"/ctf/70/mydata.tgz": "tarball"}}, obs="obs#2")
    assert r.returncode == 0, r.stderr
    assert len(sigma(facts)["files"]) == 2


def test_key_discipline_only_applies_where_declared(facts):
    """cursor is not a dict and not declared; nothing to police."""
    assert _send(facts, {"cursor": "a"}).returncode == 0
    assert _send(facts, {"cursor": "b"}, obs="obs#2").returncode == 0


def test_path_like_keys_are_compared_exactly_not_fuzzily(facts):
    """A compressed file and its extracted form are two different files.

    Found in the P3 run: dds1-alpine.flag.img.gz was already in the state and
    the patch adding dds1-alpine.flag.img was rejected as "too close". They are
    0.95 similar as strings and completely different as objects. Where the key
    space is the filesystem, the filesystem has already settled distinctness,
    so fuzzy matching has nothing to add and plenty to break. Keep it for free
    labels like api-notes, where there is no such authority.
    """
    assert _send(facts, {"files": {"dds1-alpine.flag.img.gz": "gzip archive"}}).returncode == 0
    r = _send(facts, {"files": {"dds1-alpine.flag.img": "ext2 image"}}, obs="obs#2")
    assert r.returncode == 0, r.stderr
    assert len(sigma(facts)["files"]) == 2


def test_fuzzy_matching_still_guards_free_labels(facts):
    """The api-notes case must keep working: no dot, no slash, no authority."""
    assert _send(facts, {"files": {"api-notes": "x"}}).returncode == 0
    r = _send(facts, {"files": {"api_notes": "y"}}, obs="obs#2")
    assert r.returncode == 2
    assert "too close" in r.stderr


# ---------------------------------------------------------------------------
# `ids`: the echo values, straight from the runtime
#
# Two harnesses recomputed obs_ref and the hash themselves. Both drifted from
# the runtime the moment the counter changed meaning. The runtime answers the
# question now; nobody else gets to guess.
# ---------------------------------------------------------------------------

def ids(store: Path) -> dict:
    res = run(["ids", "--dir", str(store)])
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_ids_before_any_step_names_the_step_in_flight(store):
    assert ids(store)["obs_ref"] == "obs#1"


def test_ids_agrees_with_the_formula_the_tests_use(store):
    obs, sh = current_ids(store)
    out = ids(store)
    assert (out["obs_ref"], out["state_hash"]) == (obs, sh)


def test_ids_matches_what_the_prompt_demands(store):
    body = run(["prompt", "--dir", str(store), "--observation", "o"]).stdout
    out = ids(store)
    assert out["obs_ref"] in body
    assert out["state_hash"] in body


def test_ids_does_not_move_when_a_prompt_is_rendered(store):
    before = ids(store)["obs_ref"]
    run(["prompt", "--dir", str(store), "--observation", "o"])
    assert ids(store)["obs_ref"] == before


def test_ids_advances_after_an_accepted_patch(store):
    assert patch(store, {"cursor": "svc-04"}).returncode == 0
    assert ids(store)["obs_ref"] == "obs#2"


def test_ids_values_are_accepted_by_patch(store):
    out = ids(store)
    res = send(store, {"state_patch": {"cursor": "a"}, "action": "x",
                       "obs_ref": out["obs_ref"], "state_hash": out["state_hash"]})
    assert res.returncode == 0, res.stderr


def test_ids_short_hash_is_a_prefix_of_the_full_one(store):
    out = ids(store)
    assert out["state_hash_full"].startswith(out["state_hash"])
    assert len(out["state_hash"]) == 8


def test_ids_reports_no_hash_when_the_store_has_none(tmp_path):
    d = tmp_path / "nh"
    assert run(["init", "--dir", str(d), "--schema", json.dumps(SCHEMA),
                "--no-state-hash"]).returncode == 0
    out = json.loads(run(["ids", "--dir", str(d)]).stdout)
    assert out["state_hash"] is None
    assert out["obs_ref"] == "obs#1"


# ---------------------------------------------------------------------------
# scalar $append
#
# 116 of the 124 rejections in the S-warehouse run — 94% of every rejection
# the project has ever recorded on a long horizon — were a model writing
# {"field": {"$append": "one note"}} instead of {"$append": ["one note"]}.
# The intent is not ambiguous: appending a scalar appends one element. The
# runtime used to charge a full correction round-trip for the bracket.
# ---------------------------------------------------------------------------

def test_append_of_a_bare_string_adds_one_element(store):
    assert patch(store, {"checked": {"$append": "svc-04"}}).returncode == 0
    assert sigma(store)["checked"] == ["svc-04"]


def test_append_of_a_bare_string_does_not_splat_characters(store):
    patch(store, {"checked": {"$append": "abc"}})
    assert sigma(store)["checked"] == ["abc"]


def test_append_of_a_bare_number_adds_one_element(store):
    assert patch(store, {"checked": {"$append": 7}}).returncode == 0
    assert sigma(store)["checked"] == [7]


def test_append_of_a_bare_dict_adds_one_element(store):
    assert patch(store, {"checked": {"$append": {"a": 1}}}).returncode == 0
    assert sigma(store)["checked"] == [{"a": 1}]


def test_append_of_a_list_is_unchanged(store):
    patch(store, {"checked": {"$append": ["a", "b"]}})
    assert sigma(store)["checked"] == ["a", "b"]


def test_append_of_null_still_appends_one_element(store):
    """null is the deletion sentinel for a FIELD, not for an appended item."""
    assert patch(store, {"checked": {"$append": None}}).returncode == 0
    assert sigma(store)["checked"] == [None]


def test_scalar_append_to_a_non_list_field_is_still_rejected(store):
    res = patch(store, {"cursor": {"$append": "x"}})
    assert res.returncode != 0
    assert "needs a list" in res.stderr


def test_scalar_append_still_obeys_the_element_type_contract(store):
    patch(store, {"checked": {"$append": ["a"]}})
    res = patch(store, {"checked": {"$append": 7}})
    assert res.returncode != 0, "a homogeneous list of str must not take an int"


def test_scalar_append_is_journalled_in_its_coerced_form(store):
    """The journal must show what was merged, not what arrived."""
    patch(store, {"checked": {"$append": "svc-04"}})
    last = json.loads((store / "patches.jsonl").read_text().splitlines()[-1])
    assert last["patch"]["checked"]["$append"] == ["svc-04"]


# ---------------------------------------------------------------------------
# contract decay (--brief-after)
#
# Measured on warehouse T=25: a state arm with zero rejections and no
# state_hash still emitted 4140 output tokens per step against the
# transcript control's 1090. Nothing was being retried. The prompt itself
# was being re-read and re-reasoned about, 25 times, and output is ~25x the
# price of input. After a few steps the model has seen the merge contract;
# teaching it again every step is the bill.
# ---------------------------------------------------------------------------

def brief(store: Path, n: int, obs: str = "o") -> str:
    return run(["prompt", "--dir", str(store), "--observation", obs,
                "--brief-after", str(n), "--no-stall-notice"]).stdout


def full(store: Path, obs: str = "o") -> str:
    return run(["prompt", "--dir", str(store), "--observation", obs,
                "--no-stall-notice"]).stdout


def test_brief_after_leaves_the_early_prompt_alone(store):
    assert brief(store, 3) == full(store)


def test_brief_after_shortens_the_prompt_once_the_window_passes(store):
    for i in range(3):
        assert patch(store, {"round": i + 1}).returncode == 0
    assert len(brief(store, 3)) < len(full(store)) * 0.75


def test_brief_prompt_still_carries_sigma_and_the_observation(store):
    for i in range(3):
        patch(store, {"round": i + 1})
    body = brief(store, 3, "shelf 12 low")
    assert "shelf 12 low" in body
    assert json.dumps(sigma(store), separators=(",", ":"), sort_keys=True) in body


def test_brief_prompt_still_carries_the_echo_values(store):
    for i in range(3):
        patch(store, {"round": i + 1})
    body = brief(store, 3)
    cur = json.loads(run(["ids", "--dir", str(store)]).stdout)
    assert cur["obs_ref"] in body
    assert cur["state_hash"] in body


def test_brief_prompt_still_names_the_patchable_fields(store):
    for i in range(3):
        patch(store, {"round": i + 1})
    body = brief(store, 3)
    for field in SCHEMA["fields"]:
        assert field in body, f"{field} must stay in the allowlist"


def test_brief_prompt_still_demands_the_json_block(store):
    for i in range(3):
        patch(store, {"round": i + 1})
    body = brief(store, 3)
    assert "```json" in body
    assert "state_patch" in body and "action" in body


def test_brief_prompt_drops_the_teaching_blocks(store):
    for i in range(3):
        patch(store, {"round": i + 1})
    body = brief(store, 3)
    assert "deep-merges, siblings you omit are kept" not in body
    assert "you had one turn to capture it and did not" not in body


def test_brief_prompt_keeps_the_merge_rules_in_one_line(store):
    for i in range(3):
        patch(store, {"round": i + 1})
    body = brief(store, 3)
    assert "$append" in body and "null" in body


def test_brief_after_zero_never_shortens(store):
    for i in range(5):
        patch(store, {"round": i + 1})
    assert brief(store, 0) == full(store)


def test_a_patch_built_from_the_brief_prompt_is_accepted(store):
    for i in range(3):
        patch(store, {"round": i + 1})
    brief(store, 3)
    cur = json.loads(run(["ids", "--dir", str(store)]).stdout)
    res = send(store, {"state_patch": {"cursor": "svc-09"}, "action": "x",
                       "obs_ref": cur["obs_ref"], "state_hash": cur["state_hash"]})
    assert res.returncode == 0, res.stderr


def test_brief_and_no_state_hash_compose(tmp_path):
    d = tmp_path / "nh"
    run(["init", "--dir", str(d), "--schema", json.dumps(SCHEMA), "--no-state-hash"])
    for i in range(3):
        cur = json.loads(run(["ids", "--dir", str(d)]).stdout)
        send(d, {"state_patch": {"round": i + 1}, "action": "x",
                 "obs_ref": cur["obs_ref"]})
    body = run(["prompt", "--dir", str(d), "--observation", "o",
                "--brief-after", "3", "--no-stall-notice"]).stdout
    assert "state_hash" not in body
    assert "three keys" in body


# ---------------------------------------------------------------------------
# the worked $append example must be legal under the schema in hand
#
# CONTRACT_FULL shipped a hardcoded example using `cmd_summary`. The warehouse
# schema declares cmd_summary as a str. Measured mid-run at T=100: the full
# contract produced 6 $append attempts on that str field across 96 patches,
# every one rejected; the brief contract, which carries no worked example,
# produced none in 74. The prompt was teaching the one move the runtime
# forbids.
# ---------------------------------------------------------------------------

LIST_SCHEMA = {"fields": {"notes": "list", "cursor": "str"}}
NO_LIST_SCHEMA = {"fields": {"summary": "str", "seen": "dict"}}


def store_with(tmp_path: Path, schema: dict, name: str = "st") -> Path:
    d = tmp_path / name
    res = run(["init", "--dir", str(d), "--schema", json.dumps(schema)])
    assert res.returncode == 0, res.stderr
    return d


def test_append_example_names_a_list_field_from_this_schema(tmp_path):
    d = store_with(tmp_path, LIST_SCHEMA)
    body = run(["prompt", "--dir", str(d), "--observation", "o",
                "--no-stall-notice"]).stdout
    assert '"notes": {"$append"' in body


def test_append_example_never_names_a_non_list_field(tmp_path):
    d = store_with(tmp_path, LIST_SCHEMA)
    body = run(["prompt", "--dir", str(d), "--observation", "o",
                "--no-stall-notice"]).stdout
    assert '"cursor": {"$append"' not in body


def test_no_append_example_when_the_schema_has_no_list(tmp_path):
    d = store_with(tmp_path, NO_LIST_SCHEMA)
    body = run(["prompt", "--dir", str(d), "--observation", "o",
                "--no-stall-notice"]).stdout
    assert '"summary": {"$append"' not in body
    assert '"seen": {"$append"' not in body


def test_schema_without_a_list_still_explains_append(tmp_path):
    """The operator still exists; it just has nothing here to demonstrate on."""
    d = store_with(tmp_path, NO_LIST_SCHEMA)
    body = run(["prompt", "--dir", str(d), "--observation", "o",
                "--no-stall-notice"]).stdout
    assert "$append" in body


def test_the_example_in_the_prompt_is_actually_accepted(tmp_path):
    """Whatever field the prompt demonstrates on, a patch to it must pass."""
    d = store_with(tmp_path, LIST_SCHEMA)
    body = run(["prompt", "--dir", str(d), "--observation", "o",
                "--no-stall-notice"]).stdout
    m = re.search(r'For example, \{"state_patch": \{"(\w+)": \{"\$append"', body)
    assert m, "no worked example found in the prompt"
    cur = json.loads(run(["ids", "--dir", str(d)]).stdout)
    res = send(d, {"state_patch": {m.group(1): {"$append": ["x"]}},
                   "action": "a", "obs_ref": cur["obs_ref"],
                   "state_hash": cur["state_hash"]})
    assert res.returncode == 0, f"the prompt demonstrates an illegal patch: {res.stderr}"


def test_warehouse_schema_gets_no_append_example(tmp_path):
    """The schema that triggered this: dict + str, no list anywhere."""
    wh = json.loads((SCRIPT.parents[1] / "references" / "schemas"
                     / "warehouse.json").read_text())
    d = store_with(tmp_path, wh)
    body = run(["prompt", "--dir", str(d), "--observation", "o",
                "--no-stall-notice"]).stdout
    assert '"cmd_summary": {"$append"' not in body
