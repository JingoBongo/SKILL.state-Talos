#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Deterministic execution-state runtime.

The model proposes patches; this script owns the schema, the merge and the
journal. A malformed or off-schema patch is rejected and Sigma is left exactly
as it was, so a bad generation can never corrupt state. A patch that is valid
but pushes one field over its byte ceiling drops only that field. The other
fields in the same patch still apply (see check_limits/cmd_patch).

Merge algebra (the prompt template promises exactly this):
  scalar        -> replace
  dict          -> deep merge, recursively
  list          -> replace wholesale
  {"$append":L} -> extend an existing list
  null          -> delete the key
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

TYPES: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "float": (float, int),
    "bool": (bool,),
    "list": (list,),
    "dict": (dict,),
}
DEFAULTS: dict[str, Any] = {
    "str": "",
    "int": 0,
    "float": 0.0,
    "bool": False,
    "list": [],
    "dict": {},
}
APPEND = "$append"
HASH_PREFIX = 8
KEY_SIMILARITY = 0.85


class PatchRejected(Exception):
    """A patch failed validation. Sigma is untouched."""


class Store:
    """On-disk home of one execution state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "state.json"
        self.schema_path = root / "schema.json"
        self.journal_path = root / "patches.jsonl"
        self.obs_count_path = root / "obs_count"

    def steps_done(self) -> int:
        """Accepted patches so far. The step in flight is this plus one."""
        if self.obs_count_path.exists():
            return int(self.obs_count_path.read_text().strip())
        return 0

    def current_obs_id(self) -> str:
        """The id a patch must echo: the step in flight, not the render count.

        Rendering a prompt is not an event. A caller may render twice (to log
        it, to retry a transport error, to look at it) and the id the model
        echoed stays valid. Advancing this on render is what broke the
        correction loop, whose whole job is to re-ask about the same step.
        """
        return f"obs#{self.steps_done() + 1}"

    def advance_step(self) -> None:
        """Called once, after a patch is accepted and merged."""
        self.obs_count_path.write_text(str(self.steps_done() + 1))

    def state_hash(self) -> str:
        """sha256 of the CANONICAL re-serialization of Sigma.

        state.json is written with indent=2, so the raw file bytes are never
        hashed — only the canonical form, which is stable across writers.
        """
        sigma = self.read()
        canonical = json.dumps(sigma, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def state_hash_short(self) -> str:
        """What the prompt shows and the model echoes.

        The full digest is 64 hex characters to retype on every single step,
        by a model this project measured corrupting long literals (9C174346
        came back as 9c174346, the middle of a hex string simply vanished).
        That is a failure mode bought for nothing: eight characters identify
        the state well enough for a single-writer runtime.
        """
        return self.state_hash()[:HASH_PREFIX]

    def hash_matches(self, sent: str) -> bool:
        """Accept the short form, and the full digest from older callers."""
        full = self.state_hash()
        return sent == full[:HASH_PREFIX] or sent == full

    def require(self) -> None:
        if not self.state_path.exists():
            raise SystemExit(
                f"no state at {self.root}: run `skillstate.py init --schema <file>` first"
            )

    @property
    def schema(self) -> dict[str, str]:
        return json.loads(self.schema_path.read_text())["fields"]

    @property
    def uses_state_hash(self) -> bool:
        raw = json.loads(self.schema_path.read_text())
        return raw.get("state_hash", True) if isinstance(raw, dict) else True

    @property
    def key_discipline(self) -> list[str]:
        raw = json.loads(self.schema_path.read_text())
        return raw.get("key_discipline", []) if isinstance(raw, dict) else []

    @property
    def limits(self) -> dict[str, int]:
        """Optional per-field byte ceilings. Absent means unbounded."""
        return json.loads(self.schema_path.read_text()).get("limits", {})

    def read(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text())

    def write(self, sigma: dict[str, Any]) -> None:
        """Atomic replace: a failed write leaves the previous Sigma intact."""
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(sigma, fh, indent=2, sort_keys=True)
            if self.state_path.exists() and not os.access(self.state_path, os.W_OK):
                raise PermissionError(f"{self.state_path} is not writable")
            os.replace(tmp, self.state_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def log(self, patch: dict[str, Any], accepted: bool, reason: str = "") -> None:
        entry = {
            "ts": time.time(),
            "accepted": accepted,
            "patch": patch,
            "reason": reason,
        }
        with self.journal_path.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def accepted_patches(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        out = []
        for line in self.journal_path.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("accepted"):
                out.append(entry["patch"])
        return out

    def raw_entries(self) -> list[dict[str, Any]]:
        """Every journal entry, accepted or rejected, in order."""
        if not self.journal_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.journal_path.read_text().splitlines()
            if line.strip()
        ]


def seed(schema: dict[str, str]) -> dict[str, Any]:
    return {k: DEFAULTS[v] for k, v in schema.items()}


def coerce_scalar_appends(patch: dict, schema: dict[str, str]) -> dict:
    """Wrap a bare `$append` value in a one-element list.

    Measured: 116 of the 124 patch rejections in the S-warehouse run were a
    model writing {"notes": {"$append": "one note"}}. Appending a scalar has
    exactly one meaning, and charging a correction round-trip for the
    brackets buys nothing. A non-list FIELD still rejects: the complaint
    there is the field's declared type, not the shape of the value.

    Returns a new patch; the caller journals the coerced form, so the log
    shows what was actually merged.
    """
    out = {}
    for key, value in patch.items():
        if (isinstance(value, dict) and len(value) == 1 and APPEND in value
                and not isinstance(value[APPEND], list)
                and schema.get(key) == "list"):
            out[key] = {APPEND: [value[APPEND]]}
        else:
            out[key] = value
    return out


def validate(patch: Any, schema: dict[str, str]) -> None:
    """Reject anything the schema does not own, before touching Sigma."""
    if not isinstance(patch, dict):
        raise PatchRejected("patch must be a JSON object")
    for key, value in patch.items():
        if key not in schema:
            raise PatchRejected(
                f"unknown key {key!r}: schema owns {sorted(schema)}. "
                "Extend the schema deliberately; do not invent fields mid-run."
            )
        if value is None:
            continue
        declared = schema[key]
        if isinstance(value, dict) and APPEND in value:
            if declared != "list":
                raise PatchRejected(f"{key!r} is {declared}, {APPEND} needs a list")
            if not isinstance(value[APPEND], list):
                raise PatchRejected(f"{key!r}: {APPEND} takes a list")
            if len(value) != 1:
                raise PatchRejected(f"{key!r}: {APPEND} must be the only key")
            continue
        if declared == "bool" and not isinstance(value, bool):
            raise PatchRejected(f"{key!r} expects bool, got {type(value).__name__}")
        if declared in ("int", "float") and isinstance(value, bool):
            raise PatchRejected(f"{key!r} expects {declared}, got bool")
        if not isinstance(value, TYPES[declared]):
            raise PatchRejected(
                f"{key!r} expects {declared}, got {type(value).__name__}"
            )


def _type_name(value: Any) -> str:
    """Render a Python value as the contract type name (bool before int)."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _type_compatible(current: Any, new: Any) -> bool:
    """Is `new` acceptable where `current` sits? (bool checked before int.)"""
    if isinstance(current, bool):
        return isinstance(new, bool)
    if isinstance(current, (int, float)):
        return isinstance(new, (int, float)) and not isinstance(new, bool)
    if isinstance(current, str):
        return isinstance(new, str)
    if isinstance(current, list):
        return isinstance(new, list)
    if isinstance(current, dict):
        return isinstance(new, dict)
    return True


def _walk_contract(patch_dict: dict, current_dict: dict, path: str) -> str | None:
    """Recursively check nested types; return a violation message or None."""
    for subkey, new_val in patch_dict.items():
        if subkey not in current_dict:
            continue  # new subkey: no contract yet
        cur_val = current_dict[subkey]
        if new_val is None:
            continue  # null deletes: no type check
        subpath = f"{path}.{subkey}"
        if not _type_compatible(cur_val, new_val):
            return (
                f'state key "{subpath}" expected {_type_name(cur_val)}, '
                f"received {_type_name(new_val)}"
            )
        if isinstance(cur_val, dict) and isinstance(new_val, dict):
            err = _walk_contract(new_val, cur_val, subpath)
            if err:
                return err
    return None


def check_key_discipline(patch: dict, current: dict,
                         policed: list[str]) -> str | None:
    """Reject a dict key that is a near-miss of one already there.

    A dict with free keys is a transcript with extra steps: the model writes
    api-notes, then api_note, then api_notes, nothing is ever overwritten and
    the field grows until it hits its ceiling. Measured on 234 CTF stores
    before this existed: 7% of the bytes in the freeform field were
    near-duplicates of something already written.

    Normalising the key silently would be worse than rejecting it, because the
    model would never learn which key it is meant to patch. So: reject, and
    name the key it should have used.
    """
    for field in policed:
        incoming = patch.get(field)
        if not isinstance(incoming, dict):
            continue
        existing = current.get(field)
        if not isinstance(existing, dict) or not existing:
            continue
        for new_key in incoming:
            if new_key in existing or new_key == APPEND:
                continue
            for old_key in existing:
                if _looks_like_a_path(new_key) or _looks_like_a_path(old_key):
                    continue  # the filesystem is the authority, not a ratio
                if difflib.SequenceMatcher(None, _key_norm(new_key),
                                           _key_norm(old_key)).ratio() >= KEY_SIMILARITY:
                    return (
                        f'key "{new_key}" in "{field}" is too close to the existing '
                        f'"{old_key}". Patch "{old_key}" instead, or pick a name that '
                        "is clearly a different thing."
                    )
    return None


def _key_norm(k: str) -> str:
    """Compare keys on their letters, so -, _ and case cannot fork a key."""
    return "".join(c for c in str(k).lower() if c.isalnum())


def _looks_like_a_path(k: str) -> bool:
    """Does the world already guarantee this key is distinct?

    A filesystem has settled the question before the model ever gets there, so
    fuzzy matching has nothing to add and plenty to break: measured in the P3
    run, dds1-alpine.flag.img.gz and dds1-alpine.flag.img are 0.95 similar as
    strings and are an archive and the image inside it.
    """
    k = str(k)
    return "/" in k or "." in k


def check_type_contracts(patch: dict, current: dict) -> str | None:
    """Check NESTED values inside dict fields against the CURRENT state.

    Top-level declared types are already enforced by validate(); this is a
    second layer that infers the contract for subkeys from what is already
    there. Returns a violation message, or None if the patch is clean.
    """
    for key, value in patch.items():
        if not isinstance(value, dict):
            continue
        if APPEND in value:
            # $append used to skip the contract entirely, so a dict could be
            # appended into a list of strings unchecked. Only a homogeneous
            # existing list carries a contract worth enforcing.
            existing = current.get(key)
            items = value.get(APPEND)
            if not isinstance(existing, list) or not existing or not isinstance(items, list):
                continue
            if len({_type_name(x) for x in existing}) != 1:
                continue
            for item in items:
                if not _type_compatible(existing[0], item):
                    return (
                        f'state key "{key}[]" expected {_type_name(existing[0])}, '
                        f"received {_type_name(item)}"
                    )
            continue
        if key not in current or not isinstance(current[key], dict):
            continue
        err = _walk_contract(value, current[key], key)
        if err:
            return err
    return None


def merge(sigma: Any, patch: Any) -> Any:
    """Deep dict merge with null-deletion and explicit list append."""
    if not isinstance(patch, dict):
        return patch
    if APPEND in patch and len(patch) == 1:
        base = sigma if isinstance(sigma, list) else []
        return [*base, *patch[APPEND]]
    if not isinstance(sigma, dict):
        sigma = {}
    out = dict(sigma)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict):
            out[key] = merge(out.get(key), value)
        else:
            out[key] = value
    return out


def check_limits(sigma: dict[str, Any], limits: dict[str, int]) -> None:
    """Enforce byte ceilings on the merged state.

    Checked after the merge, not on the patch: two blobs that each fit can
    still overflow the field together, and that is exactly the case a blob
    cache has to catch. Evict with null to make room.
    """
    for field, ceiling in limits.items():
        if field not in sigma:
            continue
        size = len(json.dumps(sigma[field], separators=(",", ":")))
        if size > ceiling:
            raise PatchRejected(
                f"{field!r} would be {size} bytes, over its {ceiling}-byte limit. "
                f"Delete something from {field!r} with null in the same patch."
            )


def stall_notice(store: Store, window: int = 5, reject_streak: int = 3) -> str | None:
    """Advisory only: never blocks anything, never decides for the model.

    Two independent signals, both computed from the journal alone:
      - the last `reject_streak` attempts were all rejected
      - Sigma is byte-identical to what it was `window` accepted patches ago
    Either means the runtime hasn't recorded new information in a while. That
    can be a real stall (looping, re-deriving the same fact) or a normal
    stretch of legitimate rejections/no-op steps - the model decides which.
    """
    entries = store.raw_entries()
    if len(entries) >= reject_streak:
        tail = entries[-reject_streak:]
        if all(not e.get("accepted") for e in tail):
            return (
                f"Notice: the last {reject_streak} patches were all rejected "
                f"(see `journal -n {reject_streak}` for why). Sigma has not "
                "gained anything from these attempts - worth checking whether "
                "the approach itself is wrong before trying again."
            )

    patches = store.accepted_patches()
    if len(patches) >= window:
        schema = store.schema
        sigma_then = seed(schema)
        for patch in patches[:-window]:
            sigma_then = merge(sigma_then, patch)
        if sigma_then == store.read():
            return (
                f"Notice: Sigma has not changed in the last {window} accepted "
                "patches - each one merged to the same state as before. If "
                "you are re-deriving something you already found, it may not "
                "have been committed; if this is expected (e.g. confirming a "
                "hypothesis), disregard."
            )
    return None


# The merge contract, taught in full. Costs ~1300 bytes of prompt, every
# step, forever.
CONTRACT_FULL = """State patch semantics — the runtime applies your patch exactly like this:
  scalar or list value      replaces what is there
  nested object             deep-merges, siblings you omit are kept
  {{"field": {{"$append": [...]}}}}  extends that field's list without resending it
  null                      deletes that key
"$append" is never a patch key on its own. It is always the VALUE of one of the
fields below.{append_example}
A bare {{"$append": [...]}} at the top level is rejected as an unknown field.
You never need to resend unchanged keys. An off-schema key is rejected and the
whole patch is discarded, so patch only these fields: {fields}

Nothing outside Sigma survives to the next step — this observation will not be
shown to you again after this turn. If any detail here might matter later (a
value, a location, a fact), patch it into a persistent field now (a list via
{{"<field>": {{"$append": [...]}}}}, or a dict key) rather than relying on memory or a scalar
field you are about to overwrite. Before choosing your next action: if it
repeats a command you already ran, or re-reads something you already read, you
have already lost that result — you had one turn to capture it and did not.
Extract what mattered from the observation above and commit it now, or you will
be stuck re-discovering the same thing every turn."""

# The same rules, compressed to what a model that has already seen them N
# times needs as a reminder. Output is ~25x the price of input on the
# benchmark model, and a prompt that re-opens the contract every step
# invites the model to re-reason about it: measured 4140 output tokens per
# step against a transcript control's 1090, with zero rejections.
def append_example(schema: dict[str, str]) -> str:
    """A worked `$append` on a field THIS schema declares as a list.

    The example used to be hardcoded on `cmd_summary`, which the warehouse
    schema declares as a str. Measured at T=100: the full contract produced
    six $append attempts on that str field across 96 patches, every one
    rejected, while the brief contract (no worked example) produced none in
    74. The prompt was demonstrating the one move the runtime forbids.

    A schema with no list field gets no example. The operator is still
    explained; there is just nothing here to demonstrate it on.
    """
    lists = sorted(k for k, v in schema.items() if v == "list")
    if not lists:
        return ""
    return (' For example, {"state_patch": {"%s": {"$append": '
            '["found X at line 12"]}}}.' % lists[0])


CONTRACT_BRIEF = """Patch rules (as before): a value replaces, a nested object deep-merges,
{{"field": {{"$append": [...]}}}} extends a list, null deletes. Patch only these
fields: {fields}. Nothing outside Sigma survives this turn."""


PROMPT = """Instructions:

{instructions}

Skill Execution State:
```json
{sigma}
```

state_hash: {state_hash}

Latest Observation ({obs_id}): {observation}

{contract_block}

Provide your response with:
1. A JSON block fenced with ```json ... ``` containing both your State Patch
   and your Action, output FIRST so it is never cut off if you run long. The
   JSON block MUST have exactly these four keys:
{{ "state_patch": {{ <dict: your state updates, set keys to null to delete> }},
  "action": {action_contract},
  "obs_ref": "<the observation id from this prompt, verbatim, e.g. obs#7>",
  "state_hash": "<the state_hash from this prompt, verbatim>" }}
   where "obs_ref" is the observation id from this prompt verbatim (e.g.
   "obs#7") and "state_hash" is the hash from this prompt verbatim. The
   runtime rejects the patch if either is stale, and the prompt above is the
   authoritative source — re-derive the patch from it.
2. Step-by-step reasoning after the JSON block, if you want it (discarded,
   never read).
"""


def cmd_init(args, store: Store) -> int:
    raw = Path(args.schema).read_text() if Path(args.schema).exists() else args.schema
    parsed = json.loads(raw)
    schema = parsed["fields"]
    limits = parsed.get("limits", {})
    policed = parsed.get("key_discipline", [])
    use_hash = not getattr(args, "no_state_hash", False)
    for name, declared in schema.items():
        if declared not in TYPES:
            raise SystemExit(f"field {name!r}: unknown type {declared!r}")
    for name in limits:
        if name not in schema:
            raise SystemExit(f"limit set for unknown field {name!r}")
    for name in policed:
        if schema.get(name) != "dict":
            raise SystemExit(
                f"key_discipline names {name!r}, which is not a dict field"
            )
    if store.state_path.exists() and not args.force:
        raise SystemExit(f"state already exists at {store.root}: pass --force to reset")
    store.root.mkdir(parents=True, exist_ok=True)
    store.schema_path.write_text(
        json.dumps({"fields": schema, "limits": limits,
                    "key_discipline": policed, "state_hash": use_hash}, indent=2)
    )
    store.write(seed(schema))
    store.journal_path.write_text("")
    if args.force:
        store.obs_count_path.unlink(missing_ok=True)
    print(f"initialised {store.root} with fields: {', '.join(sorted(schema))}")
    return 0


def cmd_show(args, store: Store) -> int:
    store.require()
    sigma = store.read()
    if args.pretty:
        print(json.dumps(sigma, indent=2, sort_keys=True))
    else:
        print(json.dumps(sigma, separators=(",", ":"), sort_keys=True))
    return 0


def cmd_ids(args, store: Store) -> int:
    """The echo values a patch must carry, as the runtime computes them.

    A caller that derives these itself is one refactor away from a harness
    that rejects every patch (or, worse, accepts a stale one). Ask instead.
    """
    store.require()
    full = store.state_hash()
    print(json.dumps({
        "obs_ref": store.current_obs_id(),
        "state_hash": store.state_hash_short() if store.uses_state_hash else None,
        "state_hash_full": full,
        "steps_done": store.steps_done(),
        "uses_state_hash": store.uses_state_hash,
    }, sort_keys=True))
    return 0


def cmd_patch(args, store: Store) -> int:
    store.require()
    raw = sys.stdin.read()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        store.log({"_raw": raw[:2000]}, False, f"malformed JSON: {exc}")
        print(f"patch rejected, malformed JSON: {exc}", file=sys.stderr)
        return 2

    # Unwrap the full response wrapper. A bare state-patch dict (no wrapper)
    # is still accepted, but the obs_ref/state_hash checks below still apply.
    obs_ref = None
    state_hash_sent = None
    if isinstance(body, dict) and "state_patch" in body:
        obs_ref = body.get("obs_ref")
        state_hash_sent = body.get("state_hash")
        patch = body["state_patch"]
    else:
        patch = body

    # Coerce before validating, so the journal and every later check see the
    # one form the merge will apply.
    if isinstance(patch, dict):
        patch = coerce_scalar_appends(patch, store.schema)

    try:
        validate(patch, store.schema)
    except PatchRejected as exc:
        store.log(patch, False, str(exc))
        print(f"patch rejected: {exc}", file=sys.stderr)
        return 2

    # Nested type contracts: reject the whole patch if a nested value's type
    # contradicts what is already in Sigma at the same path.
    violation = check_type_contracts(patch, store.read())
    if violation:
        store.log(patch, False, violation)
        print(f"patch rejected: {violation}", file=sys.stderr)
        return 2

    drift = check_key_discipline(patch, store.read(), store.key_discipline)
    if drift:
        store.log(patch, False, drift)
        print(f"patch rejected: {drift}", file=sys.stderr)
        return 2

    # Staleness is checked last, on purpose. A patch can be both stale and
    # malformed; the malformed half is wrong either way and is the half the
    # model can act on, so it is reported first. Staleness still blocks the
    # merge, it just stops masking every substantive error behind a clerical
    # one and burning a correction round on it.
    current_obs = store.current_obs_id()
    if obs_ref is None:
        msg = (
            "patch rejected: missing obs_ref — echo the observation id from "
            f"the prompt verbatim (current: {current_obs})"
        )
        store.log(patch, False, msg)
        print(msg, file=sys.stderr)
        return 2
    if obs_ref != current_obs:
        msg = (
            f"patch rejected: obs_ref mismatch — you cited '{obs_ref}', "
            f"current observation is '{current_obs}'; the patch may be derived "
            "from a stale observation. Re-derive it from the latest "
            "observation in the prompt."
        )
        store.log(patch, False, msg)
        print(msg, file=sys.stderr)
        return 2

    if not store.uses_state_hash:
        return _apply(args, store, patch)

    current_hash = store.state_hash_short()
    if state_hash_sent is None:
        msg = (
            "patch rejected: missing state_hash — echo the state hash from "
            f"the prompt verbatim (current: {current_hash})"
        )
        store.log(patch, False, msg)
        print(msg, file=sys.stderr)
        return 2
    if not store.hash_matches(state_hash_sent):
        msg = (
            f"patch rejected: state_hash mismatch — you sent "
            f"'{state_hash_sent}', current state hash is '{current_hash}'; "
            "the state changed since you read it. Re-read the state in the "
            "prompt and re-derive the patch."
        )
        store.log(patch, False, msg)
        print(msg, file=sys.stderr)
        return 2

    return _apply(args, store, patch)


def _apply(args, store: Store, patch: dict) -> int:
    """Merge a validated patch, honour per-field limits, journal, advance."""
    # Byte limits are enforced per top-level field, not on the patch as a
    # whole: a field that would overflow is dropped on its own, so an
    # unrelated valid update in the same patch is not collateral damage.
    limits = store.limits
    current = store.read()
    applied: dict[str, Any] = {}
    dropped: dict[str, str] = {}
    for key, value in patch.items():
        candidate = merge(current, {key: value})
        field_limit = {key: limits[key]} if key in limits else {}
        try:
            check_limits(candidate, field_limit)
        except PatchRejected as exc:
            dropped[key] = str(exc)
            continue
        current = candidate
        applied[key] = value

    reason = "; ".join(f"{k}: {v}" for k, v in dropped.items())
    if not applied:
        store.log(patch, False, reason or "empty patch")
        print(f"patch rejected: {reason or 'empty patch'}", file=sys.stderr)
        return 2

    try:
        store.write(current)
    except OSError as exc:
        store.log(patch, False, f"write failed: {exc}")
        print(f"patch rejected, write failed: {exc}", file=sys.stderr)
        return 3
    store.advance_step()
    store.log(applied, True, reason)
    if dropped:
        print(f"patch partially applied, dropped {reason}", file=sys.stderr)
    return cmd_show(args, store)



def cmd_journal(args, store: Store) -> int:
    store.require()
    lines = [x for x in store.journal_path.read_text().splitlines() if x.strip()]
    for i, line in enumerate(lines[-args.n :] if args.n else lines, start=1):
        entry = json.loads(line)
        mark = "ok  " if entry["accepted"] else "REJ "
        detail = "" if entry["accepted"] else f"  <- {entry['reason']}"
        print(f"{i:>4} {mark}{json.dumps(entry['patch'], sort_keys=True)}{detail}")
    return 0


def cmd_rollback(args, store: Store) -> int:
    store.require()
    patches = store.accepted_patches()
    if args.to > len(patches):
        raise SystemExit(f"cannot roll back to {args.to}: only {len(patches)} accepted")
    sigma = seed(store.schema)
    for patch in patches[: args.to]:
        sigma = merge(sigma, patch)
    store.write(sigma)
    store.log({"_rollback_to": args.to}, True)
    print(f"rolled back to accepted patch {args.to}")
    return 0


DEFAULT_ACTION_CONTRACT = '"<string: the exact next step you want executed>"'


def cmd_prompt(args, store: Store) -> int:
    store.require()
    schema = store.schema
    instructions = (
        Path(args.instructions).read_text()
        if args.instructions and Path(args.instructions).exists()
        else (args.instructions or "{skill.instructions}")
    )
    contract = getattr(args, "action_contract", None) or DEFAULT_ACTION_CONTRACT
    if contract and Path(contract).exists():
        contract = Path(contract).read_text().strip()
    fields = ", ".join(f"{k} ({v})" for k, v in sorted(schema.items()))
    # Contract decay: teach it in full while it is still new, then remind.
    brief_after = getattr(args, "brief_after", 0) or 0
    use_brief = brief_after > 0 and store.steps_done() >= brief_after
    contract_block = (CONTRACT_BRIEF if use_brief else CONTRACT_FULL).format(
        fields=fields, append_example=append_example(schema))
    rendered = PROMPT.format(
        action_contract=contract,
        contract_block=contract_block,
        instructions=instructions.strip(),
        sigma=json.dumps(store.read(), separators=(",", ":"), sort_keys=True),
        state_hash=store.state_hash_short(),
        obs_id=store.current_obs_id(),
        observation=args.observation or "(none yet — this is the first step)",
        fields=fields,
    )
    if not store.uses_state_hash:
        # Strip both the value and the instruction to echo it: asking a model to
        # quote a line that is not in its prompt is a rejection waiting to happen.
        rendered = rendered.replace(
            '  "obs_ref": "<the observation id from this prompt, verbatim, e.g. obs#7>",\n'
            '  "state_hash": "<the state_hash from this prompt, verbatim>" }',
            '  "obs_ref": "<the observation id from this prompt, verbatim, e.g. obs#7>" }')
        rendered = rendered.replace(
            '   where "obs_ref" is the observation id from this prompt verbatim (e.g.\n'
            '   "obs#7") and "state_hash" is the hash from this prompt verbatim. The\n',
            '   where "obs_ref" is the observation id from this prompt verbatim (e.g.\n'
            '   "obs#7"). The\n')
        rendered = rendered.replace("exactly these four keys:", "exactly these three keys:")
        rendered = "\n".join(
            l for l in rendered.split("\n") if not l.startswith("state_hash:")
        )

    hybrid_k = getattr(args, "hybrid_k", 0) or 0
    if hybrid_k > 0:
        step = store.steps_done() + 1
        keep = step < hybrid_k
        rendered += (
            f"\n\ntranscript_policy: {'keep' if keep else 'drop'}  (step {step} of K={hybrid_k})\n"
            + (
                "This episode is still short. Keep your prior observations in "
                "context as well as the state above: below the crossover a "
                "transcript is the cheaper carrier, and state is accumulating "
                "in parallel so nothing is lost when it takes over.\n"
                if keep else
                "Past the crossover. Drop your prior observations: from here the "
                "state above is the carrier, and anything not in it is gone.\n"
            )
        )

    notice = None if getattr(args, "no_stall_notice", False) else stall_notice(store)
    if notice:
        rendered = f"{rendered}\n{notice}"
    print(rendered)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skillstate")
    parser.add_argument(
        "--dir",
        default=".state-over-history",
        help="state directory (default: .state-over-history)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_: str):
        """Every subcommand also accepts --dir, so argument order never matters."""
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--dir", dest="dir_sub", default=None, help=argparse.SUPPRESS)
        return sp

    p = add("init", "create a state store from a schema")
    p.add_argument("--schema", required=True, help="path to schema JSON, or inline JSON")
    p.add_argument("--force", action="store_true", help="overwrite an existing store")
    p.add_argument("--no-state-hash", action="store_true",
                   help="do not show or require the state hash. On a single-writer "
                        "runtime obs_ref already covers staleness, and on a long "
                        "horizon the echo is paid once per step.")
    p.set_defaults(fn=cmd_init, pretty=False)

    p = add("show", "print Sigma")
    p.add_argument("--pretty", action="store_true")
    p.set_defaults(fn=cmd_show)

    p = add("ids", "print the obs_ref and state hash a patch must echo")
    p.set_defaults(fn=cmd_ids)

    p = add("patch", "validate and merge a patch from stdin")
    p.add_argument("--pretty", action="store_true")
    p.set_defaults(fn=cmd_patch)

    p = add("journal", "show the patch log, rejections included")
    p.add_argument("-n", type=int, default=0, help="show only the last N entries")
    p.set_defaults(fn=cmd_journal)

    p = add("rollback", "replay the journal to an earlier step")
    p.add_argument("--to", type=int, required=True)
    p.set_defaults(fn=cmd_rollback)

    p = add("prompt", "emit the step prompt with Sigma injected")
    p.add_argument("--observation", default="")
    p.add_argument("--instructions", default="", help="path to the task spec, or inline")
    p.add_argument("--brief-after", type=int, default=0, metavar="N",
                   help="after N accepted steps, compress the merge contract to "
                        "a one-line reminder. The model has seen it N times by "
                        "then, and re-teaching it costs output, which is the "
                        "expensive side.")
    p.add_argument("--no-stall-notice", action="store_true",
                   help="suppress the advisory stall notice")
    p.add_argument("--hybrid-k", type=int, default=0, metavar="K",
                   help="announce a transcript policy: keep prior observations "
                        "in context below step K, drop them at K and after. "
                        "0 (default) announces nothing. The measured crossover "
                        "is around 31 steps.")
    p.add_argument("--action-contract", default=None,
                   help="what shape the action takes, as JSON-ish text or a file path. "
                        "Defaults to a shell-command string. Set it when the caller "
                        "speaks tool calls rather than shell.")
    p.set_defaults(fn=cmd_prompt)

    args = parser.parse_args(argv)
    root = Path(args.dir_sub or args.dir)
    return args.fn(args, Store(root))


if __name__ == "__main__":
    sys.exit(main())
