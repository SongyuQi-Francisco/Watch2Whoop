# The body butler (always-on Claude Code session + Remote Control)

The body butler (身体管家) is an always-on Claude Code session. Its working directory is `assistant/` in the repo (role definition `CLAUDE.md`, `.mcp.json`, `.claude/settings.json`), and it is exposed through **Remote Control**: you join the same session from the Claude app on your phone (Code tab) or from claude.ai/code in a desktop browser, chat with it and send photos, screenshots and PDFs; after it recognises them it files them into Body Engine / SparkyFitness through the body-engine MCP tools. **Everything goes through your Claude subscription login, no API key** (design: `docs/ai-assistant-plan.md` section 3).

**Since 2026-10-18 it runs on Alibaba Cloud Ali** (systemd service `body-butler`), so the Mac being off or asleep does not matter. The Mac LaunchAgent is disabled and kept only for fallback.

| File | Purpose |
|---|---|
| `butler.py` | Daemon (standard library only, compatible with Ali's Python 3.6 and macOS `/usr/bin/python3`): strips `ANTHROPIC_API_KEY` and similar variables -> picks a `claude` that is new enough (>= 2.1.200) -> runs `claude remote-control --name 身体管家 --spawn same-dir --permission-mode default --capacity <N> --no-chrome` in a pseudo-terminal -> log `butler-<date>.log` (kept 14 days); session URL and status are written to `state.json` (directory `BODY_BUTLER_STATE_DIR`: `/var/lib/body-butler/` on Ali, `health_data/assistant/` on the Mac). `butler.py status / check` |
| `../ali/butler/body-butler.service` | Ali's systemd unit: user `bodybutler` (not root; cannot read `secrets/`, `health_data/`, `.env`), enabled at boot, `Restart=always` (30 s), `MemoryLimit=1536M`, `CPUQuota=100%` (at most 1 of the 2 cores), `OOMScoreAdjust=800` (under machine-wide memory pressure the kernel kills it first, never other-app), `--capacity 2`; MCP goes straight to the local `http://127.0.0.1:8100/mcp` (no public nginx / certificate / rate limit), and the AI token is injected by systemd reading `secrets/body_butler.env` as root |
| `../ali/butler/butler.sh` | Entry point on Ali (root): `install` (create the user, install Claude Code into `/home/bodybutler/.local` with the official installer, generate `secrets/body_butler.env`, install / update the unit and restart; safe to re-run), `login` (**run by the owner**, see below), `status`, `activate` (write the current session URL into `BODY_ASSISTANT_URL` in Ali's `.env` and recreate body-engine), `uninstall` |
| `install.sh` | The Mac LaunchAgent `org.watch2whoop.body.butler` (**fallback**): no arguments installs / restores, `--disable` stops it and renames the plist `.disabled`, `--uninstall` removes it |
| `../../assistant/` | The butler's working directory (in git) |

- **Session URL**: read from claude's `~/.claude/projects/<directory name>/bridge-pointer.json` (the output only prints the environment URL). After a restart, `claude remote-control` reattaches the session this directory served before (if down for under 4 hours), so service restarts and code pushes keep the URL; a different machine (Mac <-> Ali) means a new session and a new URL, and `butler.sh activate` points "Ask the butler" (「问管家」) at it. See the current URL with `butler.sh status` or Ali's doctor. On the phone you can also open the Claude app -> **Code** -> find "身体管家" (green dot = online).
- **When the owner has to act** (login expired / directory not trusted / first Remote Control start), `butler.py` writes the status as `needs_login` / `needs_trust` / `needs_consent` and retries every 5 minutes; Ali's doctor (hourly, also brought back by the Mac doctor) shows a failure and what to do. Usually one run of `butler.sh login` fixes it.
- **Confirmation cards** (owner's rule, 2026-10-18): logging tools run directly; only deleting / undoing (`undo_sparky_entry`, `undo_meal_log`, `delete_journal_entry`, `delete_lab_results`, `delete_observation`) is `ask` in `.claude/settings.json`, so a permission prompt pops up before execution. The in-page chat uses the same list (`ai/chat.py` `ALWAYS_CONFIRM`).
- **Cost**: uses your subscription quota (when the Pro quota runs out you wait for the reset). Model Claude Opus 5.5 (`model` in `.claude/settings.json`; on the phone `/model` can switch temporarily).
- **Resources**: one claude process per session (roughly 200-400MB idle); `--capacity 2` + a 1.5G cap. Claude Code's auto-updater keeps a few versions in `/home/bodybutler/.local/share/claude` (about 300MB); the disk row in Ali's doctor reports the headroom.

## First-time setup on Ali (the owner does this, once, about 3 minutes)
The maintainer has already run `butler.sh install` on Ali: the service is installed and waiting for your login. Prerequisite: the Claude app is installed on the iPhone and logged in as owner@example.com.

1. Open Terminal on the Mac and run (`-t` is required, the next steps are interactive; `ServerAliveInterval` keeps the connection alive while you are in the browser — on 2026-10-18 an idle connection was reset at the paste-code prompt):
   ```bash
   ssh -o ServerAliveInterval=15 -t Ali /opt/body-management/deploy/ali/butler/butler.sh login
   ```
2. **[1/3] Log in**: choose **Claude account with subscription**. The terminal shows a long `https://claude.ai/oauth/...` URL: copy it into the Mac browser (if you are logged in to claude.ai it shows the authorisation page directly), click **Authorize**, copy the code the page gives you, paste it back into the terminal and press Enter. Your password is only typed in your own browser and never passes through the server or this script.
3. **[2/3] Trust the directory**: press Enter and Claude Code opens. For **Do you trust the files in this folder?** choose **Yes, proceed**; if it asks whether to enable **body-engine** from `.mcp.json`, choose enable. When the input box shows up, type `/exit`.
4. **[3/3] Enable Remote Control**: press Enter; for **Enable Remote Control?** type `y`; once you see **Connected** or a URL, press **Ctrl+C**. (If it does not ask, just press Ctrl+C.)
5. The script starts the butler service, waits for it to come online (up to 2 minutes) and points the Today page's "Ask the butler" at the new URL. Done when you see **✅ 完成**.
6. **Connect from the phone**: Claude app -> **Code** -> **身体管家** (shows Ali's host name `iZ8ps…`, green dot; the old Mac "身体管家" will show offline and can be archived by swiping left) -> send "how is today". The first time it writes data a permission prompt appears; tap **Allow**.
   - If you turned on **Require trusted devices** in the claude.ai settings, the first open asks you to register the device (Face ID); follow the prompts.
7. (Optional) Push notifications when your confirmation is needed: `ssh -t Ali "su - bodybutler -c 'cd /opt/body-management/assistant && claude'"`, type `/config`, enable **Push when actions required**, `/exit`.

Afterwards tell the maintainer, who disables the old Mac butler (`deploy/assistant/install.sh --disable`).

When the login expires later (doctor: "身体管家 ✗ 没登录"), re-run the command in step 1; finished steps are skipped or just need Enter.

## Falling back to the Mac (Ali unavailable for a long time)
```bash
ssh Ali 'systemctl stop body-butler'           # if Ali is still reachable, stop it first (do not run both under the same name)
deploy/assistant/install.sh                     # Mac: restore the LaunchAgent (removes .disabled)
deploy/assistant/butler.py status               # get the Mac session URL and put it into BODY_ASSISTANT_URL in Ali's .env
```
The Mac version uses the public `https://body.example.com/mcp` (the default in `assistant/.mcp.json`, token `secrets/body_ai_token_butler`). The Mac must already be logged in to Claude Code and trust `~/Watch2Whoop/assistant` (done on 2026-10-17).

## Permission design (`assistant/.claude/settings.json`, least privilege)
| Setting | Why |
|---|---|
| `allow`: `mcp__body-engine__get_*`, `list_*`, `analyze_journal`, `search_exercises` | Read your own data without prompts |
| `allow`: `update_profile_card`, `add_observation`, `set_status_items` | The butler's own notes (long-term memory) and status card; you can see them any time on the Today page's "Me" tab (「我」页), every change is audited; a prompt for each would be annoying |
| `allow`: `mcp__body-engine__*`; `ask`: only the delete / undo tools listed above (`test_every_write_tool_asks_in_butler_settings` keeps it equal to the in-page chat's `ALWAYS_CONFIRM` and makes sure every new delete / undo tool is listed) | Logging is not interrupted; anything that destroys data still needs your tap. `ask` takes precedence over `allow` |
| `deny`: `Bash`, `Edit`, `Write`, `NotebookEdit`, `WebFetch`, `WebSearch`, `Agent`, scheduled tasks | The butler only needs the database tools; with no shell and no file writes, even instructions hidden in a photo cannot run commands or change code and configuration; no internet, so health data cannot be carried out |
| `deny`: `Read` of `secrets/` and `health_data/` in the Mac main repo and in Ali's `/opt/body-management` (plus Ali's `.env`), `~/.ssh`, `~/.aws`, `~/.claude.json`, the keychain | Passwords, tokens and raw health data stay out of the conversation. On Ali there is a second layer: the `bodybutler` user cannot read these anyway (700 root), and `assistant/` is not owned by it, so it cannot change its own settings |
| `disableBypassPermissionsMode`, `defaultMode: default` | You cannot switch to the "skip all confirmations" mode from the phone |
| `disableClaudeAiConnectors`, `mcp__claude_ai_*` / `mcp__claude-in-chrome__*` deny, `enabledMcpjsonServers: [body-engine]`, stock plugin turned off | Connect only the body-engine MCP; no claude.ai connectors such as Gmail / Drive and no browser |
| `autoMemoryEnabled: false`, `claudeMdExcludes` excludes the repo root `CLAUDE.md` (Mac main repo, worktrees, Ali's `/opt/body-management/CLAUDE.md`) | Does not read the development notes and auto-memory (those are for development sessions); long-term memory lives in the database tables `assistant_profile` / `assistant_observations` |
| `UserPromptSubmit` hook: appends the current local time to every message | The butler has no shell; to decide which day "tonight / the small hours" belongs to it needs the current time (`get_profile` also returns `now_local`) |
| `ENABLE_TOOL_SEARCH=false` | All tools (65, about 14k tokens since the 2026-10-18 slimming) are loaded up front, no search needed first |

MCP identity: the AI-only token `secrets/body_ai_token_butler` (user 1, scope `mcp`, usable only on `/mcp`, can be revoked separately; `ai-token-list / revoke`), audit channel `mcp:butler`. On Ali `butler.sh install` turns it into systemd's `secrets/body_butler.env`; re-run `install` after replacing the token.

## After code updates (maintainer)
After `deploy/ali/push.sh code`, if `butler.py` or the unit changed: `ssh Ali /opt/body-management/deploy/ali/butler/butler.sh install` (restarts the service; the session is reattached and the URL stays). If only `assistant/` CLAUDE.md / settings changed: it applies to new sessions; `/clear` on the phone or `ssh Ali systemctl restart body-butler`.

## Verification (2026-10-17, worktree version)
`claude remote-control` (v2.1.301) started by the LaunchAgent connected to Anthropic (`✔ Connected`), session `https://claude.ai/code/session_013z2WCXLHuokuCfS8tuRNP7` ("身体管家"); after `launchctl kickstart -k` restarted it, it reattached to the same session.
The conversation flows were run with `claude -p` inside `assistant/` (same CLAUDE.md, `.mcp.json` and settings, model claude-opus-5-5; "tap allow" was simulated with `--permission-prompt-tool`, every confirmation card was logged) and all worked: "how is today" gives a 3-line briefing; "two beers last night, dinner only at 12" -> blocked by a confirmation before writing -> after allowing, journal alcohol / late_dinner (10-16) + food were written; a nutrition-label photo -> kJ conversion, logged to 10-16 by the "before bed counts as the previous day" rule; a synthetic lab report -> it first pointed out it was a synthetic image and did not write silently, after confirmation 9 items were written under unified keys (uric acid mmol/L -> μmol/L); upright row / bar dips -> rejected by the tool for the whole call even though the user had allowed it; logging the same yogurt tub again -> duplicate skipped; observation notes added and deleted (visible via the Today page API); finally the butler itself undid all the test data, and the database and SparkyFitness were verified back to their pre-test state. The conversation records are in the maintainer's notes directory `butler-e2e/`.

## Troubleshooting
- Ali: `ssh Ali /opt/body-management/deploy/ali/butler/butler.sh status`; logs `/var/lib/body-butler/butler-*.log`, `journalctl -u body-butler`; the rows "身体管家", "管家 MCP" and "「问管家」地址" in `python3 /opt/body-management/deploy/ali/doctor.py`.
- Shows offline on the phone: the service is restarting (30 s) or Ali lost network; `systemctl status body-butler`. Kills by the memory cap show up in `NRestarts`.
- Chats but cannot read data: the AI token was revoked / MCP is down (doctor "管家 MCP" ✗): on Ali `deploy/ali/engine.sh ai-token-create --user-id 1 --client butler`, write the first line into `secrets/body_ai_token_butler`, then `butler.sh install`.
- A session is stuck: send it a message from the phone and Claude Code serves it again; or `ssh Ali systemctl restart body-butler`.
- To start a new conversation (context too long): `/clear` in this session on the phone, or create a new session for the Ali machine in claude.ai/code (same directory, same settings).
- Mac fallback: `deploy/assistant/butler.py status`; logs `health_data/assistant/butler-*.log`.

## Gemini CLI in `assistant/` (Google AI plan)
For an owner who uses a Google AI plan instead of, or next to, a Claude subscription:
- `assistant/.gemini/settings.json`: persona from `GEMINI.md`, which imports `CLAUDE.md` (a `CLAUDE.md` file name would also load the repository's developer notes one directory up); remote MCP `${BODY_MCP_URL}` with `Bearer ${BODY_AI_TOKEN}`; built-in tools excluded; a `BeforeAgent` hook adds the local time as `additionalContext`.
- `assistant/.gemini/policies/butler.toml`: body-engine tools allowed, the 5 delete / undo tools (`ALWAYS_CONFIRM`) ask (denied when non-interactive), built-in tools denied; `tests/test_butler.py` checks it against `ALWAYS_CONFIRM`.
- `deploy/assistant/butler.py gemini-check`: the `gemini` binary (`BODY_GEMINI_BIN` or `PATH`), the Google sign-in, the settings file.
- Use it in a terminal: `cd assistant && gemini`. There is no Gemini equivalent of `claude remote-control` yet, so the phone entry point stays the Claude butler.
- In-page chat through the server's CLI (`gemini_cli` provider) is **off by default**: it needs `BODY_GEMINI_CLI_ENABLED=1` in the body-engine environment, a `gemini` binary where body-engine runs (the default image has none), and is only for `BODY_ASSISTANT_USER_ID`. Point `BODY_GEMINI_HOME` at a directory holding only the CLI's sign-in. Details and the containment list: `docs/ai-assistant-plan.md` 9.14.
