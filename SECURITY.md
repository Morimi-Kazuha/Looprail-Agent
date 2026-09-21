# Security

Looprail can inspect repositories, run local tools, modify files, and connect
to external Providers or message channels. Treat configuration, credentials,
workspaces, and generated evidence as sensitive.

## Safe handling

- Do not commit API keys, tokens, passwords, App Secrets, private keys,
  personal data, or signed URLs.
- Do not paste secrets into issues, pull requests, chat transcripts, screenshots,
  or raw Runtime traces.
- Review workspace, shell, Provider, channel, and sandbox settings before
  allowing a live task to run.
- Redact credentials and machine-specific paths from diagnostic output.

## Reporting a vulnerability

Please do not publish exploit details in a public issue. If the repository host
provides a private security-reporting channel, use it. Otherwise, contact the
project maintainers through a private channel associated with the repository
and ask for a secure reporting route before sending vulnerability details.

Include only the minimum information needed to establish a private discussion.
Once a private channel is available, provide the affected version or commit,
reproduction steps, impact, and any suggested mitigation. Do not include live
credentials or unrelated private data.

For ordinary installation, configuration, compatibility, or feature questions,
use the [Looprail public issue tracker](https://github.com/Morimi-Kazuha/Looprail-Agent/issues)
instead.
