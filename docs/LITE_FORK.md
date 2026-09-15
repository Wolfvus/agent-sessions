# Agent Sessions Lite

This fork keeps Agent Sessions' mature local parsers and transcript/search UX while reducing the trust boundary for a personal multi-profile workflow.

## Goals

- Keep upstream session parsers and parser tests intact where possible.
- Prioritize Codex, Claude and Antigravity.
- Support multiple isolated Codex homes (for example `work`, `personal`, `geo`).
- Organize history around projects/repos rather than only providers.
- Treat upstream session stores as read-only inputs.
- Avoid unnecessary network access, credential ingestion and command execution.

## Threat model

Session history can contain source code, filesystem paths, tool output, secrets accidentally printed by an agent, and other sensitive local data. Lite therefore treats the history reader as a privileged local application.

The desired steady state is:

```text
agent session stores (read only)
        ↓
local parsers
        ↓
local index/search
        ↓
SwiftUI
```

Features outside that boundary should be disabled or removed from the Lite product surface unless explicitly required.

### Remove/disable from Lite

- Claude web/cloud session-cookie features
- Codex account/quota API calls
- remote model-price fetches
- automatic update networking
- feedback submission networking
- Resume / terminal launching
- shell command execution
- CLI auth/status probes that spawn subprocesses
- IDE launch helpers
- archive restore or any feature that writes back into an agent's source store

Parser code may remain in-tree even when a provider is hidden. Keeping upstream adapters makes future parser fixes easier to merge.

## Multi-profile Codex: phase 1

Upstream currently assumes one effective Codex sessions root in several subsystems. Before changing the Swift runtime model, Lite provides `scripts/lite_codex_profiles.py`, which constructs a disposable federated session tree made only of symlinks.

The source Codex homes are never modified.

### Configure

```bash
python3 scripts/lite_codex_profiles.py init
```

Edit:

```text
~/.config/agent-sessions-lite/codex-profiles.json
```

Example:

```json
[
  {"name": "work", "home": "~/.codex-work"},
  {"name": "personal", "home": "~/.codex-personal"},
  {"name": "geo", "home": "~/.codex-geo"}
]
```

The example paths are placeholders. Use the actual `CODEX_HOME` values used by your shell aliases/functions.

Then:

```bash
python3 scripts/lite_codex_profiles.py sync
```

Set Agent Sessions' **Codex custom sessions root** to the `.../CodexFederated/sessions` path printed by the command.

Check it later with:

```bash
python3 scripts/lite_codex_profiles.py status
```

Re-run `sync` after new sessions are created. A native watcher/profile registry is phase 2.

## Multi-profile Codex: phase 2

The native implementation should introduce a profile registry without changing Codex transcript parsing:

```text
CodexProfile
- id
- label
- codexHome / sessionsRoot
- enabled
```

Session provenance should be inferred from the containing configured root and surfaced as `Codex · Work`, `Codex · Personal`, etc. The parser itself should continue receiving a file URL exactly as upstream does.

Project mapping remains independent of profile:

```text
session
  → recorded cwd/workspace
  → canonical path
  → git root when available
  → project
```

A missing/moved cwd must not make the session disappear; it should remain visible under an unresolved project bucket.

## Upstream strategy

Keep this fork easy to rebase:

- prefer composition/gating changes over parser rewrites
- avoid deleting unused provider adapters initially
- keep source-format fixtures/tests
- isolate Lite-only UI and policy changes
- periodically merge/rebase `jazzyalex/agent-sessions` and resolve policy differences explicitly

## Safety invariant

The Lite app should be useful with networking unavailable and without permission to execute external commands.
