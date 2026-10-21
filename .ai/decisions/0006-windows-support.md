# ADR 0006: Windows CLI and project commands

Status: Accepted (user request: add Windows support)

Extend ADR 0001 to support Windows with Python 3.11+ and Git. Keep the existing
XDG configuration paths. Install Windows curses only on Windows and accept
native terminals without TERM. New project command starters use Python with a
Windows dev.cmd launcher. Existing project commands remain project-owned.
Goal command strings use the platform shell, cmd.exe on Windows and /bin/sh
on Linux. Generated CI runs the project commands on Windows and Linux.

Managed text and profile hashes normalize CRLF to LF so Git checkout conversion
does not report content drift. Guarded writes and source preservation still
check the actual bytes; instruction rewrites preserve human bytes outside the
managed block. Windows junctions are refused wherever symlinks are refused.

ADR 0004's confinement requirement remains in force: Windows adoption records
UNKNOWN, and setup cannot finalize without confined verification. Do not run
inherited commands unconfined as a fallback.
