# ADR 0001: Local CLI with a Codex-first adapter

Status: Accepted

Context: The MVP needs a small, inspectable implementation that works inside an existing repository without an operated service.

Decision: Ship a Linux-first Python 3.11+ CLI. Render Codex support from the project profile; keep deterministic operations in the CLI and repository commands. No daemon, service, or web UI is required.

Consequences: Linux is the supported MVP platform, including the confinement used during adoption. Codex is the first adapter. Other adapters, orchestration, technology radar, and a dashboard remain deferred; no generic adapter framework is needed yet.
