#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Deterministic execution-state runtime.

The model proposes patches; this script owns the schema, the merge and the
journal. A malformed or off-schema patch is rejected and Sigma is left exactly
as it was, so a bad generation can never corrupt state. A patch that is valid
but pushes one field over its byte ceiling drops only that field — the other
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


class PatchRejected(Exception):
    """A patch failed validation. Sigma is untouched."""


class Store:
    """On-disk home of one execution state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_path = root / "state.json"
        self.schema_path = root / "schema.json"
        self.journal_path = root / "patches.jsonl"

    def require(self) -> None:
        if not self.state_path.exists():
            raise SystemExit(
                f"no state at {self.root} — run `skillstate.py init --schema <file>` first"
            )

    @property
    def schema(self) -> dict[str, str]:
        return json.loads(self.schema_path.read_text())["fields"]

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


def seed(schema: dict[str, str]) -> dict[str, Any]:
    return {k: DEFAULTS[v] for k, v in schema.items()}


def validate(patch: Any, schema: dict[str, str]) -> None:
    """Reject anything the schema does not own, before touching Sigma."""
    if not isinstance(patch, dict):
        raise PatchRejected("patch must be a JSON object")
    for key, value in patch.items():
        if key not in schema:
            raise PatchRejected(
                f"unknown key {key!r} — schema owns {sorted(schema)}. "
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


PROMPT = """Instructions:

{instructions}

Skill Execution State:
```json
{sigma}
```

Latest Observation: {observation}

State patch semantics — the runtime applies your patch exactly like this:
  scalar or list value      replaces what is there
  nested object             deep-merges, siblings you omit are kept
  {{"$append": [...]}}        extends an existing list without resending it
  null                      deletes that key
You never need to resend unchanged keys. An off-schema key is rejected and the
whole patch is discarded, so patch only these fields: {fields}

Nothing outside Sigma survives to the next step — this observation will not be
shown to you again after this turn. If any detail here might matter later (a
value, a location, a fact), patch it into a persistent field now (a list via
{{"$append": [...]}}, or a dict key) rather than relying on memory or a scalar
field you are about to overwrite. Before choosing your next action: if it
repeats a command you already ran, or re-reads something you already read, you
have already lost that result — you had one turn to capture it and did not.
Extract what mattered from the observation above and commit it now, or you will
be stuck re-discovering the same thing every turn.

Provide your response with:
1. A JSON block fenced with ```json ... ``` containing both your State Patch
   and your Action, output FIRST so it is never cut off if you run long. The
   JSON block MUST have exactly these two keys:
{{ "state_patch": {{ <dict: your state updates, set keys to null to delete> }},
  "action": "<string: the exact next step you want executed>" }}
2. Step-by-step reasoning after the JSON block, if you want it (discarded,
   never read).
"""


def cmd_init(args, store: Store) -> int:
    raw = Path(args.schema).read_text() if Path(args.schema).exists() else args.schema
    parsed = json.loads(raw)
    schema = parsed["fields"]
    limits = parsed.get("limits", {})
    for name, declared in schema.items():
        if declared not in TYPES:
            raise SystemExit(f"field {name!r}: unknown type {declared!r}")
    for name in limits:
        if name not in schema:
            raise SystemExit(f"limit set for unknown field {name!r}")
    if store.state_path.exists() and not args.force:
        raise SystemExit(f"state already exists at {store.root} — pass --force to reset")
    store.root.mkdir(parents=True, exist_ok=True)
    store.schema_path.write_text(
        json.dumps({"fields": schema, "limits": limits}, indent=2)
    )
    store.write(seed(schema))
    store.journal_path.write_text("")
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


def cmd_patch(args, store: Store) -> int:
    store.require()
    raw = sys.stdin.read()
    try:
        patch = json.loads(raw)
    except json.JSONDecodeError as exc:
        store.log({"_raw": raw[:2000]}, False, f"malformed JSON: {exc}")
        print(f"patch rejected — malformed JSON: {exc}", file=sys.stderr)
        return 2
    if isinstance(patch, dict) and "state_patch" in patch:
        patch = patch["state_patch"]
    try:
        validate(patch, store.schema)
    except PatchRejected as exc:
        store.log(patch, False, str(exc))
        print(f"patch rejected — {exc}", file=sys.stderr)
        return 2

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
        print(f"patch rejected — {reason or 'empty patch'}", file=sys.stderr)
        return 2

    try:
        store.write(current)
    except OSError as exc:
        store.log(patch, False, f"write failed: {exc}")
        print(f"patch rejected — write failed: {exc}", file=sys.stderr)
        return 3
    store.log(applied, True, reason)
    if dropped:
        print(f"patch partially applied — dropped {reason}", file=sys.stderr)
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


def cmd_prompt(args, store: Store) -> int:
    store.require()
    schema = store.schema
    instructions = (
        Path(args.instructions).read_text()
        if args.instructions and Path(args.instructions).exists()
        else (args.instructions or "{skill.instructions}")
    )
    print(
        PROMPT.format(
            instructions=instructions.strip(),
            sigma=json.dumps(store.read(), separators=(",", ":"), sort_keys=True),
            observation=args.observation or "(none yet — this is the first step)",
            fields=", ".join(f"{k} ({v})" for k, v in sorted(schema.items())),
        )
    )
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
    p.set_defaults(fn=cmd_init, pretty=False)

    p = add("show", "print Sigma")
    p.add_argument("--pretty", action="store_true")
    p.set_defaults(fn=cmd_show)

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
    p.set_defaults(fn=cmd_prompt)

    args = parser.parse_args(argv)
    root = Path(args.dir_sub or args.dir)
    return args.fn(args, Store(root))


if __name__ == "__main__":
    sys.exit(main())
