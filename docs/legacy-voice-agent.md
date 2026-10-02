# Legacy voice-agent reference

Historical reference for `agent.py`, the general-purpose voice runtime that
preceded the DBS app. For the current project, start with the [README](../README.md)
and `dbs_web.py` or `dbs_agent.py`.

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
pip install -r requirements.txt

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
lk app create --template voice-assistant-frontend voice-agent-ui
cd voice-agent-ui   # put the same three vars in .env.local
pnpm install && pnpm dev
```

Open `http://localhost:3000`. Both sides are on localhost, so `ws://` works —
the hosted playground at agents-playground.livekit.io will *not* connect to
your local server, because an HTTPS page can't open an insecure websocket.

For a bare client that needs a token by hand:

```bash
lk token create --api-key devkey --api-secret secret \
  --join --room voice-agent-room --identity josh --valid-for 24h
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
