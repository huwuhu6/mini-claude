# AI-Assisted Development Workflow

## Project Goal

This project is interview-oriented. The goal is not only to complete the product, but to make the system explainable, testable, and defensible in technical interviews.

## Model Roles

GPT acts as the project brain, architect, task planner, and prompt generator.

DeepSeek or Gemini acts as reviewer/challenger. It reviews GPT-generated plans and checks for overengineering, missing edge cases, weak testing, and unclear interview value.

Claude Code acts as implementation engineer. It reads the repo, proposes an implementation plan, edits code, runs tests, and reports changes.

The human owner makes final decisions, approves plans, controls scope, and validates whether the work supports the interview goal.

## Standard Task Loop

1. GPT generates an implementation brief.
2. DeepSeek/Gemini reviews the brief.
3. GPT absorbs review feedback and generates the final Claude Code prompt.
4. Claude Code inspects the repo and proposes a plan before editing.
5. GPT reviews Claude Code’s plan if the task is important.
6. Claude Code implements the change.
7. Claude Code runs tests and outputs a change report.
8. GPT reviews the change report and diff.
9. DeepSeek/Gemini reviews again for important changes.
10. Docs are updated.
11. Tests are run.
12. Git commit is created.
13. GPT generates a handoff summary.

## Definition of Done

A task is not done until:
- The feature or fix is implemented.
- Relevant tests or manual verification have passed.
- GPT has reviewed the result.
- Important decisions are documented.
- Interview value has been captured.
- Git commit is created.

## Documentation Rules

Long-term knowledge goes into docs, not chat history.

Use:
- docs/architecture.md for system design.
- docs/roadmap.md for remaining work.
- docs/decision_log.md for important technical decisions.
- docs/interview_notes.md for interview talking points.
- docs/testing_plan.md for testing strategy.
- prompts/ for reusable prompts and task templates.

## Chat Rules

One ChatGPT conversation should focus on one deliverable task. Open a new conversation when switching to a new feature, major bug, architecture review, documentation pass, or interview preparation task.

At the end of each important conversation, generate a handoff summary with:
- Completed work.
- Key decisions.
- Files changed.
- Current state.
- Open issues.
- Next task.
- Interview relevance.