# William — multi-speaker voice agent POC

One mic, several people, an agent that stays out of the way until it's wanted.

## Discovery Bible Study prototype — start here

Use **`dbs_agent.py`**, not the original general-purpose `agent.py`, for the
non-answering facilitator. The original scaffold is documented below; its
generative replies are deliberately not connected to the DBS speech pipeline.

```bash
cd /home/yeshu/projects/voice_dbs_demo

# English: canonical Waha questions and Genesis 1:1–25, NLT.
.venv/bin/python dbs_agent.py console --log-level info

# Spanish: canonical Waha questions, NVI only (participant reads the passage).
DBS_LANGUAGE=es .venv/bin/python dbs_agent.py console --log-level info

# No microphone or voice enrollment; exercise the same controller by typing.
DBS_LANGUAGE=es .venv/bin/python dbs_agent.py rehearse
```

Stop with **Ctrl-C**. `.env` supplies `SPEECHMATICS_API_KEY`; default English/
Spanish control parsing requires neither OpenRouter nor LiveKit Cloud.
Both languages now use **ElevenLabs Flash v2.5** (`eleven_flash_v2_5`) and
**Eric — Smooth, Trustworthy** (`cjVigY5qzO86Huf0OWal`). Speechmatics is used
for transcription and diarization, not DBS speech output. eSpeak has been
removed, with no robotic or alternate-provider fallback.

The ElevenLabs key is read from `ELEVENLABS_API_KEY` / `ELEVEN_API_KEY`, or the
existing `~/Developer/video-use/.env`; no key is copied into the demo. Set
`ELEVENLABS_ENV_FILE` to change that file. `ELEVENLABS_VOICE_ID` overrides Eric;
`ELEVENLABS_VOICE_ID_ES` overrides only Spanish. A stale `DBS_TTS_PROVIDER`
setting must be unset or changed to `elevenlabs`.

Speech output uses the streaming HTTP endpoint with 16 kHz PCM and Flash's
low-latency model. The controller currently validates the entire prompt's audio
before playback; first-byte timing is therefore not time-to-audible-response.
Spoken text, including names in confirmations, goes to ElevenLabs. Requests use
normal provider logging, not enterprise zero-retention mode.
Wear headphones or use a tested echo-cancelling speakerphone. A plain shared
speaker/microphone can feed William's voice back into enrollment and controls.

For a fresh Python 3.13 environment, install `requirements.txt` and run
`dbs_agent.py download-files`. The existing `.venv` is already installed.
PortAudio is the system audio prerequisite; no local TTS executable is needed.

### What to say

The opening now says:

> Welcome to a new session of Discovering God! Let's begin by catching up on how we're doing. First, can you say your name so I can recognize who is who? Then, based on what's happened to you since last time we met, what is something that you're thankful for?

Spanish uses the equivalent localized welcome. There is no spoken permission
request; the host app handles permissions before starting the voice session.

1. Each participant says their name and shares something they are thankful for
   since the last meeting: “My name is Josh, and I'm thankful for …” /
   “Me llamo Kami y estoy agradecida por …”. Speak one at a time; about 20 seconds
   works well. William requires a real Speechmatics identifier and at least five
   seconds of recognized speech.
2. The same speaker confirms their own name with “Yes” / “Sí”. Repeat for each
   person; no separate persistent enrollment command is needed for this mode.
3. Say “William, everyone is here” / “William, ya estamos todos”, then confirm
   the complete roster after everyone has shared. Mentioned friends do not count
   as participants. Thankfulness is covered during introductions, so William
   continues with `f.002` rather than repeating the old welcome/thankfulness prompt.
4. Discuss each question. William stays silent during ordinary discussion.
   Say “William, next question” / “William, siguiente pregunta”, then “Yes” /
   “Sí” to advance. Silence never advances the lesson.
5. Controls: “William, repeat”, “William, read the passage again”, “William,
   pause”, “William, resume”, “William, stop”. Spanish: “William, repite”,
   “William, lee el pasaje otra vez”, “William, pausa”, “William, continúa”,
   “William, detente”. Use these explicit phrases in the default rules mode.

The group is confirmed dynamically, up to `DBS_MAX_PEOPLE=7`. Speechmatics has
a separate `DBS_MAX_SPEAKERS=10` ceiling, not a claim that ten people attended.
The ceiling accepts 2–100. Voice identifiers and roster names are session-only;
this mode does not write `.speaker_profiles.json` or record a local audio file.
Speech still goes to Speechmatics; runtime/console logs may contain transcripts.
The host app must obtain everyone's agreement before opening the microphone;
for this standalone CLI, arrange that before launching it. Name confirmation
only checks identity and is not a permission grant. An STT reconnection
can change temporary speaker labels: restart introductions after a disconnect.

### Canonical content and Spanish NVI

Lesson `01.001.001` is read from the adjacent `../waha-app` data, or `WAHA_ROOT`:
`f.001`, `f.002`, `f.003`, `f.008`, Genesis 1:1–25, `a.001` through `a.007`.
The English passage is read from Waha's existing NLT cache. The custom onboarding
replaces spoken `f.001` with names and thankfulness. All remaining English and
Spanish questions are exact Waha spoken-question strings, not model rewrites;
the canonical source data is unchanged.
The independently extracted reference is `research/canonical-01.001.001.json`.
DMC source guidance is in `research/dmc-facilitation-evidence.md`.

**Spanish is NVI, never NBLA or Portuguese NVI-PT.** No authorized local Spanish
NVI passage was available for this run, so the prototype explicitly asks a
participant to read it and waits before the retelling question. Source:
https://www.bible.com/es/bible/128/GEN.1.NVI
It does not scrape, invent, translate, or substitute Scripture.

To enable automatic NVI reading, supply an authorized UTF-8 JSON file through
`SCRIPTURE_FILE=/absolute/path/passage.json`. Required fields are
`bibleTextId: "NVI"`, `languageId: "spa"`, and `verses`, an ordered array of
exactly 25 objects with `verseId` (`GEN.1.1` through `GEN.1.25`) and nonempty
`text`. An optional `copyright` string records attribution. Incorrect versions,
missing verses and changed ordering are rejected. Do not commit private or
restricted Bible exports.

### Multilingual scope — configuration is not full-language validation

`dbs_agent.py list-languages` lists the captured Speechmatics discovery catalog:
56 individual language codes and 5 combined-language packs. `STT_LANGUAGE`
selects recognition independently of `DBS_LANGUAGE`, the facilitation locale.
`STT_DOMAIN` is optional for domain-specific packs. Realtime enrollment does not
use batch `auto`/`multi` modes, and account entitlements still apply. The catalog
is not a promise of unrestricted code-switching or that every locale is runnable.

English and Spanish have tested deterministic controls and hand-authored UI.
Other locales require all of: canonical Waha spoken questions, a resolved Bible
cache or `SCRIPTURE_FILE`, support in ElevenLabs Flash v2.5, and
`DBS_LLM_PROVIDER=ollama` or `openrouter` for intent parsing.
Flash supports 32 languages, not all Speechmatics recognition languages;
unsupported speech-output locales fail explicitly rather than using eSpeak.
`WAHA_LANGUAGE` disambiguates the Waha locale. Missing assets fail at startup;
there is no silent English or alternate-Bible fallback.

Optional model routes emit only validated `{intent, name}` objects, never
answers. A supplied `DBS_PROMPTS_FILE` contains a reviewed translation with the
same keys/placeholders as `dbs_prompts.EN`; otherwise only prototype UI copy is
translated at startup. Neither curriculum nor Scripture is model-translated.
These optional routes and other locales have not been live-validated. Immediate
stop/pause detection is currently English/Spanish; **Ctrl-C remains universal**.
If choosing OpenRouter, source `~/.config/shell/profile` at launch rather than
copying its key into this repository. Participant turns then go to that provider.

### Verification and prototype boundaries

```bash
.venv/bin/python -m unittest -v
uvx ruff check dbs_*.py test_dbs*.py smoke_dbs.py

# Real API calls using synthesized test speech, not microphone recordings.
.venv/bin/python smoke_dbs.py --language en
.venv/bin/python smoke_dbs.py --language es
```

The audio smoke test sends generated speech through the actual Speechmatics
plugin, obtains a real voice identifier, then exercises name/roster confirmation
as text through the controller. It is not a multi-person recognition-accuracy
test. Runtime regressions use mocks for cancellation, provider failure and
serialization; they do not prove acoustic barge-in accuracy in a room.

Known limits: English/Spanish only are live-tested; NVI is participant-read
unless explicitly supplied; group diarization/echo still needs a human trial;
rules mode expects explicit commands; danger-phrase detection is intentionally
limited and is not emergency monitoring. Stop/pause commands preempt after ASR
finalization, not instantaneously. Resuming an interrupted reading replays the
interrupted prompt batch from its beginning rather than silently skipping it.

## Original general-purpose scaffold (not DBS)

### Accounts you need

Two:

1. **Speechmatics** — STT with realtime diarization, plus TTS. Free tier is
   480 min/month, 20 concurrent realtime sessions. One key covers both ends of
   the pipeline. → `SPEECHMATICS_API_KEY`
2. **OpenRouter** → `OPENROUTER_API_KEY`. Handled by the OpenAI plugin via
   `openai.LLM.with_openrouter()`, so there's no extra package and you switch
   models by editing a string. Set `LLM_MODEL` to override the default.

**LiveKit Cloud** is a third, but you don't need it for `console` mode — that
runs against your machine's own mic and speakers with no room involved. Set the
vars anyway if you plan to move to browser clients later; the free tier is
plenty and `lk cloud auth && lk app env -w .env.local` fills them in.

## Setup

```bash
# System audio deps (Debian/Ubuntu)
sudo apt install portaudio19-dev python3-dev

python -m venv .venv && source .venv/bin/activate
pip install "livekit-agents[speechmatics,openai,silero]~=1.4" python-dotenv sounddevice

# .env.local
cat > .env.local <<'EOF'
SPEECHMATICS_API_KEY=...
OPENROUTER_API_KEY=...
# LLM_MODEL=anthropic/claude-sonnet-4.5
# LIVEKIT_URL=
# LIVEKIT_API_KEY=
# LIVEKIT_API_SECRET=
EOF

# Fetches the Silero VAD weights — required before first run
python agent.py download-files

python agent.py console
```

Wear headphones, or William will hear his own voice and transcribe it as a
fourth speaker. The real fix is speaker filtering (below); headphones are the
five-second version.

## Enroll known speakers

[Speechmatics speaker identification](https://docs.speechmatics.com/speech-to-text/features/speaker-identification)
can generate an encrypted voice identifier from a 5–30 second sample of one
person speaking alone. William saves those identifiers locally and sends them
back to Speechmatics in future sessions, which lets the transcript use a
stable name such as `[Speaker Josh]` instead of a temporary label such as
`[Speaker S1]`.

Run enrollment once per person before starting the agent:

```bash
python agent.py enroll Josh
python agent.py enroll Kami
python agent.py list-speakers
python agent.py console
```

Enrollment records 20 seconds by default. Use `--seconds` with any whole-number
duration from 5 through 30 seconds to change it:

```bash
python agent.py enroll Josh --seconds 30
```

Running enrollment again with the same name appends another identifier. This
can improve recognition when the samples represent different microphones,
positions, or room conditions. Use `--replace` to discard that person's old
identifiers, or remove a profile entirely:

```bash
python agent.py enroll Josh --replace
python agent.py remove-speaker Josh
```

Profiles are stored in `.speaker_profiles.json` with owner-only permissions.
The identifiers are encrypted and scoped by Speechmatics to the account,
project, and recognition model, but they are still derived from a person's
voice: keep this file private and get each person's consent before enrolling
them. Re-enroll everyone after changing the Speechmatics recognition model.

## Running the LiveKit server locally

Console mode needs no server at all. You only want one when you're ready for a
real room with a browser client in it.

```bash
docker run --rm \
  -p 7880:7880 -p 7881:7881 -p 7882:7882/udp \
  livekit/livekit-server --dev --bind 0.0.0.0
```

`--dev` starts an insecure single-node server with a fixed, well-known
credential pair. Fine on a laptop, never anywhere else:

```
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
```

Then run the agent as a worker instead of a console app:

```bash
python agent.py dev
```

It connects to the local server and waits. Nothing happens until a participant
joins a room — that's the job trigger.

### A client to join with

```bash
lk app create --template voice-assistant-frontend william-ui
cd william-ui   # put the same three vars in .env.local
pnpm install && pnpm dev
```

Open `http://localhost:3000`. Both sides are on localhost, so `ws://` works —
the hosted playground at agents-playground.livekit.io will *not* connect to
your local server, because an HTTPS page can't open an insecure websocket.

For a bare client that needs a token by hand:

```bash
lk token create --api-key devkey --api-secret secret \
  --join --room william-poc --identity josh --valid-for 24h
```

### Reaching it over Tailscale

Two separate problems, and the second one is the one that wastes an evening.

**1. Media path.** `--dev` advertises the host's IP addresses as ICE
candidates, and it may not pick your tailnet one. Pin it:

```bash
docker run --rm --network host \
  livekit/livekit-server --dev \
  --node-ip $(tailscale ip -4)
```

`--network host` matters on Linux: inside a bridge network the server can't
see the `tailscale0` interface, so it advertises a container IP that nothing on
the tailnet can route to. Media flows direct over UDP 7882 to that node IP —
`tailscale serve` proxies TCP only, so it can't carry it.

**2. Secure context.** A browser only grants microphone access on
`localhost` or HTTPS. `http://100.x.y.z:3000` gets you neither, so
`getUserMedia` fails silently-ish and you'll blame the agent. Terminate TLS
with Tailscale (enable MagicDNS + HTTPS certificates in the admin console
first):

```bash
tailscale serve --bg 3000                # https://<host>.<tailnet>.ts.net
tailscale serve --bg --https=8443 7880   # wss://<host>.<tailnet>.ts.net:8443
```

The second one exists because an HTTPS page can't open a `ws://` connection.
Point the frontend at the wss URL:

```
LIVEKIT_URL=wss://<host>.<tailnet>.ts.net:8443
```

The agent itself still connects over plain `ws://localhost:7880` — it's on the
same machine as the server and doesn't need any of this.

### What you give up versus Cloud

Enhanced noise cancellation is Cloud-only, so drop `noise_cancellation` from
`RoomInputOptions` if you add it. Everything else in this scaffold runs
locally: the Speechmatics plugin talks to Speechmatics directly rather than
through LiveKit Inference, and Silero VAD is a local model. If you later switch
the STT over to LiveKit Inference to skip the Speechmatics key, that's a Cloud
dependency and it won't work against `--dev`.

One caveat for your actual use case: a single shared microphone means one
browser tab publishing audio. The room only starts earning its keep when you
want remote participants or a UI. For two people at one laptop mic, console
mode is still the shorter path.

## What to expect on first run

Talk with someone else in the room. Watch the log lines — every turn is printed
with its `[Speaker S1]` tag before anything is sent to the LLM. Two things will
be obviously wrong at first, and both are tuning problems rather than design
problems:

- **Unknown-speaker label drift.** Enrolled speakers can keep a stable name,
  but someone without a profile may still come back as a new speaker ID after
  going quiet for a while.
- **Turn boundaries.** `min_endpointing_delay` is the dial. Too low and William
  treats a mid-sentence breath as a turn; too high and he feels sluggish when
  addressed directly.

## Where the logic lives

| Behavior | Where |
|---|---|
| Personality, speaker-tag handling, reply length | `SYSTEM_PROMPT` |
| What counts as "he was called" | `WAKE_PATTERN` |
| How long a lull runs before he speaks | `IDLE_REPLY_SECONDS` |
| Suppressing an unwanted volunteer reply | `SKIP_TOKEN` handling |
| Enrolled voice profiles | `.speaker_profiles.json` |

The core trick is in `on_user_turn_completed`: it raises `StopResponse` on
every human turn, which kills the framework's automatic reply. Speech is then
triggered explicitly — by the wake word, or by the silence watchdog. That
inversion is what separates a group participant from a request/response
assistant.

## Known gaps in this scaffold

- **SKIP isn't enforced.** The prompt tells William to reply `SKIP` when he has
  nothing to add on a lull, but nothing intercepts it before TTS. Override
  `tts_node` (or `llm_node`) to swallow that response.
- **No echo suppression.** See headphones, above. Production answer is
  `stt.update_speakers(ignore_speakers=[...])` once you know William's label.
- **Buffer grows unbounded** between replies. Cap it if the group talks for a
  long stretch without addressing him.
- **Model string** — `anthropic/claude-sonnet-4.5` is a starting point. Check
  the OpenRouter model list and set `LLM_MODEL`. For a group conversation the
  latency budget is looser than a 1:1 call, since William only speaks on a
  wake word or a lull, so a slower/stronger model is more affordable here than
  in a normal voice agent.
