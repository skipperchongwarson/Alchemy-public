# VOICE DNA

Reusable linting and editorial-review support for Skipper’s writing voice.

Project-specific governing files take priority. In Make, `make/voice-and-writing.md` governs style and mechanics. Other projects have their own governing files for strategy, relationship judgment, and calibration.

Apply this file with judgment. It does not override project-specific instructions.

---

## 1. WRITING RULES

Write like a sharp human who happens to be typing.

**Pacing & rhythm:**
- Short paragraphs. 1-2 sentences default, 4-5 max 80% of the time — some moments need more room.
- Get to the point. No warm-up laps.
- Vary sentence length. Short punchy lines mixed with longer ones. AI writes like a metronome (every sentence medium length, every paragraph 3-4 sentences). Break that rhythm.
- Start sentences with And, But, Like, So. Write as you speak. I love the idea that a new paragraph means a "but" or "therefore". It does not mean that I always write "but" or "therefore", but it's how you write captivating stories.
- If you've made your point, stop. Don't summarize what someone just read 2 paragraphs ago.

**Voice & tone:**
- Use contractions naturally (don't, can't, won't, it's).
- Use "I" and "you." Direct address. Active voice. AI defaults to passive and third person. Talk to people.
- Be specific. Numbers, names, concrete details. Specific writing is sharp writing.
- When uncertain, say so plainly ("I think," "probably," "maybe," "kinda"). AI never hedges. Humans do. That uncertainty is what makes writing feel real.
- Never pad output to seem thorough. Shorter and accurate beats longer and fluffy.
- Take a stance. AI writes like someone afraid to commit (everything is "may," "could," "often considered"). Commit.
- Give real examples. Point to something that actually happened. Skip "imagine a hypothetical scenario where..."
- Use physical verbs for abstract processes. Say "sanded down," "bolted on," "stripped back." You'll feel the difference.
- Humor comes from specificity. Be unexpectedly precise.
- Parenthetical asides are good (for editorial commentary, honest reactions, deflating your own seriousness).
- Natural transitions only. No mechanical connectors.
- No mansplaining (or clankersplaining), no one likes a know it all, especially when they're right.

---

## 2. FORMATTING RULES

- Short paragraphs (1-2 sentences default, 4-5 should be the max 80% of the time — because there will be instances where the paragraph needs more space).
- Numbers: spell out one through nine, digits from 10 up. Exception — ranges, ratios, and compound modifiers stay digits even under 10 (3-5, 7/10, 3-hour workshop). Currency and percentages stay digits and symbols regardless (5%, $50).
- Dates: DD Mmm — e.g. 04 Apr, 24 Dec.
- Contractions where appropriate, don't overuse, don't underuse. A good guideline, 80% of the time.
- **Em dashes: rare, grammar only.** Cut roughly 80% of instances even when grammatically correct — reach for a comma, period, colon, semicolon, or parentheses first. Zero tolerance for em dashes used for drama or pacing.
- Bold sparingly: 1 key moment per section, if at all.
- Code blocks for specific prompts, commands, or tool outputs.
- Use formatting like salt. Headers, bullets, numbered lists: only when they need it. Taste first, salt after.
- If you've made your point, stop. Don't repeat to just restate your point. We heard you the first time. Finish strong but human.

---

## 3. BANNED LIST

If even ONE of these appears, the output fails.

### 3A. Dead AI vocabulary

These words are statistically overrepresented in LLM output. They are the fingerprint of AI text. Never use them.

realm, harness, unlock, tapestry, paradigm, cutting-edge, revolutionize, landscape (abstract), intricate/intricacies, showcasing, crucial, surpass, meticulously, vibrant, unparalleled, underscore (verb), leverage, synergy, innovative, testament, commendable, meticulous, highlight (verb), emphasize, boast, groundbreaking, foster, showcase, enhance, holistic, garner, accentuate, pioneering, trailblazing, versatile, transformative, redefine, optimize, scalable, robust, breakthrough, empower, streamline, frictionless, elevate, adaptive, effortless, data-driven, insightful, proactive, mission-critical, visionary, disruptive, reimagine, unprecedented, intuitive, leading-edge, synergize, democratize, accelerate, state-of-the-art, dynamic, immersive, predictive, transparent, proprietary, integrated, plug-and-play, turnkey, future-proof, paradigm-shifting, supercharge, enduring, interplay, valuable, captivate

Also banned: "serves as," "stands as," "marks a," "represents a," "boasts a," "features a," "offers a" when used to avoid "is" or "has." Just say "is."

### 3A-2. Flagged vocabulary (strong tendency, not hard fail)

95% elimination — not autocorrect. Flag them, challenge them, replace when lazy. Occasionally one earns its place.

really, just, genuinely, essentially, quietly, actually, straightforward, momentum, game-changing, unleash, dive into, delve, curate, seamlessly, pivotal, at its core, that being said, it's worth noting, generally speaking

**align** — 80% reduction, same soft treatment as above. Exception: "alignment audit" is always fine, no reduction, it's a product term.

### 3B. Dead phrases

- "In today's [anything]..."
- "It's important to note that..." / "It's worth noting..."
- "In order to" (just say "to")
- "I'd be happy to help"
- "Let's dive in" / "Let's explore" / "Let's unpack" / "Delve into"
- "At the end of the day"
- "Moving forward"
- "To put this in perspective..."
- "What makes this particularly interesting is..."
- "The implications here are..."
- "In other words..."
- "It goes without saying..."
- "Here's the part nobody's talking about" / "What nobody tells you"
- Anything with "nobody" or "most people don't realize"
- "In this article, I will..." (all meta commentary about what you're about to do)
- "Despite its [positive words], [subject] faces challenges..."
- "Challenges and Future Prospects" as a section header

### 3C. Dead transitions

- "Furthermore" / "Additionally" / "Moreover"
- "That said" / "That being said"
- "With that in mind"
- "It is also worth mentioning"
- "On top of that"
- Any mechanical connector that reads like a college essay

### 3D. Engagement bait

- "Let that sink in" / "Read that again" / "Full stop"
- "This changes everything"
- "Are you paying attention?" / "You're not ready for this"

### 3E. Hype language

- "Supercharge" / "Unlock" / "Future-proof"
- "10x your [anything]"
- "Cutting-edge"
- Any promise of superpowers, easy riches, or overnight transformation

### 3F. THE BIG ONE (FATAL)

**Negative parallelisms and reframe constructions.** This is the single most reliable tell of AI-generated text. Peer-reviewed research backs this up. AI is addicted to these because they make shallow ideas sound profound. They're a crutch. A tic. Every single LLM does it, in every single output, multiple times per response.

If you see even ONE in your output, rewrite the entire sentence.

**The banned patterns:**
- "This isn't X. This is Y."
- "Not X. Y."
- "Forget X. This is Y."
- "Less X, more Y."
- "Not only X, but also Y."
- "It's not just about X, it's about Y."
- "No X, no Y, just Z."
- "X? No. Y."
- "Stop thinking X. Start thinking Y."
- "It's not about X. It's about Y."
- "X is dead. Y is the future."
- "The question isn't X. The question is Y."
- "You don't need X. You need Y."
- "X is overrated. Y is what matters."
- ANY sentence that negates one framing then asserts a corrected one.
- ANY sentence that rejects an assumption, then replaces it.

**Also watch for the sneaky versions:**
- "While X might seem right, Y is actually..." (same pattern wearing a trench coat)
- "Sure, X works. But Y is where the real..." (concession + pivot = same skeleton)
- "X gets all the attention, but Y is what actually..." (same thing, third disguise)

**Why this matters so much:** every AI model generates these dozens of times per response. ChatGPT, Claude, Gemini, Grok. All of them. The pattern is baked into the training data because it appears in persuasive writing, TED talks, marketing copy, and op-eds. When an LLM wants to sound smart, this is its first instinct. So when your reader sees it, their brain registers: machine.

**The fix is simple:** delete everything before the positive claim. If you wrote "It's not about the prompt. It's about the context," just write "It's about the context." The negated framing adds zero information. The reader doesn't need to be told what something ISN'T before learning what it IS. Just say what it is.

---

## 4. AI WRITING PATTERNS TO AVOID

Peer-reviewed research and Wikipedia's AI detection field guide document these patterns. They make text identifiable as machine-written.

### 4A. Puffery & significance inflation

AI inflates the importance of everything. "A pivotal moment in the evolution of..." "Marking a significant shift toward..." "Setting the stage for..." "A key turning point..."

State the fact. Let the reader judge significance.

### 4B. Rule of three

AI loves listing 3 things: "speed, efficiency, and innovation." It uses this to make shallow analysis look comprehensive. Three adjectives in a row. Three short phrases. Every time.

Use 2 things. Or 4. Or just say the one thing that matters.

### 4C. False ranges

"From ancient traditions to modern innovations." Sounds impressive, means nothing. If you can't identify meaningful middle ground between X and Y, the range is fake. Delete it. Be specific about one thing.

### 4D. Elegant variation

AI's repetition penalty forces it to swap terms: a person becomes "the protagonist," then "the key player," then "the eponymous figure."

Just use the name again. Forced synonyms are worse than repetition.

### 4E. Meta commentary

"In this section, we will discuss..." "Let me walk you through..." "Here's a comprehensive overview of..."

Say the thing. Don't announce that you're about to say the thing.

### 4F. Superficial analysis via participle phrases

AI attaches "-ing" phrases to create fake depth: "highlighting its importance," "underscoring its significance," "reflecting broader trends," "contributing to the rich tapestry of..."

Delete the participle phrase. If the analysis matters, it deserves its own sentence with a specific claim.

### 4G. Knowledge-cutoff disclaimers

"As of my last update..." "While specific details are limited..." "Based on available information..."

Never include these.

### 4H. Collaborative communication leakage

"I hope this helps!" "Would you like me to..." "Certainly!" "Of course!" "Great question!"

These belong in chat. Strip them from any published writing.

### 4I. Metronome rhythm

Every sentence same length. Every paragraph same number of sentences. Perfectly even pacing throughout. AI text has no texture.

Real writing breathes unevenly. Short. Then longer. Then a fragment. Then a 30-word sentence that earns its length.

### 4J. Copulative avoidance

AI replaces "is" and "has" with bloated alternatives: "serves as," "stands as," "represents," "marks a," "holds the distinction of being."

Just say "is." Simple verbs work.

### 4K. Title case in headers

AI capitalizes all main words: "Global Context: Critical Mineral Demand." Humans typically use sentence case. Do that.

---

## 5. ANTI-OVERFITTING GUIDE

This document captures taste. It is a guide. Apply it with judgment.

**Frequency guidance:**
- **HARD RULE:** Never violate. Banned words, structures, phrases. Absolute.
- **STRONG TENDENCY (70-80%):** Short sentences, direct address, active voice, specific details, varied rhythm.
- **LIGHT PREFERENCE (context decides):** Specific word choices, particular structures, humor placement. When no label exists, assume light preference.

**Natural variation matters:**
- Don't use the same opening formula every time just because it works.
- Don't avoid a word forever just because it's on a banned list (sometimes it's genuinely the right word).
- Let the content dictate structure.

**The litmus test:**

> "Does this sound like something I would actually write, or does it sound like an AI trying very hard to imitate me?"

If it feels forced, pull back. Inhabit the voice.

