# secrets/

Passwords and tokens live here. **The whole directory is kept out of git, except this file.**

| File | Purpose | How it is generated |
|---|---|---|
| `body_db_password` | Postgres password for Body Engine | `openssl rand -base64 24 \| tr -d '\n' > secrets/body_db_password` |
| `body_engine_token_butler` | Token the body butler (the Claude Code session in `assistant/`) uses to reach the Body Engine MCP server (user 1, named claude-butler, can be revoked on its own) | `cd body_engine && uv run body-engine token-create --user-id 1 --name claude-butler > ../secrets/body_engine_token_butler` |
| `anthropic_api_key` | (reserved) the AI assistant calling the Claude API | The user generates it at console.anthropic.com and writes it in; Claude never handles it |
| `sparkyfitness.env` | SparkyFitness database password, encryption key, Better Auth secret, public URL | `python3 deploy/sparkyfitness/init-secrets.py` |
| `sparkyfitness_<who>_password` | SparkyFitness account password (owner = the owner themself) | `init-secrets.py` / `bootstrap.py` |
| `sparkyfitness_api_key_<who>_rw` / `_ro` | SparkyFitness read-write key (migration script) / read-only key (Body Engine) | `python3 deploy/sparkyfitness/bootstrap.py` |
| `restic_password` | Encryption password for the backup repository `~/Backups/body-management-restic`. **Keep a second copy in a password manager**: secrets/ itself is part of the backup, so if this password is lost the backup cannot be opened | Generated automatically the first time `deploy/ops/ops.sh backup` runs |
| `shortcut_deny.txt`, `public_deny.txt` | Private deny lists (one word or phrase per line, `#` comments) for the public-repository scan `tools/export_public.py --scan` that the maintainer's pre-push hook runs: words the Shortcut must not contain, and the maintainer's own health values / personal details. Never committed; the CI scan runs only the generic rules | Written by hand |
