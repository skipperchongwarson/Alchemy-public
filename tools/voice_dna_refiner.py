#!/usr/bin/env python3
"""VOICE DNA + deterministic copy-refiner orchestration layer.

This tool combines a broad editorial linter with an author-specific policy layer.
It reports writing signals and house-style violations. It does not classify
human or machine authorship.

Standard-library only. Keep this file beside ``llm_copy_refiner.py``.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import statistics
import sys
import tempfile
import unittest
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from difflib import unified_diff
from pathlib import Path
from typing import Iterable, Sequence

TOOL_NAME = "voice_dna_refiner"
VERSION = "1.0.0"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_SELF_TEST = 4

SEVERITY_RANK = {"light": 0, "strong": 1, "hard": 2}
RISK_RANK = {"low": 0, "medium": 1, "high": 2}
MODES = {"audit", "suggest", "auto"}
FORMATS = {"auto", "plain", "markdown", "html", "latex"}
FAIL_LEVELS = {"hard", "strong", "any", "none"}

WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
NUMBER_RE = re.compile(r"(?<!\w)[+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?%?(?!\w)")
HEADING_RE = re.compile(r"(?m)^(?P<prefix>\s{0,3}#{1,6}\s+)(?P<title>[^#\r\n].*?)(?:\s+#+)?$")
BOLD_RE = re.compile(r"(?<!\*)\*\*(?!\s)(.+?)(?<!\s)\*\*(?!\*)")


# ---------------------------------------------------------------------------
# Load the broad refiner from the sibling file without requiring installation.
# ---------------------------------------------------------------------------


def _load_base_module():
    path = Path(__file__).with_name("llm_copy_refiner.py")
    if not path.exists():
        raise RuntimeError(f"Missing sibling dependency: {path}")
    spec = importlib.util.spec_from_file_location("voice_dna_base_refiner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load base refiner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = _load_base_module()


# ---------------------------------------------------------------------------
# Policy data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceRule:
    rule_id: str
    category: str
    description: str
    severity: str
    evidence_class: str
    false_positive_risk: str
    pattern: str | None = None
    suggestion: str = "Review and rewrite directly."
    enabled: bool = True
    flags: int = re.IGNORECASE
    safe_replacement: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class UnifiedFinding:
    source: str
    rule_id: str
    category: str
    severity: str
    start: int
    end: int
    line: int
    column: int
    matched_text: str
    explanation: str
    suggestion: str
    evidence_class: str
    false_positive_risk: str
    confidence: str
    protected: bool = False
    protected_kind: str | None = None


@dataclass(frozen=True)
class AppliedEdit:
    source: str
    rule_id: str
    original: str
    replacement: str
    rationale: str


@dataclass(frozen=True)
class VoiceConfig:
    mode: str = "audit"
    format: str = "auto"
    base_profile: str = "markdown"
    fail_on: str = "hard"
    max_auto_risk: str = "low"
    check_quotes: bool = False
    allowed_terms: tuple[str, ...] = ()
    allowed_patterns: tuple[str, ...] = ()
    disabled_rules: tuple[str, ...] = ()
    severity_overrides: tuple[tuple[str, str], ...] = ()
    source_name: str | None = None

    def severity_map(self) -> dict[str, str]:
        return dict(self.severity_overrides)


@dataclass
class SystemResult:
    original_text: str
    refined_text: str
    findings: list[UnifiedFinding]
    initial_findings: list[UnifiedFinding]
    edits: list[AppliedEdit]
    warnings: list[str]
    metrics: dict[str, object]
    manual_review: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original_text != self.refined_text

    def to_payload(self, source: str | None = None) -> dict[str, object]:
        return {
            "tool": TOOL_NAME,
            "version": VERSION,
            "source": source,
            "metrics": self.metrics,
            "findings": [asdict(item) for item in self.findings],
            "initial_findings": [asdict(item) for item in self.initial_findings],
            "edits": [asdict(item) for item in self.edits],
            "warnings": list(self.warnings),
            "manual_review": list(self.manual_review),
            "changed": self.changed,
        }


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


def _literal_pattern(phrase: str) -> str:
    escaped = re.escape(phrase.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace("\\'", "(?:'|’)")
    prefix = r"(?<!\w)" if phrase and (phrase[0].isalnum() or phrase[0] in "'’") else ""
    suffix = r"(?!\w)" if phrase and (phrase[-1].isalnum() or phrase[-1] in "'’") else ""
    return prefix + escaped + suffix


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:52] or "rule"


def _build_voice_rules() -> tuple[VoiceRule, ...]:
    rules: list[VoiceRule] = []
    used: set[str] = set()

    def add(rule: VoiceRule) -> None:
        if rule.severity not in SEVERITY_RANK:
            raise RuntimeError(f"Bad severity for {rule.rule_id}: {rule.severity}")
        if rule.false_positive_risk not in RISK_RANK:
            raise RuntimeError(f"Bad risk for {rule.rule_id}: {rule.false_positive_risk}")
        if rule.rule_id in used:
            raise RuntimeError(f"Duplicate rule ID: {rule.rule_id}")
        used.add(rule.rule_id)
        rules.append(rule)

    def literals(
        prefix: str,
        category: str,
        entries: Sequence[str],
        *,
        severity: str,
        description: str,
        suggestion: str,
        risk: str = "low",
        evidence: str = "house-style",
    ) -> None:
        for index, phrase in enumerate(entries, 1):
            add(
                VoiceRule(
                    rule_id=f"{prefix}_{index:03d}_{_slug(phrase)}",
                    category=category,
                    description=f"{description}: {phrase!r}.",
                    severity=severity,
                    evidence_class=evidence,
                    false_positive_risk=risk,
                    pattern=_literal_pattern(phrase),
                    suggestion=suggestion,
                )
            )

    banned_words = [
        "delve", "realm", "harness", "unlock", "tapestry", "paradigm", "cutting-edge",
        "revolutionize", "landscape", "intricate", "intricacies", "showcasing", "crucial",
        "pivotal", "surpass", "meticulously", "vibrant", "unparalleled", "underscore",
        "leverage", "synergy", "innovative", "game-changer", "testament", "commendable",
        "meticulous", "highlight", "emphasize", "boast", "groundbreaking", "align", "foster",
        "showcase", "enhance", "holistic", "garner", "accentuate", "pioneering", "trailblazing",
        "unleash", "versatile", "transformative", "redefine", "seamless", "optimize", "scalable",
        "robust", "breakthrough", "empower", "streamline", "frictionless", "elevate", "adaptive",
        "effortless", "data-driven", "insightful", "proactive", "mission-critical", "visionary",
        "disruptive", "reimagine", "unprecedented", "intuitive", "leading-edge", "synergize",
        "democratize", "accelerate", "state-of-the-art", "dynamic", "immersive", "predictive",
        "transparent", "proprietary", "integrated", "plug-and-play", "turnkey", "future-proof",
        "paradigm-shifting", "supercharge", "enduring", "interplay", "valuable", "captivate",
    ]
    literals(
        "VD_WORD", "dead_ai_vocabulary", banned_words,
        severity="hard",
        description="VOICE DNA banned vocabulary",
        suggestion="Use the plainest specific word that preserves the intended meaning, or explicitly allow this term for the document.",
        risk="medium",
    )

    # The guide names lemmas. Catch common grammatical variants without
    # pretending that every possible derivation belongs to the same rule.
    inflected_roots = [
        "delve", "harness", "unlock", "revolutionize", "surpass", "underscore", "leverage",
        "emphasize", "boast", "align", "foster", "showcase", "enhance", "garner", "accentuate",
        "unleash", "redefine", "optimize", "empower", "streamline", "elevate", "reimagine",
        "synergize", "democratize", "accelerate", "supercharge", "captivate",
    ]
    for index, root in enumerate(inflected_roots, 1):
        if root.endswith("e"):
            variant = re.escape(root) + r"(?:s|d|ing)"
        else:
            variant = re.escape(root) + r"(?:s|es|ed|ing)"
        add(VoiceRule(
            f"VD_WORD_VARIANT_{index:03d}_{_slug(root)}", "dead_ai_vocabulary",
            f"Inflected form of VOICE DNA banned vocabulary: {root!r}.", "hard",
            "house-style", "medium", rf"(?<!\w){variant}(?!\w)",
            "Use a plain, specific verb, or explicitly allow this term for the document."
        ))

    copulative = [
        "serves as", "served as", "stands as", "stood as", "represents a", "represents an",
        "represented a", "represented an", "boasts a", "boasts an", "features a", "features an",
        "offers a", "offers an", "holds the distinction of being",
    ]
    literals(
        "VD_COPULA", "copulative_avoidance", copulative,
        severity="hard",
        description="Bloated substitute for is or has",
        suggestion="Use is, has, or a concrete verb.",
    )
    add(VoiceRule(
        "VD_COPULA_MARKS_A", "copulative_avoidance", "Bloated marks-a construction.", "hard",
        "house-style", "medium", r"\bmarks?\s+(?:a|an)\b", "Use is, starts, records, or the concrete action."
    ))

    hard_phrases = [
        "I'd be happy to help", "Straightforward", "At the end of the day", "Moving forward",
        "To put this in perspective", "What makes this particularly interesting is",
        "The implications here are", "In other words", "It goes without saying",
        "Here's the part nobody's talking about", "What nobody tells you", "Most people don't realize",
        "Furthermore", "Additionally", "Moreover", "That said", "That being said",
        "With that in mind", "It is also worth mentioning", "On top of that",
        "Let that sink in", "Read that again", "Full stop", "This changes everything",
        "Are you paying attention", "You're not ready for this", "Great question", "Of course",
        "I hope this helps", "Would you like me to", "As of my last update",
        "While specific details are limited", "Based on available information",
        "Challenges and Future Prospects",
    ]
    literals(
        "VD_PHRASE", "dead_phrase", hard_phrases,
        severity="hard",
        description="VOICE DNA banned phrase",
        suggestion="Delete the staging language and state the claim.",
    )

    regex_rules = [
        ("VD_TODAYS", "dead_phrase", r"\bin\s+today(?:'|’)s\s+[^,.!?\n]{1,80}", "Open with the actual subject.", "low"),
        ("VD_NOTE_PREFIX", "dead_phrase", r"\bit(?:'|’)s\s+(?:important|worth)\s+to\s+note\s+that\b", "Delete the prefix and capitalize the claim.", "low"),
        ("VD_NOTE_PREFIX_FORMAL", "dead_phrase", r"\bit\s+is\s+(?:important|worth)\s+to\s+note\s+that\b", "Delete the prefix and capitalize the claim.", "low"),
        ("VD_IN_ORDER_TO", "dead_phrase", r"\bin\s+order\s+to\b", "Use to.", "low"),
        ("VD_LETS_META", "dead_phrase", r"\blet(?:'|’)s\s+(?:dive\s+in|explore|unpack|delve\s+into)\b", "Start with the subject.", "low"),
        ("VD_NOBODY", "engagement_bait", r"\bnobody\b", "Name the actual group or delete the claim.", "medium"),
        ("VD_IN_THIS_ARTICLE", "meta_commentary", r"\bin\s+this\s+(?:article|section|post|essay|report)\s*,?\s+(?:i|we)\s+(?:will|am\s+going\s+to|are\s+going\s+to)\b", "Say the thing instead of announcing it.", "low"),
        ("VD_DESPITE_FACES", "dead_phrase", r"\bdespite\s+(?:its|their|the)\s+[^,.!?\n]{1,80},\s+[^,.!?\n]{1,80}\s+faces?\s+challenges\b", "State the concrete constraint directly.", "medium"),
        ("VD_10X", "hype", r"\b10x\s+(?:your|the|our|their)\b", "Name a measured change and its evidence.", "low"),
        ("VD_SUPERPOWER_PROMISE", "hype", r"\b(?:overnight\s+(?:success|transformation|results)|easy\s+riches|superpowers?)\b", "Remove the impossible promise.", "low"),
        ("VD_META_OVERVIEW", "meta_commentary", r"\b(?:in\s+this\s+section,?\s+we\s+will\s+discuss|let\s+me\s+walk\s+you\s+through|here(?:'|’)s\s+a\s+comprehensive\s+overview\s+of)\b", "Start with the content.", "low"),
        ("VD_CUTOFF_DISCLAIMER", "assistant_residue", r"\b(?:as\s+of\s+my\s+(?:last\s+)?(?:update|knowledge\s+cutoff)|my\s+knowledge\s+only\s+goes\s+up\s+to)\b", "Use current, sourced information or state the exact uncertainty.", "low"),
    ]
    regex_descriptions = {
        "VD_TODAYS": "Generic 'In today's...' opening.",
        "VD_NOTE_PREFIX": "Importance announcement instead of a direct claim.",
        "VD_NOTE_PREFIX_FORMAL": "Importance announcement instead of a direct claim.",
        "VD_IN_ORDER_TO": "Wordy 'in order to' construction.",
        "VD_LETS_META": "Meta invitation before the content starts.",
        "VD_NOBODY": "Unsupported nobody claim or engagement bait.",
        "VD_IN_THIS_ARTICLE": "Draft announces what it will say.",
        "VD_DESPITE_FACES": "Stock concession-plus-challenges sentence.",
        "VD_10X": "Unmeasured 10x promise.",
        "VD_SUPERPOWER_PROMISE": "Impossible or overnight promise.",
        "VD_META_OVERVIEW": "Meta commentary before the content.",
        "VD_CUTOFF_DISCLAIMER": "Knowledge-cutoff disclaimer in reusable writing.",
    }
    for rid, category, pattern, suggestion, risk in regex_rules:
        add(VoiceRule(rid, category, regex_descriptions[rid], "hard", "house-style", risk, pattern, suggestion))

    negative_patterns = [
        ("VD_NEG_NOT_PERIOD", r"\b(?:(?:this|it)\s+(?:is|was)(?:n(?:'|’)t|\s+not)|it(?:'|’)s\s+not)\s+[^.!?\n]{1,100}[.!?]\s+(?:(?:this|it)\s+(?:is|was)|it(?:'|’)s)\s+[^.!?\n]{1,120}"),
        ("VD_NEG_BARE_NOT", r"(?m)(?:^|[.!?]\s+)Not\s+[^.!?\n]{1,100}[.!?]\s+[A-Z][^.!?\n]{1,120}"),
        ("VD_NEG_FORGET", r"\bForget\s+[^.!?\n]{1,100}[.!?]\s+(?:This|It)\s+is\s+[^.!?\n]{1,120}"),
        ("VD_NEG_LESS_MORE", r"\bless\s+[^,;.!?\n]{1,90},\s*more\s+[^.!?\n]{1,120}"),
        ("VD_NEG_NOT_ONLY", r"\bnot\s+only\s+[^,;.!?\n]{1,100},?\s+but\s+also\s+[^.!?\n]{1,120}"),
        ("VD_NEG_NOT_JUST", r"\b(?:it(?:'|’)s|it\s+is|this\s+is)\s+not\s+just\s+(?:about\s+)?[^,;.!?\n]{1,100},?\s+(?:it(?:'|’)s|it\s+is|but)\s+(?:about\s+)?[^.!?\n]{1,120}"),
        ("VD_NEG_DOESNT_JUST_PERIOD", r"\b(?:doesn|don|didn)(?:'|’)t\s+just\s+[^.!?\n]{1,100}[.!?]\s+(?:it|this|they|we|you|[A-Z][A-Za-z0-9_-]+)\s+[^.!?\n]{1,120}"),
        ("VD_NEG_NO_NO_JUST", r"\bno\s+[^,;.!?\n]{1,70},\s*no\s+[^,;.!?\n]{1,70},\s*just\s+[^.!?\n]{1,100}"),
        ("VD_NEG_Q_NO", r"[^.!?\n]{1,100}\?\s*No[.!]\s+[A-Z][^.!?\n]{1,120}"),
        ("VD_NEG_STOP_START", r"\bstop\s+(?:thinking|doing|using|asking)\s+[^.!?\n]{1,100}[.!?]\s+start\s+(?:thinking|doing|using|asking)\s+[^.!?\n]{1,120}"),
        ("VD_NEG_DEAD_FUTURE", r"\b[^.!?\n]{1,80}\s+is\s+dead[.!?]\s+[^.!?\n]{1,80}\s+is\s+the\s+future\b"),
        ("VD_NEG_QUESTION", r"\bthe\s+question\s+(?:isn(?:'|’)t|is\s+not)\s+[^.!?\n]{1,100}[.!?]\s+the\s+question\s+is\s+[^.!?\n]{1,120}"),
        ("VD_NEG_DONT_NEED", r"\byou\s+don(?:'|’)t\s+need\s+[^.!?\n]{1,100}[.!?]\s+you\s+need\s+[^.!?\n]{1,120}"),
        ("VD_NEG_OVERRATED", r"\b[^.!?\n]{1,80}\s+is\s+overrated[.!?]\s+[^.!?\n]{1,80}\s+is\s+what\s+matters\b"),
        ("VD_NEG_WHILE_ACTUALLY", r"\bwhile\s+[^,;.!?\n]{1,100}\s+might\s+seem\s+[^,;.!?\n]{1,80},\s+[^.!?\n]{1,120}\s+is\s+actually\b"),
        ("VD_NEG_SURE_REAL", r"\bsure,?\s+[^.!?\n]{1,100}\s+works?[.!?]\s+but\s+[^.!?\n]{1,120}\s+is\s+where\s+the\s+real\b"),
        ("VD_NEG_ATTENTION_ACTUALLY", r"\b[^.!?\n]{1,100}\s+gets\s+all\s+the\s+attention,\s+but\s+[^.!?\n]{1,120}\s+is\s+what\s+actually\b"),
    ]
    for rid, pattern in negative_patterns:
        add(VoiceRule(
            rid, "negative_parallelism", "Negative reframe or replacement construction.", "hard",
            "house-style", "medium", pattern,
            "Delete the rejected framing and keep the positive claim. Preserve a real contrast only when the evidence needs it."
        ))

    strong_patterns = [
        ("VD_PUFFERY_PIVOTAL_MOMENT", "puffery", r"\b(?:a\s+)?pivotal\s+moment\b", "State the event and let the reader judge its importance."),
        ("VD_PUFFERY_SIGNIFICANT_SHIFT", "puffery", r"\b(?:marking\s+)?a?\s*significant\s+shift\b", "Name what changed, when, and by how much."),
        ("VD_PUFFERY_STAGE", "puffery", r"\bsetting\s+the\s+stage\s+for\b", "State the causal link."),
        ("VD_PUFFERY_TURNING", "puffery", r"\bkey\s+turning\s+point\b", "Name the decision or event."),
        ("VD_FALSE_RANGE", "false_range", r"\bfrom\s+[^,.!?\n]{2,70}\s+to\s+[^,.!?\n]{2,70}", "Name the specific endpoints and meaningful middle ground, or keep only the relevant point."),
        ("VD_PARTICIPLE_IMPORTANCE", "superficial_analysis", r"\b(?:highlighting|underscoring|reflecting|showcasing|demonstrating)\s+(?:its|their|the)\s+(?:importance|significance|value|impact)\b", "Replace the trailing -ing phrase with a specific claim or delete it."),
    ]
    for rid, category, pattern, suggestion in strong_patterns:
        add(VoiceRule(rid, category, "VOICE DNA strong-tendency pattern.", "strong", "house-style", "medium", pattern, suggestion))

    # Formatting and document-level rules use procedural analyzers below.
    procedural = [
        ("VD_EM_DASH", "punctuation", "Review this em dash. Keep it only when it serves a grammatical purpose rather than drama or pacing.", "hard", "low"),
        ("VD_LONG_PARAGRAPH", "paragraph_rhythm", "Paragraph exceeds the preferred 3-sentence maximum.", "strong", "medium"),
        ("VD_TITLE_CASE_HEADING", "formatting", "Markdown heading appears to use title case.", "strong", "medium"),
        ("VD_BOLD_DENSITY", "formatting", "Section uses more than 2 bold spans.", "strong", "medium"),
        ("VD_NUMBER_WORD", "formatting", "Small number is written as a word where a digit may be clearer.", "strong", "high"),
        ("VD_UNCONTRACTED_FORM", "voice", "Formal uncontracted form where a natural contraction may fit.", "strong", "high"),
        ("VD_LOW_SPECIFICITY", "specificity", "Long passage has few names, numbers, dates, quoted phrases, or concrete markers.", "strong", "high"),
        ("VD_NO_DIRECT_ADDRESS", "voice", "Long explanatory passage has no I or you language.", "light", "high"),
        ("VD_RULE_OF_THREE", "structural_repetition", "Sentence uses a polished 3-part list.", "strong", "high"),
    ]
    for rid, category, description, severity, risk in procedural:
        add(VoiceRule(rid, category, description, severity, "house-style", risk, None, "Review in context."))

    return tuple(rules)


VOICE_RULES = _build_voice_rules()
VOICE_RULES_BY_ID = {rule.rule_id: rule for rule in VOICE_RULES}
COMPILED_VOICE_RULES = {
    rule.rule_id: re.compile(rule.pattern, rule.flags)
    for rule in VOICE_RULES
    if rule.pattern is not None
}


MANUAL_REVIEW = [
    "Check elegant variation: repeat the real name instead of cycling through forced synonyms.",
    "Check stance: remove generic may/could language where the evidence supports a direct claim.",
    "Check examples: use something that happened, with a name, mechanism, date, number, or constraint.",
    "Check verbs: replace abstract process language with a concrete action where it fits.",
    "Check humor and parenthetical asides by ear. The linter cannot manufacture personality.",
    "Read the piece aloud. Pull back anywhere the voice sounds like an AI performing humanness.",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _validate_regex_list(values: object, label: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError(f"{label} must be an array of strings")
    for item in values:
        if len(item) > 512:
            raise ValueError(f"{label} entries must be at most 512 characters")
        re.compile(item)
    return tuple(values)


def load_config(path: Path) -> VoiceConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Config must be a JSON object")
    allowed = {
        "format", "base_profile", "fail_on", "max_auto_risk", "check_quotes",
        "allowed_terms", "allowed_patterns", "disabled_rules", "severity_overrides", "notes",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"Unknown config field(s): {', '.join(unknown)}")

    format_name = raw.get("format", "auto")
    base_profile = raw.get("base_profile", "markdown")
    fail_on = raw.get("fail_on", "hard")
    max_auto_risk = raw.get("max_auto_risk", "low")
    check_quotes = raw.get("check_quotes", False)
    if format_name not in FORMATS:
        raise ValueError(f"format must be one of {', '.join(sorted(FORMATS))}")
    if base_profile not in base.BUILTIN_PROFILES:
        raise ValueError(f"Unknown base_profile: {base_profile}")
    if fail_on not in FAIL_LEVELS:
        raise ValueError(f"fail_on must be one of {', '.join(sorted(FAIL_LEVELS))}")
    if max_auto_risk not in {"low", "medium"}:
        raise ValueError("max_auto_risk must be low or medium")
    if not isinstance(check_quotes, bool):
        raise ValueError("check_quotes must be a boolean")

    def strings(name: str) -> tuple[str, ...]:
        value = raw.get(name, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{name} must be an array of strings")
        return tuple(value)

    disabled = strings("disabled_rules")
    unknown_rules = sorted(set(disabled) - set(VOICE_RULES_BY_ID) - set(base.RULES_BY_ID))
    if unknown_rules:
        raise ValueError(f"Unknown disabled rule(s): {', '.join(unknown_rules)}")

    severity_raw = raw.get("severity_overrides", {})
    if not isinstance(severity_raw, dict):
        raise ValueError("severity_overrides must be an object")
    severity: list[tuple[str, str]] = []
    known = set(VOICE_RULES_BY_ID) | set(base.RULES_BY_ID)
    for rid, level in severity_raw.items():
        if rid not in known:
            raise ValueError(f"Unknown severity override rule: {rid}")
        if level not in SEVERITY_RANK:
            raise ValueError(f"Bad severity {level!r} for {rid}")
        severity.append((rid, level))

    return VoiceConfig(
        format=format_name,
        base_profile=base_profile,
        fail_on=fail_on,
        max_auto_risk=max_auto_risk,
        check_quotes=check_quotes,
        allowed_terms=strings("allowed_terms"),
        allowed_patterns=_validate_regex_list(raw.get("allowed_patterns", []), "allowed_patterns"),
        disabled_rules=disabled,
        severity_overrides=tuple(sorted(severity)),
    )


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------


def _overlaps(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and end > other_start


def _line_col(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    previous = text.rfind("\n", 0, offset)
    return line, offset - previous


def _is_quoted_kind(kind: str | None) -> bool:
    if not kind:
        return False
    return any(token in kind for token in ("quoted_text", "markdown_blockquote"))


def _protected_kind(base_result, start: int, end: int, check_quotes: bool) -> str | None:
    for span in base_result.protected_spans:
        if not _overlaps(start, end, span.start, span.end):
            continue
        if span.exclude_from_analysis:
            return span.kind
        if not check_quotes and _is_quoted_kind(span.kind):
            return span.kind
    return None


def _allowed(text: str, start: int, end: int, config: VoiceConfig) -> bool:
    matched = text[start:end].casefold().strip(" \t\r\n.,;:!?()[]{}\"'“”‘’")
    if matched in {term.casefold().strip() for term in config.allowed_terms}:
        return True
    window_start = max(0, start - 120)
    window_end = min(len(text), end + 120)
    window = text[window_start:window_end]
    return any(re.search(pattern, window, re.IGNORECASE) for pattern in config.allowed_patterns)


def _effective_severity(rule_id: str, default: str, config: VoiceConfig) -> str:
    return config.severity_map().get(rule_id, default)


def _make_voice_finding(
    text: str,
    rule: VoiceRule,
    start: int,
    end: int,
    matched: str,
    config: VoiceConfig,
    *,
    suggestion: str | None = None,
    confidence: str = "high",
) -> UnifiedFinding:
    line, column = _line_col(text, start)
    return UnifiedFinding(
        source="voice_dna",
        rule_id=rule.rule_id,
        category=rule.category,
        severity=_effective_severity(rule.rule_id, rule.severity, config),
        start=start,
        end=end,
        line=line,
        column=column,
        matched_text=matched,
        explanation=rule.description,
        suggestion=suggestion or rule.suggestion,
        evidence_class=rule.evidence_class,
        false_positive_risk=rule.false_positive_risk,
        confidence=confidence,
    )


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    return base._sentence_spans(text)


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    return base._paragraph_spans(text)


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _looks_title_case(title: str) -> bool:
    title = re.sub(r"[*_`]+", "", title).strip()
    words = [word for word in WORD_RE.findall(title) if any(char.isalpha() for char in word)]
    if len(words) < 3:
        return False
    minor = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
    eligible = [word for index, word in enumerate(words) if index == 0 or word.casefold() not in minor]
    capped = sum(1 for word in eligible if word[:1].isupper())
    return len(eligible) >= 3 and capped / len(eligible) >= 0.8


def _procedural_voice_findings(text: str, masked: str, base_result, config: VoiceConfig) -> list[UnifiedFinding]:
    findings: list[UnifiedFinding] = []

    # No em dashes.
    rule = VOICE_RULES_BY_ID["VD_EM_DASH"]
    for match in re.finditer("—", text):
        if rule.rule_id in config.disabled_rules:
            continue
        if _protected_kind(base_result, match.start(), match.end(), config.check_quotes):
            continue
        if _allowed(text, match.start(), match.end(), config):
            continue
        findings.append(_make_voice_finding(text, rule, match.start(), match.end(), "—", config, suggestion="Use a comma, period, colon, semicolon, or parentheses."))

    # Paragraphs above 3 sentences.
    rule = VOICE_RULES_BY_ID["VD_LONG_PARAGRAPH"]
    if rule.rule_id not in config.disabled_rules:
        for start, end in _paragraph_spans(masked):
            count = len(_sentence_spans(masked[start:end]))
            if count > 3:
                findings.append(_make_voice_finding(
                    text, rule, start, end, f"{count} sentences", config,
                    suggestion="Split only where the argument naturally turns. The default is 1-2 sentences; 3 is the usual ceiling.",
                ))

    # Markdown heading case.
    if config.format in {"auto", "markdown"}:
        rule = VOICE_RULES_BY_ID["VD_TITLE_CASE_HEADING"]
        if rule.rule_id not in config.disabled_rules:
            for match in HEADING_RE.finditer(text):
                start, end = match.span("title")
                if _protected_kind(base_result, start, end, config.check_quotes):
                    continue
                if _looks_title_case(match.group("title")):
                    findings.append(_make_voice_finding(text, rule, start, end, match.group("title"), config, suggestion="Use sentence case unless a proper noun requires capitals."))

    # Bold density by Markdown section.
    if config.format in {"auto", "markdown"}:
        rule = VOICE_RULES_BY_ID["VD_BOLD_DENSITY"]
        if rule.rule_id not in config.disabled_rules:
            heading_starts = [match.start() for match in HEADING_RE.finditer(text)]
            boundaries = [0] + heading_starts + [len(text)]
            for left, right in zip(boundaries, boundaries[1:]):
                hits = [match for match in BOLD_RE.finditer(text, left, right) if not _protected_kind(base_result, match.start(), match.end(), config.check_quotes)]
                if len(hits) > 2:
                    findings.append(_make_voice_finding(
                        text, rule, hits[2].start(), hits[-1].end(), f"{len(hits)} bold spans", config,
                        suggestion="Keep 1-2 bold moments in this section. Remove the rest unless each one performs a distinct job.",
                    ))

    # Number words. Avoid "one" because it is often a pronoun.
    rule = VOICE_RULES_BY_ID["VD_NUMBER_WORD"]
    if rule.rule_id not in config.disabled_rules:
        number_words = {"two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
        for match in re.finditer(r"\b(?:two|three|four|five|six|seven|eight|nine|ten)\b", masked, re.I):
            # Skip idioms and headings where spelling may be intentional.
            context = masked[max(0, match.start() - 20):min(len(masked), match.end() + 25)].lower()
            if re.search(r"\b(?:between|either|both)\s+$", context[:20]) or "one or " in context:
                continue
            findings.append(_make_voice_finding(
                text, rule, match.start(), match.end(), text[match.start():match.end()], config,
                suggestion=f"Consider {number_words[match.group(0).lower()]} if this is a count rather than an idiom.",
                confidence="low",
            ))

    # Common uncontracted forms where speech would usually contract.
    rule = VOICE_RULES_BY_ID["VD_UNCONTRACTED_FORM"]
    if rule.rule_id not in config.disabled_rules:
        forms = {
            "do not": "don't", "does not": "doesn't", "did not": "didn't", "cannot": "can't",
            "can not": "can't", "will not": "won't", "would not": "wouldn't", "is not": "isn't",
            "are not": "aren't", "was not": "wasn't", "were not": "weren't", "i am": "I'm",
            "we are": "we're", "you are": "you're", "they are": "they're", "it is": "it's",
            "that is": "that's", "there is": "there's",
        }
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(key) for key in sorted(forms, key=len, reverse=True)) + r")\b", re.I)
        for match in pattern.finditer(masked):
            if _protected_kind(base_result, match.start(), match.end(), config.check_quotes):
                continue
            original = text[match.start():match.end()]
            replacement = forms[match.group(0).lower()]
            findings.append(_make_voice_finding(text, rule, match.start(), match.end(), original, config, suggestion=f"Consider {replacement!r} if it matches the intended emphasis.", confidence="low"))

    # One polished triad is a strong signal in this voice, though ordinary lists
    # of 3 can be legitimate. Keep the risk high and leave it to review.
    rule = VOICE_RULES_BY_ID["VD_RULE_OF_THREE"]
    if rule.rule_id not in config.disabled_rules:
        triad = re.compile(
            r"(?<![,;])\b(?P<a>[^,;.!?\n:]{1,45}),\s+(?P<b>[^,;.!?\n:]{1,45}),\s+(?:and|or)\s+(?P<c>[^,;.!?\n:]{1,45})(?=[.!?;]|$)",
            re.I,
        )
        for match in triad.finditer(masked):
            parts = [match.group(name).strip() for name in ("a", "b", "c")]
            if any(_word_count(part) > 8 for part in parts):
                continue
            if _protected_kind(base_result, match.start(), match.end(), config.check_quotes):
                continue
            findings.append(_make_voice_finding(
                text, rule, match.start(), match.end(), text[match.start():match.end()], config,
                suggestion="Keep the 3 items only when the content genuinely has 3 parts. Otherwise use 1, 2, or 4.",
                confidence="low",
            ))

    words = _word_count(masked)
    if words >= 180:
        # Specificity snapshot.
        rule = VOICE_RULES_BY_ID["VD_LOW_SPECIFICITY"]
        if rule.rule_id not in config.disabled_rules:
            numbers = len(NUMBER_RE.findall(masked))
            names = len(re.findall(r"(?<![.!?]\s)\b[A-Z][a-z]{2,}\b", text))
            quotes = len(re.findall(r"[“\"][^”\"\n]{3,80}[”\"]", text))
            date_terms = len(re.findall(r"\b(?:19|20)\d{2}\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", text))
            concrete = numbers + names + quotes + date_terms
            if concrete <= max(2, words // 180):
                findings.append(_make_voice_finding(
                    text, rule, 0, min(len(text), 1), f"{concrete} concrete markers across {words} words", config,
                    suggestion="Add a real name, number, date, quote, mechanism, or constraint where the claim needs one.",
                    confidence="low",
                ))

        # Direct address is a light preference, only for long explanatory prose.
        rule = VOICE_RULES_BY_ID["VD_NO_DIRECT_ADDRESS"]
        if rule.rule_id not in config.disabled_rules and not re.search(r"\b(?:I|me|my|we|our|you|your)\b", masked, re.I):
            findings.append(_make_voice_finding(
                text, rule, 0, min(len(text), 1), "no direct-address pronouns", config,
                suggestion="Consider I or you where direct address would make the relationship clearer.", confidence="low",
            ))

    return findings


def _lexical_voice_findings(text: str, masked: str, base_result, config: VoiceConfig) -> list[UnifiedFinding]:
    findings: list[UnifiedFinding] = []
    for rule in VOICE_RULES:
        if rule.pattern is None or not rule.enabled or rule.rule_id in config.disabled_rules:
            continue
        pattern = COMPILED_VOICE_RULES[rule.rule_id]
        for match in pattern.finditer(masked):
            protected_kind = _protected_kind(base_result, match.start(), match.end(), config.check_quotes)
            if protected_kind:
                continue
            if _allowed(text, match.start(), match.end(), config):
                continue
            findings.append(_make_voice_finding(text, rule, match.start(), match.end(), text[match.start():match.end()], config))
    return findings


def _base_severity(rule_id: str, category: str) -> str:
    if rule_id.startswith("NEG_") or category == "negative_parallelism":
        return "hard"
    if rule_id.startswith("EMDASH_"):
        return "hard"
    if rule_id.startswith("STRUCT_"):
        return "strong"
    if category in {
        "assistant_residue", "discourse_signposting", "grandiose_metaphor", "vague_importance",
        "controlled_revelation", "formulaic_architecture", "participial_framing",
    }:
        return "strong"
    return "light"


def _base_findings(text: str, base_result, config: VoiceConfig) -> list[UnifiedFinding]:
    findings: list[UnifiedFinding] = []
    for item in base_result.findings:
        if item.rule_id in config.disabled_rules:
            continue
        if item.protected and (not config.check_quotes or _is_quoted_kind(item.protected_kind)):
            continue
        if _allowed(text, item.start, item.end, config):
            continue
        severity = _effective_severity(item.rule_id, _base_severity(item.rule_id, item.category), config)
        suggestion = item.candidates[0] if item.candidates else "Review in context."
        findings.append(UnifiedFinding(
            source="base_refiner",
            rule_id=item.rule_id,
            category=item.category,
            severity=severity,
            start=item.start,
            end=item.end,
            line=item.line,
            column=item.column,
            matched_text=item.matched_text,
            explanation=item.explanation,
            suggestion=suggestion,
            evidence_class=item.evidence_class,
            false_positive_risk=item.false_positive_risk,
            confidence=item.confidence,
            protected=item.protected,
            protected_kind=item.protected_kind,
        ))
    return findings


def _dedupe_findings(findings: Iterable[UnifiedFinding]) -> list[UnifiedFinding]:
    ordered = sorted(
        findings,
        key=lambda item: (item.start, -(item.end - item.start), -SEVERITY_RANK[item.severity], item.source != "voice_dna", item.rule_id),
    )
    kept: list[UnifiedFinding] = []
    for finding in ordered:
        duplicate = False
        for prior in kept[-12:]:
            overlap = max(0, min(prior.end, finding.end) - max(prior.start, finding.start))
            smaller = min(max(1, prior.end - prior.start), max(1, finding.end - finding.start))
            if overlap / smaller < 0.8:
                continue
            if prior.rule_id == finding.rule_id:
                duplicate = True
                break
            if (
                prior.source == "voice_dna"
                and finding.source == "base_refiner"
                and SEVERITY_RANK[prior.severity] >= SEVERITY_RANK[finding.severity]
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(finding)
    return sorted(kept, key=lambda item: (item.start, item.end, item.rule_id))


def _analyze_once(text: str, config: VoiceConfig) -> tuple[list[UnifiedFinding], object, str]:
    base_config = base.RefinerConfig(
        mode="audit",
        profile=config.base_profile,
        em_dash_policy="eliminate",
        format=config.format,
        min_confidence="low",
        max_auto_risk=config.max_auto_risk,
        source_name=config.source_name,
    )
    base_result = base.analyze_text(text, base_config)
    format_name = str(base_result.metrics.get("format", config.format))
    effective = replace(config, format=format_name)
    masked = base._mask_for_analysis(text, base_result.protected_spans)
    findings = []
    findings.extend(_lexical_voice_findings(text, masked, base_result, effective))
    findings.extend(_procedural_voice_findings(text, masked, base_result, effective))
    findings.extend(_base_findings(text, base_result, effective))
    return _dedupe_findings(findings), base_result, format_name


# ---------------------------------------------------------------------------
# Conservative automatic edits
# ---------------------------------------------------------------------------


def _case_match(replacement: str, original: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _voice_safe_edits(text: str, base_result, config: VoiceConfig) -> list[tuple[int, int, str, str, str]]:
    edits: list[tuple[int, int, str, str, str]] = []
    rule = VOICE_RULES_BY_ID["VD_IN_ORDER_TO"]
    if rule.rule_id not in config.disabled_rules and RISK_RANK[rule.false_positive_risk] <= RISK_RANK[config.max_auto_risk]:
        pattern = COMPILED_VOICE_RULES[rule.rule_id]
        for match in pattern.finditer(text):
            if _protected_kind(base_result, match.start(), match.end(), config.check_quotes):
                continue
            replacement = _case_match("to", match.group(0))
            edits.append((match.start(), match.end(), replacement, rule.rule_id, "Replace 'in order to' with 'to'."))
    return edits


def _apply_offset_edits(text: str, edits: Sequence[tuple[int, int, str, str, str]]) -> tuple[str, list[AppliedEdit]]:
    result = text
    applied: list[AppliedEdit] = []
    selected: list[tuple[int, int, str, str, str]] = []
    for edit in sorted(edits, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(edit[0] < prior[1] and edit[1] > prior[0] for prior in selected):
            continue
        selected.append(edit)
    for start, end, replacement, rule_id, rationale in sorted(selected, reverse=True):
        original = result[start:end]
        result = result[:start] + replacement + result[end:]
        applied.append(AppliedEdit("voice_dna", rule_id, original, replacement, rationale))
    applied.reverse()
    return result, applied


def _auto_pass(text: str, config: VoiceConfig) -> tuple[str, list[AppliedEdit], list[str]]:
    warnings: list[str] = []
    base_config = base.RefinerConfig(
        mode="auto",
        profile=config.base_profile,
        em_dash_policy="eliminate",
        format=config.format,
        min_confidence="low",
        max_auto_risk=config.max_auto_risk,
        source_name=config.source_name,
    )
    base_result = base.refine_text(text, base_config)
    intermediate = base_result.refined_text
    edits = [
        AppliedEdit("base_refiner", item.rule_id, item.original, item.replacement, item.rationale)
        for item in base_result.edits
    ]
    warnings.extend(base_result.warnings)

    audit_config = replace(config, mode="audit")
    _, intermediate_base, _ = _analyze_once(intermediate, audit_config)
    voice_candidates = _voice_safe_edits(intermediate, intermediate_base, audit_config)
    refined, voice_edits = _apply_offset_edits(intermediate, voice_candidates)
    edits.extend(voice_edits)
    return refined, edits, warnings


# ---------------------------------------------------------------------------
# Metrics, reports, and public API
# ---------------------------------------------------------------------------


def _fails(finding: UnifiedFinding, fail_on: str) -> bool:
    if fail_on == "none":
        return False
    if fail_on == "any":
        return True
    if fail_on == "hard":
        return finding.severity == "hard"
    if fail_on == "strong":
        return SEVERITY_RANK[finding.severity] >= SEVERITY_RANK["strong"]
    raise ValueError(f"Unknown fail_on: {fail_on}")


def _texture_metrics(text: str) -> dict[str, object]:
    sentences = _sentence_spans(text)
    lengths = [_word_count(text[start:end]) for start, end in sentences]
    paragraphs = _paragraph_spans(text)
    paragraph_counts = [len(_sentence_spans(text[start:end])) for start, end in paragraphs]
    mean = statistics.fmean(lengths) if lengths else 0.0
    stdev = statistics.pstdev(lengths) if len(lengths) >= 2 else 0.0
    cv = stdev / mean if mean else 0.0
    return {
        "sentence_word_mean": round(mean, 2),
        "sentence_word_min": min(lengths, default=0),
        "sentence_word_max": max(lengths, default=0),
        "sentence_length_cv": round(cv, 3),
        "paragraph_sentence_counts": paragraph_counts,
        "contraction_count": len(re.findall(r"\b\w+(?:n(?:'|’)t|(?:'|’)(?:m|re|ve|ll|d|s))\b", text, re.I)),
        "direct_address_count": len(re.findall(r"\b(?:I|me|my|we|our|you|your)\b", text, re.I)),
        "number_count": len(NUMBER_RE.findall(text)),
        "parenthetical_count": len(re.findall(r"\([^()\n]{2,160}\)", text)),
    }


def _metrics(text: str, findings: Sequence[UnifiedFinding], config: VoiceConfig, format_name: str, *, idempotent: bool) -> dict[str, object]:
    severity_counts = Counter(item.severity for item in findings)
    source_counts = Counter(item.source for item in findings)
    category_counts = Counter(item.category for item in findings)
    failure_count = sum(1 for item in findings if _fails(item, config.fail_on))
    return {
        "mode": config.mode,
        "format": format_name,
        "base_profile": config.base_profile,
        "fail_on": config.fail_on,
        "word_count": _word_count(text),
        "finding_count": len(findings),
        "failure_count": failure_count,
        "status": "fail" if failure_count else "pass",
        "severity_counts": dict(sorted(severity_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "voice_rule_count": len(VOICE_RULES),
        "base_rule_count": len(base.RULES),
        "idempotent_check": idempotent,
        "texture": _texture_metrics(text),
    }


def analyze_text(text: str, config: VoiceConfig | None = None) -> SystemResult:
    effective = replace(config or VoiceConfig(), mode="audit")
    findings, _, format_name = _analyze_once(text, effective)
    metrics = _metrics(text, findings, effective, format_name, idempotent=True)
    return SystemResult(text, text, findings, findings, [], [], metrics, list(MANUAL_REVIEW))


def refine_text(text: str, config: VoiceConfig | None = None) -> SystemResult:
    effective = config or VoiceConfig()
    if effective.mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(sorted(MODES))}")
    if effective.fail_on not in FAIL_LEVELS:
        raise ValueError(f"fail_on must be one of {', '.join(sorted(FAIL_LEVELS))}")
    if effective.max_auto_risk not in {"low", "medium"}:
        raise ValueError("max_auto_risk must be low or medium")
    if effective.base_profile not in base.BUILTIN_PROFILES:
        raise ValueError(f"Unknown base_profile: {effective.base_profile}")

    initial, _, initial_format = _analyze_once(text, effective)
    if effective.mode != "auto":
        metrics = _metrics(text, initial, effective, initial_format, idempotent=True)
        return SystemResult(text, text, initial, initial, [], [], metrics, list(MANUAL_REVIEW))

    refined, edits, warnings = _auto_pass(text, effective)
    remaining, _, format_name = _analyze_once(refined, replace(effective, mode="audit"))
    second, _, _ = _auto_pass(refined, effective)
    idempotent = second == refined
    if not idempotent:
        warnings.append("Idempotency check failed: a second auto pass would make more edits.")
    metrics = _metrics(refined, remaining, effective, format_name, idempotent=idempotent)
    metrics["applied_edit_count"] = len(edits)
    return SystemResult(text, refined, remaining, initial, edits, warnings, metrics, list(MANUAL_REVIEW))


def render_unified_diff(result: SystemResult, source_name: str = "input") -> str:
    if not result.changed:
        return ""
    return "".join(unified_diff(
        result.original_text.splitlines(keepends=True),
        result.refined_text.splitlines(keepends=True),
        fromfile=f"{source_name} (original)",
        tofile=f"{source_name} (refined)",
    ))


def render_human_report(result: SystemResult, source_name: str = "input") -> str:
    metrics = result.metrics
    lines = [
        f"{TOOL_NAME} {VERSION}",
        f"Source: {source_name}",
        f"Mode / format / base: {metrics.get('mode')} / {metrics.get('format')} / {metrics.get('base_profile')}",
        f"Status: {str(metrics.get('status')).upper()} (fail on {metrics.get('fail_on')})",
        f"Words / findings / failures: {metrics.get('word_count')} / {metrics.get('finding_count')} / {metrics.get('failure_count')}",
        f"Rules: voice={metrics.get('voice_rule_count')} + base={metrics.get('base_rule_count')}",
        f"Idempotent: {metrics.get('idempotent_check')}",
    ]
    severity = metrics.get("severity_counts", {})
    if isinstance(severity, dict):
        lines.append("Severity: " + ", ".join(f"{key}={value}" for key, value in severity.items()) if severity else "Severity: none")
    texture = metrics.get("texture", {})
    if isinstance(texture, dict):
        lines.append(
            "Texture: sentence words "
            f"{texture.get('sentence_word_min')}-{texture.get('sentence_word_max')} "
            f"(mean {texture.get('sentence_word_mean')}, CV {texture.get('sentence_length_cv')}); "
            f"contractions={texture.get('contraction_count')}; direct address={texture.get('direct_address_count')}; "
            f"numbers={texture.get('number_count')}; parentheticals={texture.get('parenthetical_count')}"
        )
    if result.edits:
        lines.append("Applied edits:")
        for edit in result.edits:
            lines.append(f"  - [{edit.source}:{edit.rule_id}] {edit.original!r} -> {edit.replacement!r}")
    if result.findings:
        lines.append("Remaining findings:")
        for item in result.findings:
            lines.append(
                f"  - {item.severity.upper()} L{item.line}:C{item.column} "
                f"[{item.source}:{item.rule_id}] {item.matched_text!r}"
            )
            lines.append(f"    {item.explanation}")
            lines.append(f"    Fix: {item.suggestion}")
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    lines.append("Manual pass:")
    lines.extend(f"  - {item}" for item in result.manual_review)
    lines.append("Limitation: these are editorial signals and house-style checks, not authorship evidence.")
    return "\n".join(lines) + "\n"


def render_markdown_report(result: SystemResult, source_name: str = "input") -> str:
    metrics = result.metrics
    lines = [
        "# VOICE DNA report",
        "",
        f"**Source:** `{source_name}`  ",
        f"**Status:** {str(metrics.get('status')).upper()}  ",
        f"**Policy:** fail on {metrics.get('fail_on')}  ",
        f"**Findings:** {metrics.get('finding_count')} ({metrics.get('failure_count')} failures)",
        "",
    ]
    if result.edits:
        lines.extend(["## Applied edits", ""])
        for edit in result.edits:
            lines.append(f"- `{edit.rule_id}`: `{edit.original}` → `{edit.replacement}`")
        lines.append("")
    if result.findings:
        lines.extend(["## Findings", ""])
        for item in result.findings:
            lines.append(f"### {item.severity.upper()}: `{item.rule_id}` at L{item.line}:C{item.column}")
            lines.append("")
            lines.append(f"Matched: `{item.matched_text}`")
            lines.append("")
            lines.append(item.explanation)
            lines.append("")
            lines.append(f"**Edit:** {item.suggestion}")
            lines.append("")
    lines.extend(["## Manual pass", ""])
    lines.extend(f"- {item}" for item in result.manual_review)
    lines.extend(["", "_Editorial signals and house-style checks, not authorship evidence._", ""])
    return "\n".join(lines)


def prompt_text() -> str:
    return """Use VOICE-DNA.md as the source of truth for this draft.

Write the piece once, then run a separate editorial pass:

1. Remove every hard-rule violation unless the word or construction is genuinely the best choice. Mark deliberate exceptions instead of silently weakening the rule.
2. Check strong tendencies across the whole piece: short paragraphs, varied sentence length, direct address, contractions, specific details, natural transitions, and sentence-case headings.
3. Treat light preferences as taste, not quotas. Do not bolt on jokes, fragments, physical verbs, or parenthetical asides merely to imitate a voice.
4. Preserve facts, uncertainty, names, numbers, quotations, citations, code, links, and formatting.
5. End when the point is made. Do not add a summary that repeats the draft.

Final ear test: Does this sound like a sharp human typing, or like an AI trying very hard to imitate one?
"""


# ---------------------------------------------------------------------------
# CLI and file handling
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str, preserve_mode_from: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    if preserve_mode_from and preserve_mode_from.exists():
        mode = preserve_mode_from.stat().st_mode
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()
        raise


def _write_json(path: Path, result: SystemResult, source: str) -> None:
    _atomic_write(path, json.dumps(result.to_payload(source), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _list_rules() -> str:
    lines = ["source\trule_id\tcategory\tseverity\trisk\tdescription"]
    for rule in VOICE_RULES:
        lines.append("\t".join(("voice_dna", rule.rule_id, rule.category, rule.severity, rule.false_positive_risk, rule.description.replace("\t", " "))))
    for rule in base.RULES:
        lines.append("\t".join(("base_refiner", rule.rule_id, rule.category, _base_severity(rule.rule_id, rule.category), rule.false_positive_risk, rule.description.replace("\t", " "))))
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice_dna_refiner.py",
        description="Run the VOICE DNA house-style policy and broad deterministic copy refiner together.",
    )
    parser.add_argument("input", nargs="?", help="UTF-8 input path, or '-' for standard input")
    parser.add_argument("--mode", choices=sorted(MODES), default="audit")
    parser.add_argument("--check", action="store_true", help="Return status 1 when findings meet --fail-on")
    parser.add_argument("--fail-on", choices=sorted(FAIL_LEVELS), help="hard, strong, any, or none")
    parser.add_argument("--config", type=Path, help="JSON config with exceptions and policy overrides")
    parser.add_argument("--format", choices=sorted(FORMATS))
    parser.add_argument("--base-profile", choices=sorted(base.BUILTIN_PROFILES))
    parser.add_argument("--max-auto-risk", choices=("low", "medium"))
    parser.add_argument("--check-quotes", action="store_true")
    parser.add_argument("--allow-term", action="append", default=[], help="Allow one exact term for this run; repeatable")
    parser.add_argument("--allow-pattern", action="append", default=[], help="Allow matches inside a regex window; repeatable")
    parser.add_argument("--output", type=Path, help="Write auto-refined text to a new file")
    parser.add_argument("--apply", action="store_true", help="Atomically overwrite input; requires --mode auto")
    parser.add_argument("--diff", action="store_true")
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-markdown", type=Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--list-rules", action="store_true")
    parser.add_argument("--emit-prompt", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    utility = sum(bool(item) for item in (args.list_rules, args.emit_prompt, args.self_test))
    if utility > 1:
        raise ValueError("Choose only one of --list-rules, --emit-prompt, or --self-test")
    if utility:
        if args.input is not None:
            raise ValueError("Utility modes do not take an input file")
        return
    if args.input is None:
        raise ValueError("An input path or '-' is required")
    if args.apply and args.mode != "auto":
        raise ValueError("--apply requires --mode auto")
    if args.output and args.mode != "auto":
        raise ValueError("--output requires --mode auto")
    if args.apply and args.output:
        raise ValueError("--apply and --output are mutually exclusive")
    if args.apply and args.input == "-":
        raise ValueError("--apply cannot overwrite standard input")
    if args.check and (args.apply or args.output):
        raise ValueError("--check cannot be combined with --apply or --output")


def _config_from_args(args: argparse.Namespace, source: str) -> VoiceConfig:
    config = load_config(args.config) if args.config else VoiceConfig()
    return replace(
        config,
        mode="audit" if args.check else args.mode,
        format=args.format or config.format,
        base_profile=args.base_profile or config.base_profile,
        fail_on=args.fail_on or config.fail_on,
        max_auto_risk=args.max_auto_risk or config.max_auto_risk,
        check_quotes=args.check_quotes or config.check_quotes,
        allowed_terms=tuple(dict.fromkeys(config.allowed_terms + tuple(args.allow_term))),
        allowed_patterns=tuple(dict.fromkeys(config.allowed_patterns + tuple(args.allow_pattern))),
        source_name=source,
    )


def _process(args: argparse.Namespace) -> int:
    source = "stdin" if args.input == "-" else str(args.input)
    try:
        if args.input == "-":
            original = sys.stdin.read()
        else:
            path = Path(args.input)
            if not path.is_file():
                raise OSError(f"Input is not a regular file: {path}")
            original = path.read_bytes().decode("utf-8")
        config = _config_from_args(args, source)
        result = refine_text(original, config)
    except (OSError, UnicodeError, ValueError, RuntimeError, re.error) as exc:
        print(f"[{TOOL_NAME}] error: {exc}", file=sys.stderr)
        return EXIT_IO

    if args.report_json:
        _write_json(args.report_json, result, source)
    if args.report_markdown:
        _atomic_write(args.report_markdown, render_markdown_report(result, source))

    if args.apply or args.output:
        if not result.metrics.get("idempotent_check"):
            print(f"[{TOOL_NAME}] error: refusing to write non-idempotent output", file=sys.stderr)
            return EXIT_IO
        target = Path(args.input) if args.apply else args.output
        assert target is not None
        _atomic_write(target, result.refined_text, preserve_mode_from=Path(args.input) if args.apply else None)

    if args.diff:
        sys.stdout.write(render_unified_diff(result, source))
    if args.mode == "auto" and not args.apply and not args.output and not args.check:
        sys.stdout.write(result.refined_text)
        if not args.quiet:
            sys.stderr.write(render_human_report(result, source))
    elif not args.quiet:
        sys.stdout.write(render_human_report(result, source))

    if args.check and int(result.metrics.get("failure_count", 0)) > 0:
        return EXIT_FINDINGS
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        _validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    if args.list_rules:
        sys.stdout.write(_list_rules())
        return EXIT_OK
    if args.emit_prompt:
        sys.stdout.write(prompt_text())
        return EXIT_OK
    if args.self_test:
        return run_self_tests()
    return _process(args)


# ---------------------------------------------------------------------------
# Embedded tests
# ---------------------------------------------------------------------------


class _Tests(unittest.TestCase):
    def test_registry_size(self) -> None:
        self.assertGreaterEqual(len(VOICE_RULES), 120)
        self.assertGreaterEqual(len(base.RULES), 300)

    def test_banned_word_is_hard(self) -> None:
        result = analyze_text("This robust framework will unlock value.")
        ids = {item.rule_id for item in result.findings}
        self.assertTrue(any(rid.startswith("VD_WORD") for rid in ids))
        self.assertGreaterEqual(result.metrics["failure_count"], 2)

    def test_negative_reframe_is_hard(self) -> None:
        result = analyze_text("It's not about prompts. It's about context.")
        self.assertTrue(any(item.category == "negative_parallelism" and item.severity == "hard" for item in result.findings))

    def test_quotes_are_ignored_by_default(self) -> None:
        result = analyze_text('She wrote, “This robust tool will unlock value.”')
        self.assertFalse(any(item.rule_id.startswith("VD_WORD") for item in result.findings))

    def test_quotes_can_be_checked(self) -> None:
        result = analyze_text('She wrote, “This robust tool will unlock value.”', VoiceConfig(check_quotes=True))
        self.assertTrue(any(item.rule_id.startswith("VD_WORD") for item in result.findings))

    def test_allow_term(self) -> None:
        result = analyze_text("The JSON is transparent.", VoiceConfig(allowed_terms=("transparent",)))
        self.assertFalse(any(item.matched_text.lower() == "transparent" and item.source == "voice_dna" for item in result.findings))

    def test_em_dash(self) -> None:
        result = analyze_text("The sample was small—but useful.")
        self.assertTrue(any(item.rule_id == "VD_EM_DASH" for item in result.findings))

    def test_auto_safe_edit(self) -> None:
        result = refine_text("In order to test it, we ran 5 interviews.", VoiceConfig(mode="auto"))
        self.assertEqual(result.refined_text, "To test it, we ran 5 interviews.")
        self.assertTrue(result.metrics["idempotent_check"])

    def test_title_case_heading(self) -> None:
        result = analyze_text("## Global Context and Critical Mineral Demand\n\nText.", VoiceConfig(format="markdown"))
        self.assertTrue(any(item.rule_id == "VD_TITLE_CASE_HEADING" for item in result.findings))

    def test_inflected_banned_word(self) -> None:
        result = analyze_text("The tool unlocks value and streamlines work.")
        matches = {item.matched_text.lower() for item in result.findings if item.category == "dead_ai_vocabulary"}
        self.assertIn("unlocks", matches)
        self.assertIn("streamlines", matches)

    def test_rule_of_three(self) -> None:
        result = analyze_text("We wanted speed, clarity, and innovation.")
        self.assertTrue(any(item.rule_id == "VD_RULE_OF_THREE" for item in result.findings))

    def test_fail_levels(self) -> None:
        text = "## Global Context and Critical Mineral Demand\n\nText."
        hard = analyze_text(text, VoiceConfig(format="markdown", fail_on="hard"))
        strong = analyze_text(text, VoiceConfig(format="markdown", fail_on="strong"))
        self.assertEqual(hard.metrics["failure_count"], 0)
        self.assertGreater(strong.metrics["failure_count"], 0)

    def test_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"allowed_terms": ["robust"], "fail_on": "strong"}), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.allowed_terms, ("robust",))
            self.assertEqual(config.fail_on, "strong")

    def test_base_findings_present(self) -> None:
        result = analyze_text("At its core, the system is useful.")
        self.assertTrue(any(item.source == "base_refiner" for item in result.findings))


def run_self_tests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(_Tests)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if result.wasSuccessful():
        print(f"SELF-TEST PASS: tests={result.testsRun}; voice_rules={len(VOICE_RULES)}; base_rules={len(base.RULES)}")
        return EXIT_OK
    sys.stderr.write(stream.getvalue())
    print(f"SELF-TEST FAIL: tests={result.testsRun}; failures={len(result.failures)}; errors={len(result.errors)}", file=sys.stderr)
    return EXIT_SELF_TEST


if __name__ == "__main__":
    raise SystemExit(main())
