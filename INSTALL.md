# Installing Talos

Talos is a [Claude Code](https://claude.com/claude-code) skill: a folder with a `SKILL.md`
plus a small runtime script. There is no package to publish; you place the folder where Claude
Code looks for skills.

## Requirements

- Claude Code (any recent version with skill support)
- [`uv`](https://docs.astral.sh/uv/) on PATH. The runtime script is a `uv run --script`
  single-file script with zero external dependencies (stdlib only, Python ≥3.11), so `uv` only
  needs to find an interpreter, not resolve any packages.

## Personal install (all your projects)

```bash
git clone <this-repo-url> ~/.claude/skills/talos
```

Claude Code discovers skills under `~/.claude/skills/` automatically; no restart-and-hope step
beyond starting a fresh session.

## Project install (one repo only)

```bash
git clone <this-repo-url> .claude/skills/talos
```

Commit `.claude/skills/talos` to the repo if you want the whole team to have it.

## Verify it works

```bash
cd ~/.claude/skills/talos   # or wherever you cloned it
uv run --script scripts/skillstate.py --help
python3 -m pytest tests/ -q   # 27 tests, should all pass
```

## Using it

Say "Talos" or "Талос" on their own, or describe a long multi-step job (audit, migration,
backfill, long debug hunt, batch work, subagent-driven task). Claude Code will match the
skill's description and load `SKILL.md`. See the skill itself for the loop, the schema
catalogue in `references/schemas/`, and `references/paper-findings.md` for the research this
implements.

## Uninstalling

Delete the folder. The only other state left behind is whatever `.state-over-history/`
directories were created inside task working directories: plain JSON and JSONL, safe to
`rm -rf` per project once you no longer need the audit trail.
