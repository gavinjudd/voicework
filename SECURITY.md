# Security and data boundaries

Voicework runs on loopback and stores its profile in a local SQLite database. It is intended for a trusted user account on a local computer, not a shared hosted service. Do not expose its port through a public tunnel or reverse proxy. Loopback binding, origin checks and mutation tokens reduce browser-origin risks; they do not protect against another process or user with access to your account.

## What leaves the machine

Selecting a live provider sends the requested writing and applicable guidance to that provider through its configured CLI or API. Depending on the task, that guidance can include confirmed authored excerpts, reviewed preferences and approved identity context. The provider's account terms and retention policies apply. Local storage does not mean local-only model inference. Offline mode performs no model inference and does not silently replace a failed live provider.

Discovery is user-started and limited to chosen accessible sources. MCP clients can request contextual guidance and optional excerpts; a client may then send those results to its model. Exported files are separate copies under the user's control.

## Keep private data outside source control

The repository ships code, documentation and synthetic test fixtures. It does not ship a personal writing profile. Do not commit databases, writing samples, identity records, exports, provider logs or credentials. Store backups outside the checkout and use normal operating-system access controls. The application does not claim encrypted storage, secure deletion, exhaustive file discovery or guaranteed prompt-injection resistance.

Keep provider CLI software and dependencies current. Use an explicit provider executable when multiple versions are installed. Never include CLI authentication files, environment secrets or full private prompts in a bug report.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** on this repository when private vulnerability reporting is available. Include a minimal synthetic reproduction, the affected commit and environment, expected behavior, and observed impact. Do not submit real writing or credentials.

If private reporting is unavailable, open an issue requesting a private reporting channel without publishing exploit details or private data. This project does not currently promise a response time or support for historical releases.
