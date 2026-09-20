# VOICE DNA report

**Source:** `examples/before.md`  
**Status:** FAIL  
**Policy:** fail on hard  
**Findings:** 14 (11 failures)

## Findings

### STRONG: `VD_TITLE_CASE_HEADING` at L1:C3

Matched: `A Revolutionary Framework for Better Writing`

Markdown heading appears to use title case.

**Edit:** Use sentence case unless a proper noun requires capitals.

### HARD: `VD_TODAYS` at L3:C1

Matched: `In today's dynamic landscape`

Generic 'In today's...' opening.

**Edit:** Open with the actual subject.

### HARD: `VD_WORD_069_dynamic` at L3:C12

Matched: `dynamic`

VOICE DNA banned vocabulary: 'dynamic'.

**Edit:** Use the plainest specific word that preserves the intended meaning, or explicitly allow this term for the document.

### HARD: `VD_WORD_009_landscape` at L3:C20

Matched: `landscape`

VOICE DNA banned vocabulary: 'landscape'.

**Edit:** Use the plainest specific word that preserves the intended meaning, or explicitly allow this term for the document.

### HARD: `VD_WORD_047_robust` at L3:C36

Matched: `robust`

VOICE DNA banned vocabulary: 'robust'.

**Edit:** Use the plainest specific word that preserves the intended meaning, or explicitly allow this term for the document.

### HARD: `VD_NEG_DOESNT_JUST_PERIOD` at L3:C50

Matched: `doesn't just streamline your workflow. It unlocks a seamless, data-driven future`

Negative reframe or replacement construction.

**Edit:** Delete the rejected framing and keep the positive claim. Preserve a real contrast only when the evidence needs it.

### HARD: `VD_WORD_050_streamline` at L3:C63

Matched: `streamline`

VOICE DNA banned vocabulary: 'streamline'.

**Edit:** Use the plainest specific word that preserves the intended meaning, or explicitly allow this term for the document.

### HARD: `VD_WORD_VARIANT_003_unlock` at L3:C92

Matched: `unlocks`

Inflected form of VOICE DNA banned vocabulary: 'unlock'.

**Edit:** Use a plain, specific verb, or explicitly allow this term for the document.

### HARD: `VD_WORD_044_seamless` at L3:C102

Matched: `seamless`

VOICE DNA banned vocabulary: 'seamless'.

**Edit:** Use the plainest specific word that preserves the intended meaning, or explicitly allow this term for the document.

### HARD: `VD_WORD_055_data_driven` at L3:C112

Matched: `data-driven`

VOICE DNA banned vocabulary: 'data-driven'.

**Edit:** Use the plainest specific word that preserves the intended meaning, or explicitly allow this term for the document.

### HARD: `VD_NEG_NOT_PERIOD` at L5:C1

Matched: `It's not about sounding polished. It's about sounding human`

Negative reframe or replacement construction.

**Edit:** Delete the rejected framing and keep the positive claim. Preserve a real contrast only when the evidence needs it.

### HARD: `VD_IN_ORDER_TO` at L7:C1

Matched: `In order to`

Wordy 'in order to' construction.

**Edit:** Use to.

### STRONG: `VD_NUMBER_WORD` at L7:C36

Matched: `three`

Small number is written as a word where a digit may be clearer.

**Edit:** Consider 3 if this is a count rather than an idiom.

### STRONG: `VD_RULE_OF_THREE` at L7:C50

Matched: `speed, clarity, and innovation`

Sentence uses a polished 3-part list.

**Edit:** Keep the 3 items only when the content genuinely has 3 parts. Otherwise use 1, 2, or 4.

## Manual pass

- Check elegant variation: repeat the real name instead of cycling through forced synonyms.
- Check stance: remove generic may/could language where the evidence supports a direct claim.
- Check examples: use something that happened, with a name, mechanism, date, number, or constraint.
- Check verbs: replace abstract process language with a concrete action where it fits.
- Check humor and parenthetical asides by ear. The linter cannot manufacture personality.
- Read the piece aloud. Pull back anywhere the voice sounds like an AI performing humanness.

_Editorial signals and house-style checks, not authorship evidence._
