# System design and coverage

## The split of responsibility

`VOICE-DNA.md` remains the human source of truth. It carries taste, exceptions, and the final ear test.

`llm_copy_refiner.py` handles broad editorial signals: formulaic constructions, signposting, vague importance claims, assistant residue, abstract language, structural repetition, protected spans, conservative edits, and deterministic reports.

`voice_dna_refiner.py` is the policy layer. It translates the source guide into hard, strong, and light checks; adds local exceptions; merges duplicate findings; decides whether a check passes; and keeps the manual decisions visible.

## Coverage map

| VOICE DNA area | System treatment | Default policy |
|---|---|---|
| Banned vocabulary | Exact terms plus common grammatical variants | Hard |
| Banned phrases and transitions | Literal and regular-expression checks | Hard |
| Engagement bait and hype | Literal and pattern checks | Hard |
| Negative reframes | VOICE DNA patterns plus the base refiner’s contrast rules | Hard |
| Em dashes | Procedural check; conservative automatic removal where safe | Hard |
| Assistant and knowledge-cutoff residue | VOICE DNA and base checks | Hard or strong |
| Short paragraphs | Paragraph sentence-count check | Strong |
| Numbers as digits | Low-confidence number-word check | Strong |
| Contractions | Low-confidence uncontracted-form check | Strong |
| Sentence case in headings | Markdown heading analyzer | Strong |
| Bold sparingly | Per-section Markdown bold count | Strong |
| Rule of 3 | Sentence triads plus broader structural analysis | Strong |
| Puffery and significance inflation | Phrase patterns plus broad base rules | Strong |
| False ranges | Context-sensitive pattern check | Strong |
| Metronome rhythm | Sentence and paragraph variation analysis | Strong |
| Repeated openings and tidy bullets | Broad structural analysis | Strong |
| Meta commentary | Literal and pattern checks | Hard or strong |
| Participle-based fake depth | Phrase and density checks | Strong |
| Copulative avoidance | Phrase checks | Hard |
| Repeated conclusion | Introduction/conclusion overlap analyzer | Strong |
| Specificity | Names, numbers, dates, and quotation snapshot | Strong, high risk |
| Direct address | Long-passage pronoun snapshot | Light, high risk |
| Elegant variation | Manual pass | Judgment |
| Real examples and concrete mechanisms | Partial signal plus manual pass | Judgment |
| Physical verbs | Manual pass | Judgment |
| Humor and parenthetical asides | Texture count plus manual pass | Judgment |
| Stop when the point is made | Repetition signals plus manual pass | Judgment |

## Why some checks stay manual

A deterministic linter can count and locate patterns. It cannot reliably decide whether a contrast is logically necessary, whether `transparent` is a technical requirement, whether 3 items are genuinely 3 parts, or whether a joke sounds like the writer.

The system marks those boundaries instead of burying them under a fake score.

## Exception model

Exceptions are local and inspectable:

- allow one term
- allow a narrow regular-expression context
- disable one rule for a project
- change one rule’s policy level
- include or exclude quoted text

This keeps the source guide firm while letting the content win when it should.

## Automatic editing boundary

Automatic mode is intentionally conservative. It can handle mechanical cleanup, redundant prefixes, `in order to`, and many em-dash replacements. It does not rewrite banned vocabulary, negative reframes, or voice-level decisions.

Every file write is atomic. The system also refuses to write when a second automatic pass would change the text again.
