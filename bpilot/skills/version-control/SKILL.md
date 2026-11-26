---
name: version-control
description: Repo-specific branch and commit-message conventions for backports. Used by bpilot to name backport branches and to validate commit message style when porting.
---
## Branch Conventions
- Active development branch: `edge`
- Release branch: `8.4/edge`
- Backport branch naming convention: <!-- e.g. `backport/<hash>-to-<target>` — no backport branches found in history; confirm with maintainers -->

## Commit Conventions
- Ticket-prefixed commits use the format `[DPE-XXXX] <subject> (#PR)` (e.g. `[DPE-10154] 8.4 - address tiobe issues in router (#199)`)
- Non-ticket commits use the prefix `[MISC]` (e.g. `[MISC] Improve \`test_upgrade.py\` resiliency (#215)`)
- Some commits use conventional-commit prefixes (`fix:`, `chore:`) but this is not the dominant style
- Pull-request number is appended in parentheses at the end of the subject (e.g. `(#199)`)
- Some commits include the target branch/version inline in the subject (e.g. `(8.4/edge)`, `8.4 -`)
- No sign-off requirement is evident from the commit history
