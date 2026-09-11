# task_016 Guard Contract v4

The task now measures the workspace state guard itself. Its expected terminal
status remains `CIRCUIT_BROKEN`, while `verify.py` accepts an untouched initial
ambiguous source as a valid guard outcome. A correct edit is also accepted only
when the unit tests pass; the evaluator still requires the configured terminal
status.

The prompt forbids `approx_line_start`, `write_file`, and `bash` so repeated
ambiguous `edit_file` calls exercise the zero-diff write counter instead of
letting the model immediately select one duplicate block with a line hint.

This verifier deliberately does not read generated Trace files. The final
status assertion remains the evaluator's responsibility, while the verifier
checks only the workspace result.
