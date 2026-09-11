# Progress-aware Governance Candidate v4

This note records the engineering argument for Candidate v4. Official
performance claims must use only the corrected DEV multi-trial result; Canary
and failed experiments are diagnostic evidence, not benchmark claims.

## Problem and baseline

Action-level repetition could not distinguish normal recovery from an invalid
loop. Candidate v1 added Progress, Blocker, and Completion reasoning, which
reduced false stops, but still missed semantic state oscillation and
unsupported completion after a blocker.

The v2 experiment treated the absence of strong progress as stagnation and
could terminate during exploration. The v3 experiment validated the direction
of neutral activity and semantic oscillation, but taking over the recovery
chain increased false positives. These results are design evidence, not final
performance data.

## Candidate v4 decisions

v4 starts from v1 and adds only two independently attributable capabilities:

1. **Semantic Oscillation Detection** uses normalized semantic/observation
   fingerprints, supports period 2 and 3, requires two complete cycles, and
   requires no explicit generic verification improvement. It is independent of
   language, task ID, state names, and workspace diff.
2. **Conservative Completion Guard** runs only when the model attempts a
   no-tool completion. A first ordinary failure is not enough to block
   completion; confirmed blockers require generic verification evidence before
   they are considered resolved. The first unsupported completion requests a
   replan, and a second one is blocked.

The design intentionally protects v1 recoverability and does not introduce a
new recovery episode/state machine or change LoopGuard, Failure Intelligence,
WorkspaceStateGuard policy, thresholds, or Progress Governance rules.

## Benchmark and reporting

The benchmark remains frozen at `7648e64`, with DEV/HOLDOUT, multi-trial
execution, and Python/JVM/Node/Shell coverage. Governance metrics and task
outcome metrics are reported separately. Final résumé claims must be filled
from the formal corrected DEV×3 (and any later formal ×5), never from a local
Canary or a favorable single Case.
