# Looprail installation contract for automation agents

Use this contract when an automation agent prepares Looprail for a user. The
agent must not infer release URLs, expose credentials, publish artifacts,
reset existing configuration, or initialize a repository the user did not
select.

## Required inputs

- Target operating system: macOS/Linux or Windows.
- The target Git repository path.
- A Looprail source checkout obtained from the project's current host, or a
  trusted wheel supplied by the user or maintainers.
- Provider choice and credentials supplied directly by the user.
- Whether a real billed first Turn is allowed. Default to no without explicit
  authorization.
- Whether a message channel is in scope. Default to no.

The current source checkout does not bundle an external Memory implementation.
Use `--skip-memory`; do not guess a package or download address from adapter
names in the source tree.

## Installation

From the root of a trusted source checkout:

```bash
./install.sh
```

Windows PowerShell:

```powershell
.\install.ps1
```

For a development environment instead of a user-level tool installation:

```bash
uv sync --frozen --extra dev --dev
uv run --frozen looprail --version
```

If a maintainer supplies a fixed wheel, it may be provided through
`LOOPRAIL_WHEEL_URL`. Do not download a wheel from an untrusted address, place
credentials in a URL, or paste signed query strings into a report.

## User-owned configuration boundary

Change into the exact target repository before onboarding:

```bash
cd "<workspace>"
uv run --frozen looprail onboard --skip-memory --skip-test
```

The interactive wizard is the preferred secret-entry path. Pause while the
user enters Provider credentials.

Only use `--non-interactive --api-key ...` when the user explicitly authorizes
non-interactive secret handling. Shell arguments can be visible to local
process inspection and history tooling.

Do not add `--reset` automatically. Existing Provider, channel, sandbox, and
workspace choices belong to the user.

## Verification

Run read-only checks from the target repository:

```bash
uv run --frozen looprail --version
uv run --frozen looprail plugins
uv run --frozen looprail channels list
uv run --frozen looprail doctor --json
```

Installation verification requires:

- the `looprail` command is available;
- `looprail --version` returns the installed version;
- `looprail doctor --json` is valid JSON;
- Memory is explicitly disabled or points to an installed implementation;
- no credential value appears in captured output.

If the user authorizes a billed live check, run one of these commands:

```bash
uv run --frozen looprail doctor --probe
uv run --frozen looprail run -m "Reply with: Looprail is ready"
```

Do not call a Provider verified unless the live command returned a model reply.
A successful installation, static doctor report, or skipped probe is not a
live Provider result.

## Feishu handoff

The user owns App ID and App Secret access, permission approval, application
publication, and the inbound test message. Follow
[feishu.zh-CN.md](feishu.zh-CN.md), then verify only redacted local state:

```bash
uv run --frozen looprail channels get feishu
uv run --frozen looprail gateway --workspace "<workspace>" --verbose
```

Do not call Feishu connected until a human sends an inbound message and
receives the Looprail reply in the same conversation. Never paste Feishu
secrets into an issue, pull request, chat transcript, screenshot, or committed
file.

## Handoff record

Report the installed Looprail version, target repository path, Memory state,
whether a billed probe ran, and whether a live channel round trip ran. Redact
credentials, signed URL query strings, and unrelated local paths. If a layer
was skipped, label it unverified instead of inferring success from another
layer.
