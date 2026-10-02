# Connect generative DBS facilitation

The approved change connects the existing Facilitator and ConversationFlow to browser, console, and text rehearsal. William generates brief conversational facilitation, understands natural identity confirmations, stays silent for ordinary discussion, and uses direct navigation. Exact original Waha questions and authorized Scripture remain server-owned playback assets.

## Shared controller

Add ConversationController in dbs_controller.py. It accepts transcript text and explicit speaker evidence, builds bounded context, requests a validated Decision, applies it to ConversationFlow, and renders generated or canonical prompts. It owns session-only history and selected-action diagnostics. It has no Speechmatics, AssemblyAI, LiveKit, or browser dependency. Recognition adapters supply speaker label, single-speaker status, and opaque enrollment evidence; a label alone never establishes identity. Text rehearsal deliberately bypasses voice enrollment.

Browser, console, and rehearsal default to generative facilitation; DBS_FACILITATION_MODE=rules retains the existing flow. DBS_LLM_PROVIDER keeps its optional intent-parser meaning in rules mode. Generative mode uses the existing facilitator provider and fails explicitly if configuration is unavailable. Tests inject decisions and make no model API calls.

## Runtime behavior

Participant turns, opening, and idle work reach the shared controller. Direct buttons execute known actions without a model request. Local stop/pause/danger controls retain cancellation when input is being accepted. The browser is half-duplex: microphone audio is gated while the model or playback is busy, so use Pause/Stop buttons to interrupt then. Per the current repository instructions, the organizer obtains consent before startup outside the app; remove the inherited checkbox and consent gate. Generated text reaches ElevenLabs unchanged. Silence never advances; an idle invitation is optional, generated, and limited to one per lull. The canonical question remains separate from currently spoken procedural text.

Story introduction, exact available passage, and retelling play in order. Missing Spanish NVI asks a participant to read and waits for explicit continuation. Navigation respects bounds; final closing completes after playback. Resume preserves existing interrupted-batch semantics. Snapshots include flow and history so provider/playback errors cannot silently move the lesson. Clear all session-only context and close the facilitator client on shutdown. Diagnostics identify action, provider/model, and generated/deterministic/canonical prompt origin.

## AssemblyAI merge

The parallel worktree currently adds an assemblyai-proof mode, provider transport, a turn ledger, and YouVersion source work. Leave those files and that worktree untouched. Keep browser edits localized to the study session and expose a provider-neutral controller API for future recognition adapters. Do not implement mixed-language translation in this PR. Rehearse combining this branch with a sanitized snapshot of the migration in a temporary checkout, record any shared-file conflicts and their resolution, and keep the PR independent of the uncommitted migration.

## Evidence

Test controller decisions/rendering, identity evidence, text mode, manual Scripture, navigation/completion, cancellation and rollback, and both runtime entry points. Keep existing rules regressions explicit about mode. Run the offline suite and lint, request a harness-native code review, and test the combined AssemblyAI snapshot. No external LLM calls, real participant audio, deployment changes, or source edits in main are authorized by this implementation.

## Combined opening invitation

Josh's follow-up asks for the first question to work like the earlier welcome while inviting the person's name at the same time. Keep the opening LLM-generated; strengthen its instruction to welcome the group, ask each person to begin with their own name, and share thankfulness since the last meeting in one contribution. Supply the existing English/Spanish welcome only for the opening event as wording guidance. The first welcome already covers f.001; later canonical questions remain server-owned. Subsequent invitations request names and thankfulness together, with optional clarification for uncertain names and existing voice-evidence guards.

Verify opening-only localized guidance in mocked requests and an injected combined contribution through name confirmation and continuation without repeating f.001. These checks verify request construction and controller mechanics, not real model compliance. Update the audit sample and restart only the isolated localhost8096 dev service.

## Group readiness and silence follow-up

Josh's live trial showed an introduction entering confirm_name while generated speech merely acknowledged sharing. His requested replacement uses one group round of names and thankfulness, an explicit everyone-shared/readiness signal, and a nudge after seven seconds of silence. A clear introduce records a participant using real solo voice evidence; clarify_name reserves a pending name and place only for uncertainty. Same-speaker clarification, duplicate-name/capacity bounds and matching-evidence corrections remain server guards. A finish_enrollment decision can carry the final speaker's own name to record that introduction before one canonical transition. No attendance is inferred from silence or labels.

Generative browser and console sessions request one generated nudge after seven seconds while listening in introductions, uncertain-name clarification or the study. Busy output, active speech, pause and queued input suppress it, and recently stale queued work is discarded. Participant input or direct navigation/resume rearms it; an idle event can never navigate. Rules retain 25 seconds. Verify clock boundaries, last-speaker completion, identity/capacity guards and full roster/question rollback on failed playback without provider calls. The sample distinguishes the deterministic timer/state/guards from generated speech and interpretation of readiness.

## Silent introduction follow-up

Josh's next trial showed an immediate generated acknowledgement and invitation after a clear name/thankfulness contribution. Successful introduce actions now produce no speech, enforced by the server even if the model supplies words. Diagnostics describe the actual silent turn and history contains only what was spoken. Model instructions also require silent introductions and ordinary continued thankfulness sharing. Clarification and evidence guards, direct procedural requests and explicit group readiness remain available. The existing seven-second idle event generates the next-person nudge; new participant contributions restart the silence interval. Verify two clear contributions without any synthesis call, no early nudge, and a generated nudge at the silence boundary using injected decisions and a fake clock.

## Contextual readiness follow-up

Josh clarified that a natural intent to begin or continue should finish introductions, including a reply to William's readiness invitation, without an explicit everyone-shared declaration. The earlier instructions were too restrictive. Broaden the LLM's semantic guidance rather than adding deterministic phrase matching. Interpret the current turn with the phase and spoken history; distinguish actual requests from quotations, story retelling, future plans or asking to begin one's own contribution. A readiness request authorizes continuing with the registered group, not an attendance claim. Keep server execution, unresolved-name/evidence guards and silence prohibition unchanged. The mocked transport check proves readiness text and prior invitation reach the LLM, its selected transition plays exact source questions, and quoted speech still requires a model decision. It does not establish live model accuracy.
