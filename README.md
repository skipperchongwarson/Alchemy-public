# Alchemy-public

Writing voice, editorial rules, and a linting toolkit for How This Works co (HTWco), built and maintained by Skipper Chong Warson.

This is the public half of a private/public split. It holds the style guide, hook library, and reference samples that define Skipper's writing voice for LinkedIn posts, comments, and video scripts — plus the tooling used to check drafts against that voice programmatically.

## What's here

```text
Alchemy-public/
├── make/
│   ├── voice-and-writing.md
│   ├── voice-samples.md
│   ├── hook-library.md
│   └── project-brief.md
├── reference/
│   ├── resume.md
│   └── linkedin-profile.md
├── tools/
│   ├── VOICE-DNA.md
│   ├── voice_dna_refiner.py
│   ├── llm_copy_refiner.py
│   ├── PROMPT-CONTRACT.md
│   ├── PROMPT-RUBRIC.md
│   └── examples/
└── LICENSE
```

**`make/voice-and-writing.md`** is the governing style guide: numbers, punctuation, banned words, voice defaults, and the three rules that cover most content problems. **`voice-samples.md`** shows what finished work in that voice actually sounds like. **`hook-library.md`** is a reference set of opening patterns, used only when nothing better presents itself.

**`tools/`** is a two-part linting package: `llm_copy_refiner.py` catches broad AI-tell patterns (banned vocabulary, negative-parallelism constructions, metronome rhythm), and `voice_dna_refiner.py` layers the author-specific rules from `VOICE-DNA.md` on top, producing one combined report. `PROMPT-CONTRACT.md` and `PROMPT-RUBRIC.md` are templates for constructing and reviewing bounded prompts for repeatable writing tasks.

## Who this is for

Anyone who wants to see how a specific, opinionated writing voice gets defined well enough that both a human and an LLM can consistently reproduce it — or who wants to reuse the linting approach for a different voice.

## License

MIT — see [LICENSE](LICENSE).
