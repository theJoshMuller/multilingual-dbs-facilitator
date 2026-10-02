# Generative facilitator implementation plan

The approved design is in ../specs/2026-10-02-generative-facilitator-design.md. Beads issue voice-dbs-ah6 is the authoritative tracker. This document describes execution; it is not a second task-status list.

## Shared controller and flow

Create dbs_controller.py and test_dbs_controller.py; update dbs_conversation.py and add test_dbs_conversation.py. The controller accepts a lesson, language, injected facilitator, participant ceiling, and text-mode flag. Expose async start(), idle(), accept(text, *, speaker='', identifiers=(), solo=True, source='voice'), close(); synchronous control(action), render(prompt), snapshot(), restore(snapshot); and flow, lesson, history, last_decision, provider/model metadata.

Write failing tests for unchanged generated rendering, exact canonical rendering, a three-person natural-confirmation session, required real voice evidence, explicit text-mode bypass, and snapshot/restore of history. Also test start/idle events, missing Spanish Scripture waiting, final completion and Previous/Next bounds. Implement the smallest controller and flow changes that pass. Validate with the existing Python 3.13 environment: python -m unittest -v test_dbs_controller test_dbs_conversation test_dbs_facilitator.

## Browser and console connection

Write failing browser/runtime tests before updating dbs_agent.py and the study-session parts of dbs_web.py. Select DBS_FACILITATION_MODE=generative|rules, default generative, and explicitly select rules in existing fixtures. Feed raw participant text and normalized identity evidence to the shared controller. Route buttons and urgent controls without waiting for a model. Support generated rendering, full-state snapshots, canonical question display and controller.close(). Keep microphone/provider transport changes small so AssemblyAI mode dispatch remains independent.

Validate default construction without real calls, same-speaker name confirmation, cancellation during a slow decision, direct Previous/Next, pause/resume playback, and restoration of flow/history on failed playback. Run python -m unittest -v, Ruff on modified Python, and node --check web/app.js if client code changes. Local HTTP test servers may require socket permission; provider calls remain mocked.

## Review, audit, merge rehearsal and PR

Update README, the source-labeled sample session, and browser diagnostic labels. Request independent code review and resolve material findings. Snapshot the AssemblyAI worktree's tracked/untracked implementation changes without secrets, logs, local databases or task state; apply to a temporary checkout and run both suites. Describe merge order and conflict resolutions in docs/assemblyai-integration-handoff.md and the PR body. Close and export Beads state with the code, commit, push feat/generative-facilitator, and create a PR against main. Preserve the worktree and do not merge or deploy.
