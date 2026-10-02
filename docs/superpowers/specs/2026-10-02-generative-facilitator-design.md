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
