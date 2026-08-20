# CLAUDE.md

> For dev setup and commit conventions, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Quick Commands

| Command | Purpose |
|---------|---------|
| `uv sync` | Install dependencies |
| `uv run ruff check src/ tests/` | Lint |
| `uv run ruff format src/ tests/` | Format |
| `uv run pytest` | Run tests |
| `uv run pytest --cov` | Run tests with coverage |
| `uv run mac-upkeep run --dry-run` | Test CLI without side effects |
| `uv run mac-upkeep init` | Generate starter config (auto-detect tools) |
| `uv run mac-upkeep show-config --default` | Show all available task options |
| `uv run mac-upkeep notify-test` | Verify macOS notification permissions |

## Architecture

```
defaults.toml → bundled task definitions, loaded via importlib.resources
config.py     → TaskDef dataclass, load_task_defs(), resolve_variables(), get_brew_prefix(),
                Config.load() (3-layer merge: defaults.toml → user config → env vars)
tasks.py      → _build_cmd(), run_task(), _run(), run_all_tasks() data-driven loop,
                frequency scheduling, format_last_run(), format_next_run()
cli.py        → Typer app: run, tasks, init, show-config, setup, status (dashboard), logs, notify-test
output.py     → TaskResult dataclass, Rich Live table TUI (interactive), Python logging
                (non-interactive), strip_control_sequences() (shared sanitiser)
notify.py     → macOS notifications via terminal-notifier (preferred) / osascript (fallback)
```

Entry point: `mac_upkeep.cli:app` (registered in pyproject.toml `[project.scripts]`).

Task execution order defined in `defaults.toml` `[run] order`. Users override in `~/.config/mac-upkeep/config.toml`.

### Adding a task

Add a `[tasks.<name>]` entry to `defaults.toml`. No Python code changes needed. Fields that match `TaskDef` defaults can be omitted (weekly frequency, enabled, no sudo, no shell, 300s timeout). Run `uv run pytest` to validate. If the task requires a binary not in PATH, set `detect` to the binary name.

## Key Patterns

### TOML-driven task registry

- **`defaults.toml` is the single source of truth.** Adding a built-in task = 1 TOML entry. No Python code changes needed. The file is bundled via `importlib.resources.files("mac_upkeep")` and loaded at startup.
- **`TaskDef` fields are minimal.** `defaults.toml` only specifies fields that differ from `TaskDef` dataclass defaults (`frequency="weekly"`, `enabled=True`, `sudo=False`, `shell=""`, `require_file=""`, `timeout=300`). Weekly tasks omit `frequency`.
- **Config.load() reads user TOML once.** Parsed data is passed to `load_task_defs()` as a dict — avoids double file read. Brewfile and notification settings are extracted from the same parse.

### Detection and execution

- **`detect` is separate from `command`**: `detect` field specifies which binary to check with `shutil.which()`. This is separate from the command that runs. Reason: for sudo tasks, `_build_cmd()` prepends `["sudo", "-n"]` to the command — if detect used `cmd[0]`, it would check `shutil.which("sudo")` instead of the actual tool. Validated against topgrade's `require("binary")` pattern.
- **`_build_cmd()` composable**: `sudo` and `shell` are independent flags. Both branches (shell vs non-shell) merge before the sudo check. No early return in the shell branch — sudo wraps the entire invocation.
- **stdin closed for task subprocesses**: `stdin=subprocess.DEVNULL` prevents interactive tool hangs. Mole detects interactive mode via `[[ -t 0 ]]` (stdin is TTY) or unguarded `read_key` calls. Fisher uses `$last_pid` for job control which fails in non-interactive shells ([fisher#608](https://github.com/jorgebucaran/fisher/issues/608)), hence `shell = "fish --interactive -c"`.

### Filter-then-frequency contract

`_run()` has two early-return checks in strict order:
1. **Filter** (`force_tasks is not None and task_key not in force_tasks`) — unconditional
2. **Frequency** (`not dry_run and force_tasks is None and not _should_run()`) — conditional

Do not add conditions to the filter or remove the `force_tasks is None` guard from frequency. `require_file` tasks are checked in `run_all_tasks()` BEFORE calling `_run()`, respecting filter→enabled→file order.

### Frequency scheduling

- Thresholds are 20 hours for daily, 6 days for weekly, 27 days for monthly (not 24h/7d/30d — buffer for launchd schedule drift after sleep/reboot). State tracked in `~/.local/state/mac-upkeep/last-run.json`.
- **Safety net**: prevents redundant runs from RunAtLoad boot triggers, launchd coalescing, and manual `mac-upkeep run`. `run_at_load true` is intentional — `StartCalendarInterval` does NOT coalesce from power-off (only sleep), so RunAtLoad is essential for laptops that reboot frequently.
- Timestamps only update on successful non-dry-run execution. Corrupt/missing state file silently triggers re-run. Failures are gated separately — see [Retry backoff](#retry-backoff).
- **`FREQUENCY_THRESHOLDS` is dual-purpose**: used for gating in `_should_run()` and for display in `format_next_run()`. `format_next_run()` accepts an optional `state` dict parameter to avoid redundant `_load_state()` calls — the `tasks` command pre-loads state once; `_run()` skip path omits it (one-off read is fine).
- **Status column priority in `tasks` command**: `disabled → not found → ready` mirrors the check order in `run_task()` (disabled check then detection check) but is computed independently in `cli.py` using `td.enabled` and `shutil.which(td.detect)`. `td.detect` is already variable-resolved and auto-inferred by `Config.load()`, so `shutil.which(td.detect)` works directly — no raw TOML variable resolution needed. The detection check is guarded by `td.detect and ...`: handler tasks (e.g. `editor_cache`) carry an empty `detect`, so they short-circuit to `ready`/`disabled` instead of a misleading `not found` (`shutil.which("")` is `None`). This mirrors the execution gate in `_run_handler` (`if td.detect and not shutil.which(td.detect)`).

### Retry backoff

Because only successes write to `last-run.json`, a task that *always* fails never becomes "recently run" and therefore retries on **every** invocation. Observed in production: `mo optimize` began exiting 1 whenever any sub-task fails (mole ≥1.50 `optimize_outcomes_succeeded()` returns non-zero if any single optimization failed, even on an otherwise successful run), producing 17 failed attempts across 18 invocations in 10 days — each a sudo system scan plus a failure notification.

- **Separate state file**: `~/.local/state/mac-upkeep/retry-state.json` holds `{task: {failures, last_attempt}}`. `last-run.json` deliberately stays a flat `{task: iso}` success ledger — the `tasks`/`status` dashboards and `format_last_run()`/`format_next_run()` read it directly, so changing its schema would ripple through ~40 test touchpoints for no gain.
- **Delay doubles per consecutive failure, capped at the task's own frequency threshold** (`_BACKOFF_BASE * 2**(failures-1)`, capped at `FREQUENCY_THRESHOLDS[frequency]`). A permanently-failing weekly task settles at weekly rather than giving up — no max-attempts cutoff, since these are idempotent maintenance commands that may start working again.
- **`_should_run()` checks backoff first**, so the gate composes with frequency instead of replacing it. `_skip_reason()` (not `_should_run`) produces the user-facing string so a failing task reads `retry backoff after N failures, next …` rather than the misleading `ran recently`.
- **`format_next_run()` takes the later of the frequency and backoff due times** — otherwise the dashboard shows `now` for a task that is actually blocked.
- Failures are recorded only when `not dry_run`; a success calls `_clear_failure()` to reset the counter.

### Output and notifications

- **Interactive detection**: `sys.stdout.isatty()` switches between Rich Live table and Python logging. Same code path, different presentation.
- **Rich Live TUI state separation**: `_TaskState` holds status/reason/duration; `_generate_table()` renders. Debug output scrolls ABOVE the pinned table via `self._live.console.print()` — never put debug content into `_TaskState`.
- **Notifications fire on activity**: `notify()` is called when at least one task ran or failed, regardless of `output.interactive`. Suppressed when all tasks skip (e.g., RunAtLoad boot with recent timestamps). The headless + notification + click-to-act pattern means notifications are the user's feedback channel for scheduled runs.
- **terminal-notifier preferred**: `shutil.which("terminal-notifier")` tries the richer tool first. Fallback to osascript loses `-group` (dedup), `-activate` (focus terminal), `-open` (click action).
- **Bundle ID detection chain**: `CMUX_BUNDLE_ID` env var → Ghostty.app plist via `defaults read` → `com.apple.Terminal` fallback.
- **Rich is a transitive dependency**: `typer>=0.12` requires `rich>=12.3.0`. Using Rich adds zero new runtime dependencies.
- **`status` dashboard graceful degradation**: if `_get_service_info()` returns None (brew not installed, service not registered, or JSON parse failure), the service header is skipped and only the task scheduling summary is shown. Reuses `format_last_run()` and `format_next_run()` from tasks.py. Test by patching `mac_upkeep.cli._get_service_info` directly rather than mocking subprocess.run (avoids interfering with Config.load() → get_brew_prefix() subprocess call).

### Handler-dispatched tasks

A TaskDef with `handler="<name>"` and empty `command` bypasses subprocess building: `run_all_tasks` routes it through `_run_handler`, which applies filter + frequency + `detect` gates then calls `HANDLERS[name](config, output, dry_run)`. Handler modules register themselves in `tasks._register_handlers()` (called at import). `KNOWN_HANDLERS` drives config validation — unknown handler names are rejected early. Handlers emit per-step output via `output.task_debug()` and return one aggregate `TaskResult`.

**Adding a handler is a 3-edit change** (see `editor_cache` as the second exemplar after `git_sync`): the module + `_register_handlers()`/`KNOWN_HANDLERS` line + a `[tasks.<name>]` block (with `handler=`, no `command`) in `defaults.toml` plus its `run.order` entry. **Gotcha:** every new `defaults.toml` task breaks fixtures that hardcode the task count and patched `KNOWN_HANDLERS` sets — grep `tests/` for the old count and `KNOWN_HANDLERS` monkeypatches (memory: "grep test fixtures when adding tasks to defaults.toml").

### editor_cache handler

Reclaims Electron/editor caches mole's classifier structurally misses (`Service Worker`, `node/cache` don't match its `[Cc]ache|[Ll]og|...` regex). Source-validated against `tw93/mole`:

- **Surgical targeting**: clear `Service Worker/CacheStorage` (the bloat), never the parent `Service Worker/` — its `Database/` holds the SW registrations (mole never deletes it).
- **Running-app guard via `pgrep -x`** (mole's technique; robust under launchd, no TCC/GUI dependency unlike osascript) — deleting a running Electron app's cache can corrupt state, so the app must be closed.
- **Zed `node/cache` is npm's `--cache`** (`zed-industries/zed` `node_runtime.rs:603`): clearing only re-downloads tarballs on the next LSP install; installed servers keep working. Size-gated (`min_size_mb`, default 2048) to skip pointless re-downloads.
- **`_is_safe_target`** refuses anything not ≥2 segments below `~/Library/Application Support`, and refuses symlinks — mirrors mole's `validate_path_for_deletion`.
- Ships **`enabled = false`** (opt-in: it's `rm -rf` and reaches all users, like `pnpm`). Targets default to Notion + Zed; override with `[[editor_cache.apps]]` (`name`/`process`/`min_size_mb`/`targets`) in user config.

### Fail-closed contracts (security audit run-1)

Four gates were changed from fail-open to fail-closed. Each looks like an inconsequential
default until you notice which direction it fails in; do not "simplify" them back.

- **`Config.is_enabled()` returns `False` for an unknown key**, not `True`. Every caller
  derives the key from a real `TaskDef` via `normalize_task_key()`, so a miss means the two
  keyspaces diverged — which must never resolve to "run it". This is only safe *because*
  keys are normalised at load: alone, it would stop a legitimately-enabled custom task with
  a capitalised name from ever running. The two changes ship together or not at all.
- **`normalize_task_key()` is applied once in `load_task_defs()`** — to default keys, user
  keys, and `run.order` entries — so `tasks.py`'s `task_key` derivation is an identity
  operation. Before, `[tasks.DockerPrune]` was stored raw, `is_enabled` missed, and
  `enabled = false` was ignored while the dashboard cheerfully printed "disabled". Two names
  normalising to one key is a `ValueError`, because otherwise the winner depends on TOML
  table order.
- **User field types are validated** (`_FIELD_TYPES` + `_check_field`) in *both* the override
  branch and `_parse_task_def`. `enabled = "false"` — quoting a TOML boolean, the most common
  TOML mistake — used to leave a truthy string and run the task anyway; `sudo = "false"`
  *added* `sudo -n`. `bool` subclasses `int`, so `timeout = true` needs the explicit
  `isinstance(value, bool)` rejection.
- **`MAC_UPKEEP_<TASK>` is an allowlist of truthy strings**, not a denylist of falsy ones.
  `off`, `disabled` and `""` previously all enabled the task.

### require_file must distinguish "unset" from "resolved to nothing"

`TaskDef.require_file_declared` records that the template was non-empty *before* variable
resolution. The old guard `if td.require_file and not Path(...).is_file()` short-circuited on
the empty string, so `brew_bundle` ran `--file=` — which Homebrew treats as absent, falling
back to `$PWD/Brewfile`, evaluating it as Ruby and then uninstalling everything it does not
list. It also failed on every run for every user with no Brewfile, burning retry backoff and
a notification each time. Related: `_discover_brewfile()` must never regain a CWD-relative
candidate, and `run_task` passes `cwd="/"` so no task can be steered by the caller's
directory (launchd already provides `/`).

### git_sync: repository config is untrusted input

A repo's own `.git/config` can make git execute a command, and `safe.directory` does not
help — it keys on ownership, and an extracted archive is owned by the invoking user.
`_run_git` therefore prepends `_trusted_overrides()` to **every** call, not just `pull`:
`git status --porcelain`, the `skip_dirty` *safety* check, is itself enough to trigger
`core.fsmonitor` and filter drivers. All sinks below were verified to fire on git 2.55.

- `_STATIC_OVERRIDES` are unconditional: `core.fsmonitor=`, `core.hooksPath=/dev/null`,
  `protocol.ext.allow=never` (a repo can set `protocol.ext.allow=always` and get `ext::`
  back), `protocol.file.allow=never` (the only working block for `remote.<n>.uploadpack`,
  which a per-name `-c` override does *not* beat), and `protocol.git.allow=never` (the only
  working block for `core.gitProxy` — `-c core.gitProxy=` does **not** disable it).
- **Multi-valued and single-valued keys need opposite treatment, and confusing them is a
  live outage.** `_INHERITED_LIST_KEYS` (`credential.helper`) is reset with an empty entry —
  which for a list key means "discard what git accumulated" — then the user's global/system
  values are appended back. A bare reset without the re-add would break nearly every macOS
  user, because Homebrew git ships `credential.helper = osxkeychain` at *system* scope, and
  the helper fires on an HTTP 401 despite `GIT_ASKPASS` and `GIT_TERMINAL_PROMPT`.
  `_INHERITED_SCALAR_KEYS` (`core.sshCommand`) must instead be **set** to the user's value
  or to an explicit default. An empty entry there is not a reset: git execs the empty string,
  and every SSH remote fails with `error: cannot run :` on every run — a retry storm for the
  default configuration, which is the one the README documents.
- **Known residual, do not claim otherwise:** `filter.<driver>.clean` from a planted
  `.gitattributes` still executes on `status`. Driver names are arbitrary, so no fixed `-c`
  set covers them, and git has no "ignore repo-local config" switch. Enrolment discipline is
  the real control. Before adding a "this is the only remaining sink" claim anywhere, re-derive
  the list against the installed git — `core.gitProxy` was missed on the first pass precisely
  because the sink list was taken as complete rather than re-tested.
- `_trusted_cache` is process-global; `tests/conftest.py` seeds it with the static half so
  argv is deterministic and the `git config` probes are not caught by tests that patch
  `git_sync.subprocess.run`. Tests exercising the inherited half reset it to `None`.

### Untrusted text is never Rich markup

`output.py` renders subprocess and remote-derived text as a literal `rich.text.Text`, never
interpolated into a markup string — in `task_debug` *and* in `summary`'s failure detail. A
bracketed path like `[/usr/local]` raises `MarkupError` (which used to abort the run and
leave the cursor hidden, since `Live.__exit__` never ran), and `[link=…]` renders a real
OSC-8 hyperlink with attacker-chosen target and anchor text inside our own failure summary.
`strip_control_sequences()` in `output.py` is the single sanitiser for both `tasks.py` and
`git_sync.py`; it covers all escape sequences plus stray C0/C1 characters, not just SGR
colour codes. It deliberately does **not** touch `[` — markup is handled by `Text`, not by
escaping.

### No single task may abort the run

`run_task` catches `OSError` (a shim whose shebang interpreter vanished is the routine
trigger), `run_all_tasks` wraps each task in a catch-all, and `cli.run` passes its own
`results` list in so `output.summary()` and `notify()` still describe whatever completed.
Under launchd the notification is the user's only feedback channel, and `summary()` is what
exits the Rich `Live` context. The dead `CalledProcessError` branch was removed — `check=True`
is never passed, so it read as coverage that did not exist.

### mole runs as the invoking user (4.0.0)

`mo_clean` and `mo_optimize` carry **no** `sudo`, and `setup` emits no sudoers rules. Do not
"restore" them. mac-upkeep was the component that introduced the NOPASSWD grant — mole itself
ships none, and neither do topgrade or homebrew-autoupdate.

- The rule named `$BREW_PREFIX/bin/mo`, a bash script in a **user-writable** prefix sourcing
  ~30 user-owned `.sh` files, so user-level code execution became silent root. A `sha256`
  `Digest_Spec` is not a fix: it hashes the entry script only, and `sudoers(5)` calls digests
  TOCTOU-racy for a user-writable directory.
- Running mole as root is also outside mole's own stated threat model
  (`docs/SECURITY_DESIGN.md`), and its vulnerable `chown` path (`lib/core/base.sh:711`) exists
  *only* in root mode.
- **Do not "fix" this by dropping `HOME` from `env_keep` instead.** Verified: mole's
  `get_invoking_home()` covers neither `LOG_FILE` nor its ~260 raw `$HOME` cleanup targets, so
  root-run mole would silently operate on `/var/root` and stop cleaning the user's caches
  while appearing to work.

**The two tasks are not the same shape, and that asymmetry is deliberate:**

- `mo_clean` stays **enabled**. Non-interactively (`stdin` closed) `bin/clean.sh` takes its
  `else` branch at `:1494` and calls `adopt_sudo_session`, which is `sudo -n -v` — the `-n`
  means it never prompts. No ticket simply yields `SYSTEM_CLEAN=false` and the run continues
  on user caches. Measured cost of the lost privileged half: ~7 MB/week in `/private/var/log`.
- `mo_optimize` ships **`enabled = false`**, not merely de-sudoed. `bin/optimize.sh:293` calls
  `ensure_sudo_session` unconditionally whenever `MOLE_DRY_RUN != 1`, with no non-interactive
  guard, so under launchd `request_sudo_access` takes its `is_gui_mode` branch and raises an
  osascript password dialog that sits until the task times out. Unprivileged it would also
  achieve nearly nothing, since its work is all root-only.

`TaskDef.sudo` and `_build_cmd`'s sudo branch are retained deliberately — a user may still set
`sudo = true` on their own custom task. It simply is not used by anything shipped.

### setup() reads the username from pwd, not the environment

`_current_username()` uses `pwd.getpwuid(os.getuid())` and validates against
`^[A-Za-z0-9._-]+$`. `getpass.getuser()` consults `LOGNAME`/`USER`/`LNAME`/`USERNAME` *before*
pwd, so anything able to set an env var chose what got interpolated into output the user pipes
into a root-owned config file — and `visudo -c`, the check the README used to prescribe,
reported `parsed OK` on the injected result.

### Brew prefix detection

`get_brew_prefix()` lives in `config.py` (moved from tasks.py). Called once during `Config.load()` for `${BREW_PREFIX}` variable resolution. Subprocess call with architecture fallback — portable across Apple Silicon (`/opt/homebrew`) and Intel (`/usr/local`).

## Non-Obvious Constraints

- `gcloud-cli` is a Homebrew cask, not a formula — can't be a formula dependency. Auto-detected at runtime.
- `mo_purge` non-interactive mode (stdin closed) auto-selects items not modified in the last 7 days. Interactive mode shows a TUI selector.
- `mo_optimize` has three unguarded `read_key` calls that block on stdin. With stdin closed, `read` returns non-zero → prompts skipped. Safe operations still run.
- `uv cache prune` requires `--force` in environments with long-running `uvx` processes (MCP servers). Without it, prune blocks on the cache lock ([astral-sh/uv#16112](https://github.com/astral-sh/uv/issues/16112)).
- NOPASSWD in sudoers bypasses PAM entirely — no interaction with Touch ID (pam_tid) setup.
- **launchd PATH requires `std_service_path_env`**: launchd default PATH is `/usr/bin:/bin:/usr/sbin:/sbin`. Without `environment_variables PATH: std_service_path_env` in the formula service block, all Homebrew-installed tools fail `shutil.which()`. Notifications fall back to osascript.
- **No Python FileHandler for log file**: launchd redirects stderr to the log file. Python `FileHandler` causes duplicate lines. Log rotation handled by macOS newsyslog.d.
- **newsyslog.d config** printed by `mac-upkeep setup` but NOT auto-installed (requires `sudo tee`).
- **`terminal-notifier` is optional** — installed via `brew install terminal-notifier`.
- **Do not open terminal windows from launchd** — fragile (focus stealing, macOS 13+ permission escalation). Use headless + notification + click-to-act.
- **Testing**: `Config.load()` calls `get_brew_prefix()` which runs `subprocess.run(["brew", "--prefix"])`. Tests that mock `subprocess.run` or `shutil.which` will capture this call too (shared module objects). Mock `mac_upkeep.config.get_brew_prefix` directly in `init` command tests.
- **`tests/conftest.py` isolation is mandatory, not optional.** Typer's `CliRunner.invoke` does NOT sandbox `$HOME`/`$XDG_CONFIG_HOME`, so without the autouse `isolate_user_environment` fixture every `runner.invoke(app, ["run"])` reads the developer's live `~/.config/mac-upkeep/config.toml` and real `last-run.json`. That failure mode is invisible in CI (clean runner, no user config) and only breaks for contributors who actually *use* mac-upkeep — their extra tasks enter `run_order` and break task-count and notification assertions. `tests/test_isolation.py` guards the contract; prior art is pip's autouse `isolate` fixture and pyscaffold's `fake_xdg_config_home`.
- **Config/state paths are import-time constants**, so setting `XDG_CONFIG_HOME` from a fixture is too late — patch `mac_upkeep.config.DEFAULT_CONFIG_PATH`, `mac_upkeep.cli.DEFAULT_CONFIG_PATH`, `tasks._STATE_FILE` and `tasks._RETRY_FILE` objects directly. `Config.load(path=None)` resolves `DEFAULT_CONFIG_PATH` **at call time**; as a default argument it would bind at def time and silently ignore every patch.
- **`git_sync` SSH auth under launchd** relies on `~/.ssh/config` `IdentityAgent` (path-based, e.g. 1Password socket). `SSH_AUTH_SOCK` env vars are NOT inherited by LaunchAgents, so env-based agent forwarding won't work here — the `IdentityAgent` directive is the supported path.
- **`git_sync` forces `GIT_TERMINAL_PROMPT=0` and defaults `GIT_ASKPASS=/usr/bin/true`** (user-set `GIT_ASKPASS` is respected) — fail-fast on auth misconfiguration instead of stalling to the 60 s subprocess timeout. A genuine stall (network/server hang) still hits the timeout, so `_run_git` catches `subprocess.TimeoutExpired` and returns a synthetic `CompletedProcess(returncode=124, stderr="timed out after {timeout}s")`. That marks only the affected repo failed and lets the loop continue — a hung pull cannot abort the whole run (which would otherwise skip `output.summary()`/`notify()` and later tasks).
- **`git_sync` failure reasons belong in the aggregate `TaskResult`, not only `output.task_debug()`** — debug output is suppressed without `--debug` on the non-interactive path (`Output.debug` is set but not consulted by the interactive branch, so a TTY always sees it), so the log recorded `13 failed: <names>` with no cause, which is not diagnosable after the fact. `_format_failures()` groups repos by reason because the common case is one shared cause (a network drop failing every repo at once); it caps names per group and truncates long messages so a 16-repo outage cannot blow up the log line or the notification body.

## Release Process

### A commit body line starting with `name(` silently kills the release

release-please parses each commit with `@conventional-commits/parser`. A body line that
**begins** with `identifier(` is parsed as a `type(scope):` header, so the parser enters
scope mode and expects `)`. If the parens nest, the scope never closes, the whole message
fails to parse, and release-please logs

```
error message: Error: unexpected token '(' at 43:24, valid tokens [)]
No commits for path: ., skipping
```

then **exits 0**. The workflow goes green, no release PR appears, and a `BREAKING CHANGE:`
footer is simply lost. This happened to v4.0.0's first commit, whose body contained a line
starting `` `pwd.getpwuid(os.getuid())` ``.

Verified boundaries — the trigger is the line *start*, not nesting on its own:

| body line | parses |
|---|---|
| `a(b(c)) here` | ✗ |
| `` `a(b(c))` here `` | ✗ |
| `see a(b(c)) here` | ✓ |
| `  a(b(c)) here` (indented) | ✓ |
| `- a(b(c)) here` (bullet) | ✓ |
| `a(b) here` (no nesting) | ✓ |

So: never start a body line with a function call. Indent it, bullet it, or put a word first.
`pr-checks.yml` now runs the real parser over every commit in a PR, because the failure mode
is silent and a green release run is not evidence that a release was cut. **After merging
anything with a `BREAKING CHANGE:` footer, confirm the release PR actually appeared** — the
footer surviving onto `main` is necessary but not sufficient.


Automated via release-please + homebrew-tap dispatch:

1. Commit changes using conventional commits, push to main
2. release-please creates a release PR (bumps version in `pyproject.toml`, updates CHANGELOG.md)
3. Release workflow auto-updates `uv.lock` on the PR branch, test.yml validates via `uv lock --check`
4. Merge the release PR → GitHub release + tag created → `bump-tap` dispatches to homebrew-tap, `pypi-publish` publishes to PyPI
5. Verify: check homebrew-tap Actions tab for successful formula update

## Reusable Patterns

This repo serves as a reference for Python CLI projects using Typer + UV. See [docs/reusable-patterns.md](docs/reusable-patterns.md) for copy-ready workflows, configs, and adaptable patterns.
