# AssemblyAI merge handoff

The facilitator PR is based on `main` at `4e63be5`. It connects the existing
generative brain to the English/Spanish study path. The parallel
`spike/assemblyai-multilingual` worktree currently adds an **ASR-only proof**;
merging the two does not yet make AssemblyAI drive a facilitated study or add
participant translation.

## What was combined

On 2026-10-02, a disposable checkout at
`.worktrees/generative-assemblyai-check` combined the facilitator source with a
named-file snapshot of the migration's uncommitted source. Its
`snapshot-manifest.json` records SHA-256 hashes for each source file. No `.env`,
keys, recordings, logs, task database, or other runtime data was copied. The
migration worktree was neither edited nor committed. The migration-source manifest
fingerprint is `bc25e6c6f2f5fa4387597d6c706f46c9c5412a9ca42ed257cdfc7c5b6c641de9`.

The snapshot includes `dbs_assemblyai.py`, `dbs_mixed_turns.py`, `dbs_proof.py`,
`dbs_youversion.py`, their offline tests, and the migration's changes to
curriculum, browser dispatch, and UI. Those files are absent from this PR. This
evidence applies to that snapshot; later migration changes need another check.

## Merge result and resolution

The final rehearsal had textual conflicts in `dbs_web.py`, `web/app.js`, and
`web/index.html`: the migration added mode dispatch while this PR removed the
consent gate and updated provider disclosure. Keep mode validation and proof/study
dispatch while omitting the obsolete consent prerequisite. In the client keep the
mode selector and proof startup behavior without checkbox access or a `consent`
start field. Both branches changed the HTML provider disclosure. Keep the
migration's mode selector and proof notice, and retain the paragraph ID used by
its dynamic disclosure (`consent-description` in the tested snapshot). In study
mode disclose Speechmatics, ElevenLabs, and, when generative, OpenRouter plus its
model provider. In proof mode disclose AssemblyAI and explain that there is no
translation or spoken facilitation. The snapshot's rule-only study disclosure
would be inaccurate after this PR, so its `renderGuide()` study copy was changed
to branch on `runtime.state?.facilitation_mode === 'rules'`.

An automatic JavaScript merge also needed a semantic check: study diagnostics
must not overwrite the proof's ASR-only labels. This PR renders provider/model
labels only when `state.facilitation_mode` is present, with an offline client
regression. Preserve that guard alongside the migration's proof-specific labels,
turn revision display, and termination handling.

The user explicitly approved organizer-managed consent before startup, outside
the agent experience. This PR removes the inherited browser checkbox and server
consent prerequisite, while preserving microphone permission, provider disclosure,
and stop controls. The migration snapshot predates this decision. When merging,
preserve the approved behavior in both study and proof modes: do not restore the
checkbox, the client `consent` field, or server `consent` validation. Replace the
proof's old consent-required test with startup/invalid-mode tests that contain no
consent gate.

Merge this facilitator PR first, then bring the committed migration up to date
with `main`. Resolve disclosure using the rules above and rerun both suites.
Avoid merging a broad working-directory copy: the migration still has local
credentials and runtime files that must stay outside Git.

## Recognition boundary for the later study adapter

`ConversationController` has no recognition-provider imports. It accepts
`accept(text, speaker=..., identifiers=..., solo=..., source=...)` and exposes
`start`, `idle`, direct `control`, rendering/provenance, snapshots, playback
completion, and `close`. Existing adapters supply single-speaker transcripts and
real Speechmatics enrollment evidence. Text rehearsal explicitly bypasses voice
enrollment.

AssemblyAI's A/B labels are diarization labels, not reusable voice identifiers.
Do not pass `('A',)` as an identifier to satisfy enrollment, or describe a
ledger-confirmed label as biometric enrollment. The study adapter must explicitly
reconcile the migration's identity evidence and revisions with this controller's
name bindings. Keep language separate from identity, deduplicate completed turns,
revoke changed attribution, and distinguish uncertain/overlapping speech. The
proof session remains separate until that adapter exists. YouVersion helper
presence likewise does not establish that the study runtime now fetches official
Bible text.

## Verification

The combined snapshot passed **180 offline Python tests** and **3 Node client
checks** after the stated resolution. JavaScript syntax checks passed. Commands
used the existing Python environment and an explicit Waha asset root:

```sh
WAHA_ROOT=/home/yeshu/projects/waha-app \
  /home/yeshu/projects/multilingual-dbs-facilitator/.venv/bin/python -m unittest -q
node --test tests/test_dbs_client.cjs
node --check web/app.js
```

The feature branch also passes its documented Ruff command. Tests inject model
decisions or mock HTTP/recognition/speech transports; no model or speech provider
was called for this work. This is merge and state-machine evidence, not a real
group, multilingual recognition, translation-quality, or latency validation.
