"""Tests for the deterministic state runtime.

The runtime owns the schema and the merge. A malformed patch must never reach
Sigma. These tests pin the merge algebra the prompt template promises.
"""

import json
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


def patch(store: Path, body: dict):
    return run(["patch", "--dir", str(store)], stdin=json.dumps(body))


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
