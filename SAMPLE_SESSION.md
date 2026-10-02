# Illustrative three-person session

Anna, Ben, and Carla meet with William in English. **This is an authored example
with injected model decisions, not an actual API transcript or human-group test.**
It shows the intended behavior and exact server playback separately.

| Label | Meaning |
| --- | --- |
| **H** | Illustrative human speech or button use |
| **G** | Illustrative generated William wording and injected Decision action |
| **C** | Exact canonical Waha question or a source-playback annotation |
| **D** | Deterministic identity guard, local control, or state annotation |

Quoted H/G lines are invented. C questions below match
[the canonical fixture](research/canonical-01.001.001.json) exactly. The passage
annotation references its 25 `scriptureVerses`; it is not speech and does not
replace the actual source passage. Opaque identifiers in the offline rehearsal
are synthetic adapter evidence, not real voice enrollment.

The organizer arranges consent outside the application before startup. There is
no in-app consent checkbox or dialogue gate. Each
person speaks alone. Recognition has supplied an opaque usable sample for S1,
S2, and S3; those labels alone would not be enough.

## Names and thankfulness

The opening request includes the earlier localized welcome as deterministic **guidance**. William generates the spoken invitation: names come before thankfulness, both in the same contribution. This example is illustrative; a live model may vary its wording.

**G · William [opening / respond]:** Welcome to Discovering God! Let's catch up on how we're doing. Start by telling us your name, then share something you're thankful for since we last met. We'll hear from one person at a time.

**H · Anna [S1]:** I'm Anna. I'm thankful that my sister is recovering, and that we finally had time to eat together this week.

**D · Recognition annotation:** The illustrative transcript initially mishears Anna as Ann; this is a transcription error, not a guessed identity.

**G · William [introduce: Ann]:** Ann, did I catch your name correctly?

**H · Anna [S1]:** Actually, Anna, with an a at the end.

**G · William [introduce: Anna]:** Anna—have I got it now?

**D · State:** The correction keeps only the pending S1 evidence; it does not require another voice sample or borrow someone else's.

**H · Ben [S2]:** You got it.

**D · William [deterministic same-speaker guard]:** Let me hear from the person who just introduced themselves.

**H · Anna [S1]:** That's me.

**G · William [confirm_name]:** Thanks, Anna. Who would like to share their name and something they are thankful for next?

**H · Ben [S2]:** I'm Ben. I'm thankful my new job is starting to feel less overwhelming. A colleague showed me how to finish a task I'd been stuck on.

**G · William [introduce: Ben]:** Ben, did I hear your name right?

**H · Ben [S2]:** You got it.

**G · William [confirm_name]:** Who else would like to share their name and something they are thankful for?

**H · Carla [S3]:** I'm Carla. I'm grateful my neighbor helped me carry groceries home when I hurt my ankle. It made the week easier.

**G · William [introduce: Carla]:** Carla, is that your name?

**H · Carla [S3]:** Yes, that's right.

**G · William [confirm_name]:** Thanks, Carla.

**H · Anna [S1]:** William, that's everyone: Anna, Ben, and Carla.

**G · William [finish_enrollment]:** Let's hear how everyone is doing.

**D · State:** Three confirmed names; no separate roster yes gate. The opening already covered thankfulness. Source `f.001` is recorded here for comparison, **not replayed**:

**C · Reference [f.001]:** Welcome to a new session of "Discovering God". Let’s begin by catching up on how we are doing. Based on what has happened with you since the last time we met, what is something you are thankful for?

## Fellowship

**C · William [f.002]:** What has stressed you out this week, and what do you need for things to be better?

**H · Ben:** Work has been stressful. I need help asking questions instead of pretending I already understand.

**G · William [listen]:** *(Silence; no praise or extra study question.)*

**H · Anna:** I'm worried about my sister. A ride to her appointment would help.

**G · William [listen]:** *(Silence.)*

**H · Carla:** The ankle is frustrating, but I can drive Anna's sister on Thursday.

**G · William [listen]:** *(Silence.)*

**H · Ben:** William, does everyone have to answer every question?

**G · William [respond]:** Everyone is welcome to share or pass. Take the time you need.

**H · Anna:** William, let's move on.

**G · William [next]:** Let's consider our community.

**C · William [f.003]:** What are the needs of the people in your community, and how can we help each other meet the needs we’ve expressed?

**H · Anna:** My sister isn't the only person who needs transport. We could ask who needs rides this week.

**H · Ben:** I can help organize the times.

**H · Carla:** I'll send you the times I can drive.

**G · William [listen, once per contribution]:** *(Silence.)*

**H · Carla:** William, we're ready for the story.

**G · William [next]:** Let's listen together.

## Source reading and retelling

**C · William [f.008]:** Now, let's listen to a new story from God...

**C · Passage playback annotation [scripture]:** William reads the exact NLT Genesis 1:1–25 text assembled from the canonical fixture's ordered `scriptureVerses` (`GEN.1.1` through `GEN.1.25`). The actual source text is played, with source attribution available; these descriptive words are not spoken. No model reconstructs or translates the passage.

**H · Ben:** *(Presses Pause during passage playback.)*

**D · William [local pause]:** Paused. Say William, resume when you are ready.

**H · Ben:** *(Presses Resume.)*

**D · William [local resume]:** We can continue. Take your time, or ask me to repeat the current question.

**D · Playback:** The browser replays its interrupted passage prompt and finishes the remaining batch. The console replays its interrupted batch. Study position is preserved. The browser requires buttons while William speaks; microphone input is suppressed, so this is not a voice-wake example.

**C · William [a.001]:** Now, let’s have someone retell this passage in their own words, as though they were telling a friend who has never heard it. Let's help them if they leave anything out or add anything by mistake. If that happens we can ask, "Where do you find that in the story?"

**H · Anna:** The story begins with God creating. He speaks, and light appears. Then the story describes the sky, land, plants, lights in the sky, and creatures in the water, air, and on land.

**H · Ben:** I remember the repeated evening and morning as well.

**H · Carla:** And the story keeps saying that God saw that it was good.

**G · William [listen, once per contribution]:** *(Silence; the group helps with retelling.)*

**H · Ben:** William, let's take the next question.

**G · William [next]:** Let's hear what you notice.

## Discovery and direct Previous

**C · William [a.002]:** What does this story teach us about God, his character, and what he does?

**H · Anna:** I notice God speaks and creates.

**H · Ben:** William, why does God call it good?

**G · William [repeat]:** Let's let the group discover that together.

**C · William [a.002]:** What does this story teach us about God, his character, and what he does?

**H · Carla:** I want to listen to that repeated phrase again before I say more.

**G · William [listen]:** *(Silence; no interpretation supplied.)*

**H · Ben:** William, let's move on.

**G · William [next]:** Let's consider the next question.

**C · William [a.003]:** What do we learn about people, including ourselves, from this story?

**H · Carla:** William, could we go back one question? Anna hadn't finished.

**G · William [previous]:** Of course.

**C · William [a.002]:** What does this story teach us about God, his character, and what he does?

**H · Anna:** I also noticed the story describes what God makes in a particular order. I'd like to pay closer attention to that.

**G · William [listen]:** *(Silence.)*

**H · Carla:** William, now let's move on.

**G · William [next]:** Let's continue.

**C · William [a.003]:** What do we learn about people, including ourselves, from this story?

**H · Ben:** It reminds me that I didn't make the world I'm living in.

**H · Anna:** I think we depend on what has been provided.

**H · Carla:** I want to think more about that before answering.

**G · William [listen, once per contribution]:** *(Silence.)*

**D · Idle:** Silence leaves `a.003` selected. At most one idle request per lull may produce an optional invitation; it cannot navigate. No invitation is injected in this example.

**H · Anna:** William, we're ready for the next one.

**G · William [next]:** Let's think about this week.

## Application and closing

**C · William [a.004]:** How will you apply God's truth from this story in your life this week? What is a specific action or thing you will do?

**H · Anna:** I'll take a short walk each morning and pay attention instead of immediately checking my phone.

**H · Ben:** I'll ask my colleague for help tomorrow rather than hide that I'm stuck.

**H · Carla:** I'll follow through on the ride I offered Anna's sister.

**G · William [listen, once per contribution]:** *(Silence.)*

**H · Ben:** William, let's move on.

**G · William [next]:** Let's consider who you might share with.

**C · William [a.005]:** Who will you share a truth from this story with before we meet again? Do you know others who would also like to discover God's word in this app like we are?

**H · Anna:** I'll tell my sister about what we noticed.

**H · Ben:** I'll ask my colleague whether he'd like to hear the story.

**H · Carla:** I'll share with my neighbor and ask whether she wants to join us next time.

**G · William [listen, once per contribution]:** *(Silence.)*

**H · Carla:** William, next question, please.

**G · William [next]:** Let's arrange our next meeting.

**C · William [a.006]:** As our meeting comes to a close, let's decide when we will meet again and who will facilitate our next meeting.

**H · Anna:** Thursday at seven works for me.

**H · Ben:** Me too. I can organize the next meeting.

**H · Carla:** Thursday works. Thanks, Ben.

**G · William [listen, once per contribution]:** *(Silence.)*

**H · Ben:** William, let's close.

**G · William [next]:** Let's finish together.

**C · William [a.007]:** This has been a good time together. We encourage you to make note of what you said you will do, and to re-listen to this story in the days before we meet again. The facilitator can share the story text or audio if anyone doesn't have it. As we go, let’s ask the Lord to help us.

**D · Completion:** Selecting this prompt enters `closing`, not `done`. Pause/resume, repeat, and Previous remain available during output. After successful playback, the runtime calls `complete_playback(prompts)` and marks `done`; failed/interrupted playback cannot acknowledge completion. A failed playback restores the flow/history snapshot, or an interruption retains the replayable batch. Stop/close clears session-only names, identifiers, and controller history.

**D · Room conversation:** The app has ended and released its microphone. The following prayer is spoken in the room, outside the captured session.

**H · Anna:** God, help us follow through this week.

**H · Ben:** Amen.

**H · Carla:** Amen.

## Spanish without an authorized local NVI passage

This is a separate Spanish session branch, not mixed-language translation.

**H · Anna:** William, sigamos con la historia.

**G · William [next]:** Escuchemos juntos.

**C · Playback annotation:** The server plays the exact Spanish Waha `f.008` from its source assets. This annotation is not a quotation or a generated translation.

**D · William [manual_scripture]:** Por favor, que alguien lea Génesis, capítulo uno, versículos uno al veinticinco, en la NVI. Díganme cuando el grupo esté listo para continuar.

**D · State:** Index remains `scripture`. No substitute NVI text, remembered passage, retelling question, or grounding challenge is supplied.

**H · Carla:** *(Reads Genesis 1:1–25 from her authorized NVI copy.)*

**G · William [listen]:** *(Silence, including any lull; reading does not advance.)*

**H · Anna:** William, ya terminamos; sigamos.

**G · William [next]:** Continuemos.

**C · Playback annotation:** Only now does the server play the full exact Spanish Waha `a.001` from its source assets. The group retells and continues the same source-owned question order.

## What the diagnostics establish

For this rehearsal, decisions are injected (`provider=injected`, `model=offline`)
and human turns are authored. Runtime diagnostics report the selected action,
configured provider/model, and prompt origin: `generated` for G, `canonical` for
actual C playback, `deterministic` for D safeguards and the manual reading prompt.
The current canonical question is separate from procedural speech. Diagnostics
show actions and provenance, not hidden reasoning or model confidence.

Schema checks and voice/verse evidence guards do not prove semantic restraint.
The model is instructed to avoid theological answers, judgments, and praise for
ordinary contributions; this mocked example demonstrates those choices, not a
live model's reliability. It also cannot establish room recognition accuracy,
latency, echo handling, or multilingual translation quality.
