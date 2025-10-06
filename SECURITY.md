# Security & Secret Handling

## Incident: Committed Virtualenv Containing Potential Secret
A vendored virtual environment directory `.venv-clean/` was accidentally committed. GitHub secret scanning flagged a suspected Atlassian token inside a compiled/shared object embedded within that environment.

### Timeline
- Detection: GitHub secret scanning alert (Atlassian token pattern) in `.venv-clean/.../tokenizers*.so`.
- Verification: No plaintext secrets found in tracked source (`git grep` for token & base64 forms returned none). Issue isolated to committed build artifacts.
- Remediation (this branch):
  1. Added `.gitignore` to block virtualenvs & artifacts.
  2. Removed `.venv-clean` from index (HEAD no longer contains the directory).
  3. Rewrote repository history with `git filter-repo` to excise `.venv-clean` from all past commits.
  4. Force-pushed cleaned history.
  5. Added pre-commit hooks (`gitleaks` secret scan + hygiene checks).

### Required Actions for Collaborators
If you previously cloned the repository before this remediation:
```
git fetch origin
# Option A: fresh clone (recommended)
rm -rf prompt-voice && git clone <repo-url>
# Option B: hard reset existing clone
git checkout <main-branch>
git reset --hard origin/<main-branch>
```

### Secret Rotation
If the flagged token corresponds to a real credential (even if false positive), ensure it is revoked/rotated in its upstream system (Atlassian admin console). Document the rotation reference/ticket here:
- Rotation ticket: <ADD_JIRA_OR_TRACKING_ID>
- Status: <PENDING|COMPLETE>

## Preventive Controls
| Control | Status | Notes |
|---------|--------|-------|
| .gitignore excludes venvs | Enabled | Added root `.gitignore` |
| History rewrite completed | Yes | `.venv-clean` fully removed |
| Pre-commit secret scan | Enabled | `gitleaks` with redaction mode |
| Large file gate | Enabled | Blocks >500KB accidental commits |
| Documentation of process | This file | Included in repo root |

## Local Development: Safe Workflow
1. Create a fresh virtual environment locally (not in repo root, or name it `.venv/` which is ignored).
2. Keep secrets in a local `.env` file (not committed) or environment variables.
3. Run `pre-commit install` (already executed once) to ensure hooks run automatically.
4. Before pushing, optionally run a full scan:
```
pre-commit run --all-files
```

## Adding New Secrets
Use environment variables or a secrets manager. Never hardcode credentials or API tokens. For local examples, create `.env.example` with placeholder values.

## Future Enhancements
- Add CI pipeline step running `gitleaks` in GitHub Actions.
- Add dependency vulnerability scanning (e.g., `pip-audit`).
- Add runtime health & security metrics to `/metrics` endpoint.

## Contact
Security POC: <ADD_NAME_OR_EMAIL>

---
_Last updated: $(date)_
