# Agent configuration, as used

Verbatim copies of the skill files from the `mattpocock-skills` Claude Code
plugin that were run during this build, taken from the user-scope install at
`~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/1.2.3/`
(plugin version 1.2.3, git commit `0ab1b63a410a03d3627979a109c8695de27af954`,
installed 2026-08-31). Nothing under `skills/` is edited. `plugin.json` is the
plugin's own manifest and `LICENSE` is its MIT licence.

This file is the only thing here that is not a copy.

| Skill | Used for | Where the output is |
| --- | --- | --- |
| `grill-with-docs` | The design session, 2026-09-01. Calls the two below. | `docs/ai-log.md` first entry |
| `grilling` | The interview itself: design tree, rounds, recommended answers. | `docs/ai-log.md` |
| `domain-modeling` (with `CONTEXT-FORMAT.md`, `ADR-FORMAT.md`) | Glossary and ADRs written as decisions crystallised; the 2026-09-02 ADR revision review. | `CONTEXT.md`, `docs/adr/` |
| `to-spec` | The spec, synthesised from the grilling. | `.scratch/location-verified-visits/spec.md` |
| `to-tickets` | Ten tracer-bullet issues with blocking edges. | `.scratch/location-verified-visits/issues/` |
| `handoff` | The end-of-design handoff. | `.scratch/location-verified-visits/handoff.md` |
| `setup-matt-pocock-skills` (with its seed templates) | Configured the local-markdown tracker, triage labels and domain-doc rules, once. | `docs/agents/`, the `## Agent skills` block in `CLAUDE.md` |
| `tdd` (with `tests.md`, `mocking.md`) | Issues 02 and 03 red-then-green; the seams-first rule on every issue. | `tests/`, issue comments |
| `code-review` | The two-axis review (Standards, Spec) after each issue. | Issue comments under `## Comments` and `## Review verdicts` |

The repository-level configuration those skills read is in the repo itself:
`CLAUDE.md`, `docs/agents/`, `CONTEXT.md`, `docs/adr/`,
`.claude/settings.local.json`. See `docs/ai-process.md` for the inventory.
