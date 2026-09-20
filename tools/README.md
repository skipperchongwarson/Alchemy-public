# VOICE DNA refiner

This package joins two different jobs:

1. `llm_copy_refiner.py` audits broad, repeatable writing patterns. It protects code, links, citations, quotes, and other literal spans; supports audit, suggestion, and conservative automatic modes; and produces human, JSON, and diff output.
2. `voice_dna_refiner.py` adds the author-specific rules in `VOICE-DNA.md`, assigns policy levels, handles deliberate exceptions, and produces one report.

The tool checks writing. It does not guess who wrote it.

Project-specific governing files take priority. `VOICE-DNA.md` provides reusable editorial-review support where it does not conflict with those files.

## What the merged system adds

### Policy levels

**Hard** means the draft fails by default. This includes banned vocabulary, banned phrases, assistant residue, and negative reframe constructions.

**Strong** means review the pattern across the piece. This includes em dashes: keep only rare grammatical uses and remove uses added for drama or pacing. Other examples include title-case headings, long paragraphs, 3-part lists, false ranges, puffery, and weak specificity.

**Light** means use judgment. These checks should never become quotas.

The default check policy is `hard`. Use `--fail-on strong` when a piece needs a stricter pass, or `--fail-on any` during an editorial cleanup.

### Exceptions without rule erosion

Some words are the right words. Add deliberate exceptions through:

- `allowed_terms` for one exact term
- `allowed_patterns` for a local context
- `disabled_rules` for a rule that does not fit a project
- `severity_overrides` to change one rule from hard to strong or light
- `--allow-term` and `--allow-pattern` for a single run

Quoted prose is skipped by default. Use `--check-quotes` when the quoted text is also yours.

### One combined report

Each finding includes:

- source layer
- rule ID
- hard, strong, or light policy
- line and column
- matched text
- reason
- suggested edit
- evidence class, confidence, and false-positive risk

The report also shows sentence-length spread, contractions, direct address, numbers, and parenthetical asides. Those are texture readings, not a grade.

## Files

- `voice_dna_refiner.py`: main command and public Python API
- `llm_copy_refiner.py`: broad deterministic refiner
- `VOICE-DNA.md`: reusable author-specific editorial guidance, subordinate to project-specific governing files
- `voice-dna-config.json`: editable project policy
- `VOICE-REFINER-PROMPT.md`: applies the VOICE DNA editorial system to a completed draft, subordinate to project-specific writing rules
- `PROMPT-CONTRACT.md`: helps construct bounded prompts for complex or repeatable work
- `PROMPT-RUBRIC.md`: helps review whether prompts are sufficiently clear, grounded, and testable
- `examples/`: sample copy, configuration, and a GitHub Actions loop

Both Python files use the standard library only.

## Start here

```bash
cd voice_dna_system
python3 voice_dna_refiner.py --self-test
python3 voice_dna_refiner.py examples/before.md
```

A hard-rule check for CI or a pre-publish gate:

```bash
python3 voice_dna_refiner.py article.md \
  --config voice-dna-config.json \
  --check
```

The exit status is `1` when the active failure policy is breached.

A stricter editorial pass:

```bash
python3 voice_dna_refiner.py article.md \
  --config voice-dna-config.json \
  --check \
  --fail-on strong
```

Generate inspectable reports:

```bash
python3 voice_dna_refiner.py article.md \
  --report-json article.voice-dna.json \
  --report-markdown article.voice-dna.md
```

Run conservative automatic edits and inspect the diff:

```bash
python3 voice_dna_refiner.py article.md \
  --mode auto \
  --output article.refined.md \
  --diff
```

Automatic mode handles edits with low semantic risk, such as removing redundant prefixes, replacing `in order to`, cleaning mechanical spacing, and eliminating many em-dash constructions. Negative reframes, word choice, and voice decisions stay visible for a human edit.

Read from standard input:

```bash
pbpaste | python3 voice_dna_refiner.py - --check
```

List every rule:

```bash
python3 voice_dna_refiner.py --list-rules
```

Print the reusable drafting prompt:

```bash
python3 voice_dna_refiner.py --emit-prompt
```

## Suggested writing loop

Give the model the applicable project-specific writing instructions, `VOICE-DNA.md`, and `VOICE-REFINER-PROMPT.md` before drafting.

Then run the draft through the refiner:

```bash
python3 voice_dna_refiner.py draft.md \
  --config voice-dna-config.json \
  --report-markdown draft.report.md
```

Fix hard findings first. Read strong findings as a group. Use the manual pass for the parts software cannot judge well: stance, forced synonyms, real examples, concrete verbs, humor, and whether the voice sounds inhabited.

Run the check again. A clean hard pass means the known failures are gone. It does not mean the writing is finished.

## Config reference

```json
{
  "format": "auto",
  "base_profile": "markdown",
  "fail_on": "hard",
  "max_auto_risk": "low",
  "check_quotes": false,
  "allowed_terms": ["transparent"],
  "allowed_patterns": ["physical landscape"],
  "disabled_rules": [],
  "severity_overrides": {
    "VD_NUMBER_WORD": "light"
  }
}
```

`allowed_patterns` are regular expressions checked in a small window around the match. Keep them narrow.

All em-dash rule IDs are overridden to `strong` in the repository configuration. They remain visible for editorial review but do not fail the default hard-rule check.

## Python API

```python
from voice_dna_refiner import VoiceConfig, analyze_text, refine_text

text = "In order to improve it, we ran five interviews."

result = analyze_text(text, VoiceConfig(fail_on="hard"))
print(result.metrics["status"])

refined = refine_text(text, VoiceConfig(mode="auto"))
print(refined.refined_text)
```

## Safety rails

Automatic output is written atomically. The tool checks that a second automatic pass makes no further changes before it overwrites or writes a file.

Code, URLs, email addresses, citations, Markdown links, and several other literal spans are protected. Quotes are reported only when requested.

A finding is an editorial signal. Common words, technical terms, genuine contrasts, 3-part lists, and title case can all be correct. The config exists so exceptions stay visible and local.

## Test coverage

Run both suites:

```bash
python3 llm_copy_refiner.py --self-test
python3 voice_dna_refiner.py --self-test
```

The merged layer tests banned terms, inflected variants, negative reframes, quote protection, exceptions, em dashes, title case, 3-part lists, failure thresholds, safe edits, config loading, and base-refiner integration.
