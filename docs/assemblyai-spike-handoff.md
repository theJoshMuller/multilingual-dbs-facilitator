# One-mic AssemblyAI spike handoff

This is a working ASR-only browser slice with a real two-person EN/TR label smoke test. **Human name binding remains UNVERIFIED.** Within-sentence switching and room/echo performance still need evidence. William does not speak in this proof. Conversational mixed-language facilitation, participant translation and Scripture playback have not been enabled. The original single-language study path remains selectable.

Worktree: `/home/yeshu/projects/multilingual-dbs-facilitator/.worktrees/assemblyai-multilingual`
Branch: `spike/assemblyai-multilingual`, based on `4e63be5`
Local proof: <http://127.0.0.1:8095/dbs/>

Josh has authorized publishing this ASR branch as a PR. No merge, deployment, service configuration or Tailscale route change was performed. `.worktrees/` was excluded in the canonical repository's local Git ignore metadata before worktree creation. Existing research files and concurrent changes in the main checkout were preserved. The worktree does not track the later, independently changed main HEAD.

## Running the proof

From the worktree, use `.venv/bin/python dbs_web.py`. The server left for this handoff has a 15-minute process limit; restart with that command when it expires. Its worktree-local configuration selects loopback port 8095, allowlists only localhost/127.0.0.1 origins, and uses the rules parser for the original browser study. The AssemblyAI key is read only from this worktree's ignored, owner-only `0600` `.env`; an environment variable or the main checkout's `.env` cannot substitute for that file. No ElevenLabs credential was copied or used by the proof.

Refresh the page, select “One mic · EN/TR ASR proof,” and start only after everyone present consents. One group has one provider connection. The existing microphone worklet sends 16 kHz mono signed little-endian int16 PCM in 100 ms frames over the private WebSocket bridge. Only the server authenticates to AssemblyAI. Stop releases microphone tracks immediately, then keeps the bridge open briefly for final revisions and Termination.

The provider parameters select `universal-3-6-pro`, `speaker_labels=true`, `language_detection=true` and `language_codes=["en","tr"]`. Language steering permits code-switching; it does not turn a reported language into a person's identity. Speaker label, language code, confidence and verified human name are separate fields. The proof never automatically binds names. Turn records and speech remain in memory; no audio, transcript or voice profile is automatically persisted. Sessions have a three-minute lifetime, microphone-stall detection, bounded startup/cleanup and explicit Terminate handling.

For human proof, two consenting people share the microphone, introduce names in full sentences, alternate at least two substantial turns each, and include a within-sentence language switch. Inspect labels throughout and after Stop. A vendor label is not a verified human name. Short/PENDING/uncertain turns cannot enroll; same-label confirmation requires distinct final turns, at least six seconds of evidence, and high finite turn and word confidence. Revisions and downgraded evidence revoke bindings. These enrollment helpers are offline-tested groundwork and are **not a live enrollment flow**. Overlap detection can reject overlapping reported word intervals, but cannot recover overlapping voices the vendor collapsed into one speaker; overlap performance remains unverified.

## Evidence and verification

Verification commands are run from the worktree. Existing suite coverage includes curriculum, controller, browser security and transport behavior. External LLM requests were not made.

| Command/evidence | Result |
| --- | --- |
| `.venv/bin/python -E -m unittest -v` | 148 tests passed in 5.431 s; ignored `logs/pr-regression.log` |
| `.venv/bin/python -m unittest -v test_dbs_assemblyai test_dbs_mixed test_dbs_proof test_dbs_youversion` | 45 tests passed (including portable source fixtures) |
| `.venv/bin/ruff check dbs_assemblyai.py dbs_mixed_turns.py dbs_proof.py dbs_youversion.py test_dbs_assemblyai.py test_dbs_mixed.py test_dbs_proof.py test_dbs_youversion.py smoke_assemblyai_browser.py smoke_assemblyai_bridge.py` | Passed |
| `.venv/bin/ruff check dbs_*.py test_dbs*.py smoke_dbs.py` | Passed after import-order, literal and context-manager style fixes in two existing test files; no production behavior changed |
| `node --check web/app.js` and `node --check web/pcm-worklet.js` | Passed |
| `.venv/bin/python smoke_assemblyai_browser.py` with local server running | Passed with synthetic microphone and intercepted WebSocket; seven 3200-byte PCM frames observed on latest run, no JS errors, final dedup, PENDING display, final revision, Termination and released microphone verified; no provider call |
| Real bounded AssemblyAI silence stream | Requested model/diarization confirmed; Begin and Termination received; 3 s audio / 3 s provider session, 3.86 s wall time. Real authentication/transport/cleanup evidence, no speech or diarization evidence |
| `.venv/bin/python smoke_assemblyai_bridge.py` | Latest real provider check through the fixed bridge: 3 s synthetic silence / 4 s connected, 4.202 s wall; Termination received; zero transcript events and zero remaining group sessions. Sanitized metadata in ignored `logs/bridge-smoke-final.json` |
| First live browser microphone test | One person speaking three languages, as clarified by Josh. Seven short final turns; all speaker labels PENDING; language reports it/fi unreliable. Josh confirmed the final Spanish transcription correct. No two-person or human-name proof |
| Two-person EN/TR test | Two consenting participants reported a successful shared-mic trial: final labels A/A/B/B/B/A with languages en/en/tr/tr/tr/tr; The English-first participant explicitly confirmed his final Turkish contribution. Two final revisions reached the display; 38 s audio / 38 s connected, Termination acknowledged. Demonstrates observed voice separation and between-turn language switching; no verified names or within-sentence result |

Josh explicitly permitted retaining test transcripts and timings locally; private speech is excluded from the published branch. The first user-reported stopped display is retained only in ignored, `0600` `logs/reported-mic-test1.json`, with its single-speaker context corrected. The two-person stopped display and Josh's assessment are in ignored, `0600` `logs/reported-mic-test2.json`. No word-error rate is asserted without a ground-truth transcript. Future tests require the consent of all people present.

The live trial exposed a disconnect race: a final browser write could fail after the peer began closing, and the WebSocket handler could then skip capacity release. Both failures were reproduced by focused tests before the fix. The proof now treats a connection write failure as a stop signal; the handler always releases its session/socket slot and consumes sanitized task failures. Review found a similar early-handshake race, also reproduced before fixing: six synthetic hello disconnects previously exhausted socket admission; the seventh socket now succeeds. The fixed real bridge check returned to zero active sessions. The final server restart loaded both fixes with zero active groups. The publication regression run needed host access because the existing PortAudio import stalled under sandbox isolation; speech providers remained mocked.

The offline browser runner uses a disposable short pathname under `/tmp` pointing into the worktree's `.cache/browser`, because Chromium's Unix socket cannot fit the full worktree pathname. The actual profile/test artifacts stay inside the worktree; the alias is removed on exit.

## Official Scripture and canonical questions

`dbs_youversion.py` imports the existing integration configured by `YVP_SERVER_FILE` (locally `/home/yeshu/projects/youversion_platform/server.py`) after reading that project's `agents.md`. The YVP key is read in place from `/home/yeshu/projects/youversion_platform/.env`, never copied, logged or exposed to the browser. Only official server-side reads are used. Validation checks exact ID/language/edition, publisher identity and copyright, Genesis 1 verse coverage, and passage ID/content. There is no generated verse, machine translation or silent edition fallback.

Live API readiness was checked on 2026-10-02 at 17:32 UTC. Only metadata, attribution and readiness were saved in ignored `0600` `logs/source-readiness.json`; passage content was not persisted.

| Selected edition | Official API result | Feature status |
| --- | --- | --- |
| English NLT, version 116 | HTTP 404 | Unavailable; Scripture reading fails closed |
| Turkish TCL02, version 170 | HTTP 404 | Unavailable; Scripture reading fails closed; catalog YTC 3844 is a different edition and was not substituted |
| Spanish NVI, version 128, “Nueva Versión Internacional 2025” (API abbreviation NVI-S, localized NVI), publisher Biblica | HTTP 200, exact metadata/attribution and Genesis 1:1–25 coverage/text verified | Source adapter ready; no NBLA substitution |
| Original Waha canonical questions in English, Turkish, Spanish | All 11 required original questions present per language | Verbatim availability verified |

On 2026-10-02 at 18:29 UTC, the official API also verified **NIV version 111**, exact edition “New International Version 2011” (NIV11 / localized NIV), publisher Biblica, and Genesis 1:1–25 text/coverage. Josh approved NIV for the next English slice. The current adapter selection remains NLT until that integration change; this is availability evidence, not enabled playback. Only metadata is saved in ignored `logs/niv-readiness.json`.

The existing single-language study still obtains its lesson from Waha curriculum/cache. It has not been relabeled as YouVersion-based. The proof has no Scripture action; the official source adapter is separate readiness groundwork. Full original Waha wording and the base branch's navigation, including its Previous UI, were preserved.

## Files changed

| Files | Purpose |
| --- | --- |
| `dbs_assemblyai.py`, `dbs_proof.py` | Server-only provider transport and bounded ASR group session |
| `dbs_mixed_turns.py` | Final/partial handling, revisions, conservative binding and mixed-only translation eligibility; no translator/model call |
| `dbs_youversion.py` | Exact official source and canonical-question validation |
| `dbs_web.py`, `web/app.js`, `web/index.html` | Proof mode on existing bridge, consent/debug fields, dedup/revisions, Stop drain; original mode remains selectable |
| `dbs_curriculum.py` | Resolve explicitly configured Waha root at runtime for isolated worktree execution |
| `test_dbs_assemblyai.py`, `test_dbs_mixed.py`, `test_dbs_proof.py`, `test_dbs_youversion.py`, `test_dbs_web.py`, `test_dbs_facilitator.py`, `test_dbs_tts_stream.py` | Focused provider, identity, language, translation-gate, cleanup, source and consent tests |
| `smoke_assemblyai_browser.py`, `smoke_assemblyai_bridge.py` | Reproducible offline Chromium UI check and bounded real-provider silence check |
| `.gitignore`, `docs/assemblyai-spike-design.md`, this handoff | Worktree runtime exclusions, scope and evidence |

The full mixed facilitator is gated by real two-person EN/TR evidence and exact official English/Turkish Scripture readiness. The translation gate is tested policy only; no participant translation or name acknowledgment is implemented. Runtime translation must also comply with the instruction prohibiting external LLM APIs/model-calling scripts. Do not present this spike as a finished mixed-language DBS session.

An earlier shell-startup configuration unexpectedly emitted credential environment values in tool output. Values are not reproduced in these artifacts. All subsequent commands use a non-login Bash shell to avoid that startup behavior; this spike did not modify shell configuration or rotate unrelated credentials.

Official references: [Multilingual transcription](https://www.assemblyai.com/docs/streaming/multilingual-transcription), [diarization and revisions](https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels), [streaming quickstart](https://www.assemblyai.com/docs/streaming/getting-started/transcribe-streaming-audio).
