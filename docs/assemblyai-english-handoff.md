# Combined voice migration handoff

[PR #3](https://github.com/theJoshMuller/multilingual-dbs-facilitator/pull/3)
consolidates the AssemblyAI foundation in PR #2, the latest conversational
facilitator in PR #1 (`4922cf6`), and main's v4 Turbo/Mark defaults (`c583c17`).
See the PR for its current merge state. Integration work stays in:

`/home/yeshu/projects/multilingual-dbs-facilitator/.worktrees/assemblyai-multilingual`

Branch: `integrate/voice-migrations`. The local combined test moved to
http://127.0.0.1:8097/dbs/ because the independent Waha catalog worktree now owns
8095. The canonical main checkout, its staged edits/research, other worktrees,
live service, and Tailscale configuration were not changed by this integration.
Credentials, virtualenv, logs and browser profiles remain ignored in this worktree.

English DBS now defaults to PR #1's configured OpenRouter provider. It no longer
requires an active Codex session. `DBS_ENGLISH_FACILITATOR=codex-session` explicitly
selects the private native verification bridge. All verification here uses injected
or native decisions: no external model API was called. The runtime continues to
use one AssemblyAI Universal-3.6 Pro stream, browser 16 kHz mono PCM, official NIV,
and v4 Turbo/Mark. Ordinary English contributions are not translated. The original
Speechmatics mode and the ASR-only EN/TR proof remain selectable.

Josh's exact approved expanded welcome plays once without a model request.
Clear own-name contributions are recorded silently, uncertain names get a natural
clarification, and a final introduction may include readiness for the next question.
Seven-second silence allows one optional nudge and never advances the study.
Self-reported names and provisional same-label bindings are distinct; human identity
remains **UNVERIFIED**. Revised, PENDING, short, low-confidence and overlapping
turns cannot establish a voice binding. Pause/Resume preserves the study position;
AssemblyAI voice wake interruption remains pending. Consent belongs to the human
organizer before startup, outside the agent UX.

Integration verification: 250 offline Python tests, five Node client checks,
Ruff, both JavaScript syntax checks and `git diff --check`. The Chromium mocked
WebSocket/PCM check passed with one actual playback acknowledgment, zero provider
calls and zero JavaScript errors. Read-only review reproduced and corrected
uncertain-name blocking, unassigned-name locks, revised-name ownership, stale
partials/older finals, and final completion while paused. These regressions were
seen failing before their fixes. Remaining individual speech bugs are deferred
at Josh's request; the combined human English walkthrough is still needed.

Commands from this worktree:

```sh
.venv/bin/python -E -m unittest -q
.venv/bin/ruff check dbs_*.py test_dbs*.py smoke_dbs.py harness_bridge.py smoke_english*.py
node tests/test_dbs_client.cjs
node --check web/app.js
node --check web/pcm-worklet.js
git diff --check
DBS_SMOKE_BASE_URL=http://127.0.0.1:8097 .venv/bin/python -E smoke_english_playback.py
```

The real opening smoke requires the explicit native test bridge; it fails before
starting if the server is configured for an external model. Its output and test
logs stay ignored locally. Synthetic microphones prove transport and cleanup,
not human diarization, voice identity, microphone accuracy or echo performance.

The final bounded opening check used real YouVersion/AssemblyAI/ElevenLabs with
Chromium's synthetic microphone and the explicit native bridge. NIV 111 was
ready at 0.001s; the one exact f.001 opening arrived as audio at 5.335s. Stop
received Termination acknowledgment after 34s connected/34s audio, released the
microphone, and produced no JavaScript or provider errors. There was one prompt,
one audio message, and no external model request. This is transport/cleanup
evidence; the earlier consenting Josh/Kami EN/TR test remains the separate human
ASR evidence, with identity, within-sentence switching and overlap unverified.

The following sections retain earlier source and test evidence; their older
branch/merge/provider status is historical, superseded by this consolidation.

Scripture is exact official YouVersion NIV111, New International Version2011, publisher Biblica. All 25 individual Genesis1:1–25 verses and attribution were verified live, with all 11 full original English Waha questions. Only safe metadata was retained; the YVP key remains in the YouVersion project. The session fails closed for missing source text.

Verification from this worktree after adopting v4 Turbo/Mark: `.venv/bin/python -E -m unittest -v` passed 173 tests in 2.878s; `.venv/bin/ruff check dbs_*.py test_dbs*.py smoke_dbs.py harness_bridge.py smoke_english*.py` passed; `node --check web/app.js`, `node --check web/pcm-worklet.js` and `git diff --check` passed. Tests needed host execution because the existing PortAudio import hangs in the sandbox. Speech/model boundaries in the suite are mocked.

Focused regressions reproduced and fixed interrupted Scripture restoring the wrong index, repeated enrollment skipping a study question, and an empty saved harness token admitting a missing authentication header. Browser playback was also reproduced failing before its buffered PCM handler initialized the player; the same synthetic fixture now plays and acknowledges exactly once. The harness rejects browser Origin/Sec-Fetch headers and non-loopback or unauthenticated calls. Pending decisions time out without advancing the lesson and are cleared on Stop.

`smoke_english_playback.py` passed with a synthetic microphone, PCM and intercepted WebSocket, zero provider calls. An earlier `smoke_english_browser.py` passed with real YouVersion/AssemblyAI/ElevenLabs and Chromium’s synthetic mic: source111, prompt keys assistant/f.001, two audio messages played, no JS errors, microphone released, and Termination acknowledged after17 seconds of provider connection. The prepared opening needed no decision-queue reply; first audio arrived0.98s after Start. Official source preparation was already available in server RAM. It proves transport/playback/cleanup; it does not prove human microphone accuracy, identity or room echo. Earlier smoke attempts exposed the playback bug. The first human retry then reproduced a45s opening-queue timeout; browser diagnostics showed source ready at5s and no synthesized opening. Startup now uses the in-session-prepared welcome automatically, with source cached only in memory for up to five minutes and ASR ready before asking questions; no false human pass is claimed.

Voice-name recognition, overlap/room echo, within-sentence switching and a full human English walkthrough remain unverified. Names require eligible same-voice evidence and later confirmation; revisions revoke bindings. The group can study without verified names. Wake interruption is not enabled in this slice; Pause/Resume are available. Human test results belong to ignored worktree logs only with explicit consent; private speech is excluded from publication.

Changed files for this English follow-on: `dbs_english.py`, `dbs_controller.py`, `dbs_conversation.py`, `dbs_harness.py`, `harness_bridge.py`, `dbs_web.py`, `dbs_youversion.py`, `web/app.js`, `web/index.html`, focused harness/English/web tests, both English browser smokes, ASR browser smoke adaptation, README, design/handoff and runtime ignores. No service/Tailscale/main reconfiguration was performed.

Josh subsequently replaced the opening with his exact expanded explanation and combined name/thankfulness invitation, including the natural ready-for-next-question cue. This overrides f.001 only in the English test, marked `user_authored`; all remaining canonical questions retain their wording. Startup now emits one f.001 prompt, with no extra welcome. Focused tests cover exact selection and the single prompt; Repeat uses that same opening. The earlier0.98s/17s provider smoke measured the shorter previous opening, not this longer wording.

Follow-up review reproduced a connecting/Pause race and two opening-loss races through actual queue preemption. Startup controls now wait for ASR readiness, while Stop stays available. A pending opening survives a drained queue or early cancellation, Resume plays it once, and duplicate opening jobs are ignored. Paused microphone audio is gated and paused finals cannot trigger facilitation. Four new regressions failed before the fixes and pass now. The updated synthetic browser smoke also verifies Pause disabled during connection, enabled after readiness, and one actual PCM playback acknowledgment, with zero provider calls. Final read-only review found no remaining critical or important issue; the expanded-opening human retry remains pending.

The latest bounded `smoke_english_browser.py` tested the exact expanded opening with the approved v4 Turbo/Mark defaults and real YouVersion/AssemblyAI/ElevenLabs. Chromium used a synthetic microphone. Source 111 was ready at 0.001s; first audio arrived at 5.237s; exactly one f.001 prompt and one audio message played. Stop received Termination acknowledgment (34s audio, 35s connected), released the microphone, and produced no JS or provider errors. No decision-queue reply was required. This verifies the current opening transport and cleanup, not human speech accuracy or identity. The ASR compatibility PR now reports MERGEABLE/CLEAN; neither PR has been merged or deployed.
