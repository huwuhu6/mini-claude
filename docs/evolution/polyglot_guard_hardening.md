# Polyglot Guard Hardening

## Scope

The runtime guard now treats hard-error signatures as terminal only when the
tool result is known to be a failed result. Successful output containing words
such as `Permission denied` or an old package error is not circuit-broken.

Pre-flight tool version checks invoke Windows `.cmd` and `.bat` wrappers through
`cmd.exe`. Network probing checks configured proxy endpoints before direct DNS
transport targets, and the context records the workspace write capability.

The workspace guard recognizes common shell, PowerShell, Python, Node, and
filesystem API write forms while using the actual workspace SHA256 snapshot to
decide whether a mutation occurred.

Environment blockers terminate the current task with `CIRCUIT_BROKEN` and keep
the blocker category and trigger count in the trace. This is distinct from a
task that completed successfully.
