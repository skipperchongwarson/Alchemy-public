#!/usr/bin/env python3
"""Portable, deterministic post-generation copy refiner.

The tool detects formulaic, mechanically polished, over-signposted, or
register-incongruent prose. It is an editorial linter, not an authorship
classifier. Automatic edits are deliberately conservative and auditable.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import io
import json
import math
import re
import statistics
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from difflib import SequenceMatcher, unified_diff
from pathlib import Path

TOOL_NAME = "llm_copy_refiner"
VERSION = "1.0.0"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_SELF_TEST = 4

__all__ = [
    "AuthorProfile",
    "Edit",
    "Finding",
    "RefinementResult",
    "RefinerConfig",
    "StyleRule",
    "analyze_text",
    "load_author_profile",
    "refine_text",
    "render_human_report",
    "render_unified_diff",
]

EVIDENCE_CLASSES = {
    "research-supported",
    "corroborated-heuristic",
    "house-style-heuristic",
    "safety-sanitation",
}
RISK_LEVELS = {"low": 0, "medium": 1, "high": 2}
CONFIDENCE_LEVELS = {"low": 0, "medium": 1, "high": 2}
MODES = {"audit", "suggest", "auto"}
FORMATS = {"auto", "plain", "markdown", "html", "latex"}
EM_DASH_POLICIES = {"preserve", "reduce", "eliminate"}

WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
NUMBER_RE = re.compile(r"(?<!\w)[+-]?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?%?(?!\w)")
URL_RE = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)[^\s<>()\[\]{}]+")
EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,}(?![\w-]|\.[A-Za-z0-9])")
DOI_RE = re.compile(r"(?i)\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b")


@dataclass(frozen=True)
class StyleRule:
    """One lexical, syntactic, rhetorical, structural, or sanitation rule."""

    rule_id: str
    category: str
    description: str
    evidence_class: str
    signal_weight: float
    false_positive_risk: str
    action: str
    pattern: str | None
    replacement_strategy: str
    enabled_by_default: bool
    suggested_replacements: tuple[str, ...] = ()
    min_occurrences: int = 1
    confidence: str = "medium"
    flags: int = re.IGNORECASE
    notes: str = ""


@dataclass(frozen=True)
class Finding:
    """A located marker or document-level review finding."""

    rule_id: str
    category: str
    start: int
    end: int
    line: int
    column: int
    matched_text: str
    explanation: str
    signal_weight: float
    false_positive_risk: str
    confidence: str
    candidates: tuple[str, ...]
    evidence_class: str
    protected: bool = False
    protected_kind: str | None = None


@dataclass(frozen=True)
class Edit:
    """A proposed or applied replacement."""

    rule_id: str
    start: int
    end: int
    original: str
    replacement: str
    mode: str
    rationale: str


@dataclass(frozen=True)
class ProtectedSpan:
    """A source interval excluded from analysis and/or automatic edits."""

    start: int
    end: int
    kind: str
    exclude_from_analysis: bool = True
    exclude_from_edit: bool = True


@dataclass(frozen=True)
class AuthorProfile:
    """Validated house-style configuration loaded from JSON."""

    name: str = "Unnamed profile"
    version: str = "1.0"
    preferred_sentence_words: tuple[int, int] = (8, 28)
    preferred_paragraph_sentences: tuple[int, int] = (1, 5)
    em_dash_policy: str = "reduce"
    allow_contractions: bool = True
    preferred_transitions: tuple[str, ...] = ()
    discouraged_phrases: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()
    protected_patterns: tuple[str, ...] = ()
    category_weights: tuple[tuple[str, float], ...] = ()
    disabled_rules: tuple[str, ...] = ()
    enabled_rules: tuple[str, ...] = ()
    notes: str = ""

    def category_weight_map(self) -> dict[str, float]:
        return dict(self.category_weights)


@dataclass(frozen=True)
class BuiltinProfile:
    """Built-in defaults for a publication context."""

    name: str
    description: str
    em_dash_policy: str
    category_weights: tuple[tuple[str, float], ...] = ()
    disabled_categories: tuple[str, ...] = ()
    max_auto_risk: str = "low"

    def weight_map(self) -> dict[str, float]:
        return dict(self.category_weights)


@dataclass(frozen=True)
class RefinerConfig:
    """Runtime configuration for library and CLI use."""

    mode: str = "audit"
    profile: str = "plain"
    em_dash_policy: str | None = None
    format: str = "auto"
    min_confidence: str = "low"
    max_auto_risk: str = "low"
    author_profile: AuthorProfile | None = None
    edit_quotes: bool = False
    source_name: str | None = None


@dataclass
class RefinementResult:
    """Complete, deterministic result for one input text."""

    original_text: str
    refined_text: str
    findings: list[Finding]
    edits: list[Edit]
    warnings: list[str]
    metrics: dict[str, object]
    protected_spans: list[ProtectedSpan] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original_text != self.refined_text

    def to_payload(self, source: str | None = None) -> dict[str, object]:
        return {
            "tool": TOOL_NAME,
            "version": VERSION,
            "source": source,
            "mode": self.metrics.get("mode"),
            "profile": self.metrics.get("profile"),
            "format": self.metrics.get("format"),
            "em_dash_policy": self.metrics.get("em_dash_policy"),
            "metrics": self.metrics,
            "findings": [asdict(item) for item in self.findings],
            "edits": [asdict(item) for item in self.edits],
            "warnings": list(self.warnings),
            "changed": self.changed,
            "idempotent_check": bool(self.metrics.get("idempotent_check", False)),
        }


BUILTIN_PROFILES: dict[str, BuiltinProfile] = {
    "plain": BuiltinProfile(
        name="plain",
        description="General prose with conservative automatic edits.",
        em_dash_policy="reduce",
    ),
    "markdown": BuiltinProfile(
        name="markdown",
        description="General Markdown prose with strong literal-span protection.",
        em_dash_policy="reduce",
    ),
    "formal": BuiltinProfile(
        name="formal",
        description="Professional prose; conversational residue is weighted more heavily.",
        em_dash_policy="reduce",
        category_weights=(("assistant_residue", 1.35), ("corporate_abstraction", 1.15)),
    ),
    "conversational": BuiltinProfile(
        name="conversational",
        description="Conversation-friendly profile that de-emphasizes assistant-like politeness.",
        em_dash_policy="preserve",
        category_weights=(("assistant_residue", 0.35), ("soft_intensifier", 0.65)),
    ),
    "academic": BuiltinProfile(
        name="academic",
        description="Academic prose with citation and modality safeguards.",
        em_dash_policy="preserve",
        category_weights=(("unsupported_sourcing", 1.5), ("research_lexicon", 0.75)),
        disabled_categories=("generic_audience",),
    ),
    "minimal": BuiltinProfile(
        name="minimal",
        description="Only sanitation, strong formulaic constructions, and density findings.",
        em_dash_policy="preserve",
        disabled_categories=(
            "soft_intensifier",
            "hollow_pairing",
            "generic_example",
            "generic_audience",
            "corporate_abstraction",
        ),
    ),
}


class LineMap:
    """Efficient offset-to-line/column conversion."""

    def __init__(self, text: str) -> None:
        self.starts = [0]
        self.starts.extend(match.end() for match in re.finditer(r"\n", text))

    def line_col(self, offset: int) -> tuple[int, int]:
        index = bisect.bisect_right(self.starts, max(0, offset)) - 1
        return index + 1, offset - self.starts[index] + 1


def _validate_choice(value: str, allowed: set[str], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"{label} must be one of {', '.join(sorted(allowed))}; got {value!r}")


def _validate_int_pair(value: object, label: str) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, int) for item in value):
        raise ValueError(f"{label} must be a two-integer JSON array")
    low, high = value
    if low < 0 or high < low:
        raise ValueError(f"{label} must satisfy 0 <= minimum <= maximum")
    return low, high


def _validate_string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a JSON array of strings")
    return tuple(value)


def load_author_profile(path: Path) -> AuthorProfile:
    """Load and validate an author or house-style profile from JSON."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load author profile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Author profile must be a JSON object")

    allowed_keys = {
        "name",
        "version",
        "preferred_sentence_words",
        "preferred_paragraph_sentences",
        "em_dash_policy",
        "allow_contractions",
        "preferred_transitions",
        "discouraged_phrases",
        "forbidden_phrases",
        "protected_patterns",
        "category_weights",
        "disabled_rules",
        "enabled_rules",
        "notes",
    }
    unknown = sorted(set(raw) - allowed_keys)
    if unknown:
        raise ValueError(f"Unknown author-profile field(s): {', '.join(unknown)}")

    name = raw.get("name", "Unnamed profile")
    version = raw.get("version", "1.0")
    notes = raw.get("notes", "")
    allow_contractions = raw.get("allow_contractions", True)
    if not isinstance(name, str) or not isinstance(version, str) or not isinstance(notes, str):
        raise ValueError("name, version, and notes must be strings")
    if not isinstance(allow_contractions, bool):
        raise ValueError("allow_contractions must be a boolean")

    sentence_words = _validate_int_pair(raw.get("preferred_sentence_words", [8, 28]), "preferred_sentence_words")
    paragraph_sentences = _validate_int_pair(
        raw.get("preferred_paragraph_sentences", [1, 5]), "preferred_paragraph_sentences"
    )
    em_dash_policy = raw.get("em_dash_policy", "reduce")
    if not isinstance(em_dash_policy, str):
        raise ValueError("em_dash_policy must be a string")
    _validate_choice(em_dash_policy, EM_DASH_POLICIES, "em_dash_policy")

    category_weights_raw = raw.get("category_weights", {})
    if not isinstance(category_weights_raw, dict):
        raise ValueError("category_weights must be a JSON object")
    category_weights: list[tuple[str, float]] = []
    for key, value in category_weights_raw.items():
        if not isinstance(key, str) or not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("category_weights must map strings to numbers")
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError("category_weights values must be finite and nonnegative")
        category_weights.append((key, float(value)))

    protected_patterns = _validate_string_list(raw.get("protected_patterns", []), "protected_patterns")
    for pattern in protected_patterns:
        if len(pattern) > 512:
            raise ValueError("protected_patterns entries must not exceed 512 characters")
        # Python's standard regex engine has no timeout. Reject a common class
        # of nested-repeat expressions before a profile can turn local editing
        # into an accidental denial-of-service benchmark.
        if re.search(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]", pattern):
            raise ValueError(f"Potentially unsafe nested repetition in protected pattern {pattern!r}")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid protected pattern {pattern!r}: {exc}") from exc

    disabled_rules = _validate_string_list(raw.get("disabled_rules", []), "disabled_rules")
    enabled_rules = _validate_string_list(raw.get("enabled_rules", []), "enabled_rules")
    if set(disabled_rules) & set(enabled_rules):
        overlap = ", ".join(sorted(set(disabled_rules) & set(enabled_rules)))
        raise ValueError(f"Rule IDs cannot be both enabled and disabled: {overlap}")
    known_rule_ids = set(globals().get("RULES_BY_ID", {}))
    if known_rule_ids:
        unknown_rule_ids = sorted((set(disabled_rules) | set(enabled_rules)) - known_rule_ids)
        if unknown_rule_ids:
            raise ValueError(f"Unknown rule ID(s): {', '.join(unknown_rule_ids)}")
        known_categories = {rule.category for rule in globals().get("RULES", ())}
        unknown_categories = sorted(key for key, _ in category_weights if key not in known_categories)
        if unknown_categories:
            raise ValueError(f"Unknown category weight(s): {', '.join(unknown_categories)}")

    return AuthorProfile(
        name=name,
        version=version,
        preferred_sentence_words=sentence_words,
        preferred_paragraph_sentences=paragraph_sentences,
        em_dash_policy=em_dash_policy,
        allow_contractions=allow_contractions,
        preferred_transitions=_validate_string_list(raw.get("preferred_transitions", []), "preferred_transitions"),
        discouraged_phrases=_validate_string_list(raw.get("discouraged_phrases", []), "discouraged_phrases"),
        forbidden_phrases=_validate_string_list(raw.get("forbidden_phrases", []), "forbidden_phrases"),
        protected_patterns=protected_patterns,
        category_weights=tuple(sorted(category_weights)),
        disabled_rules=disabled_rules,
        enabled_rules=enabled_rules,
        notes=notes,
    )


def _resolve_config(config: RefinerConfig) -> RefinerConfig:
    _validate_choice(config.mode, MODES, "mode")
    _validate_choice(config.format, FORMATS, "format")
    _validate_choice(config.min_confidence, set(CONFIDENCE_LEVELS), "min_confidence")
    _validate_choice(config.max_auto_risk, {"low", "medium"}, "max_auto_risk")
    if config.profile not in BUILTIN_PROFILES:
        raise ValueError(f"Unknown built-in profile {config.profile!r}")
    profile = BUILTIN_PROFILES[config.profile]
    em_dash_policy = config.em_dash_policy
    if em_dash_policy is None and config.author_profile is not None:
        em_dash_policy = config.author_profile.em_dash_policy
    if em_dash_policy is None:
        em_dash_policy = profile.em_dash_policy
    _validate_choice(em_dash_policy, EM_DASH_POLICIES, "em_dash_policy")
    return replace(config, em_dash_policy=em_dash_policy)


def _detect_format(configured: str, source_name: str | None, text: str) -> str:
    if configured != "auto":
        return configured
    if source_name:
        suffix = Path(source_name).suffix.lower()
        if suffix in {".md", ".markdown", ".mdown"}:
            return "markdown"
        if suffix in {".html", ".htm"}:
            return "html"
        if suffix in {".tex", ".latex"}:
            return "latex"
    if re.search(r"(?m)^\s{0,3}(?:#{1,6}\s|[-*+]\s|```|~~~)", text) or re.search(r"`[^`\r\n]+`|\[[^\]\r\n]+\]\([^\r\n)]+\)", text):
        return "markdown"
    if re.search(r"(?is)<(?:html|body|article|section|p|pre|code)\b", text):
        return "html"
    if re.search(r"\\(?:begin|section|chapter|documentclass)\b", text):
        return "latex"
    return "plain"


def _overlaps(start: int, end: int, span: ProtectedSpan) -> bool:
    return start < span.end and end > span.start


def _span_at(spans: Sequence[ProtectedSpan], start: int, end: int, *, for_edit: bool) -> ProtectedSpan | None:
    for span in spans:
        if (span.exclude_from_edit if for_edit else span.exclude_from_analysis) and _overlaps(start, end, span):
            return span
    return None


def _merge_protected_spans(spans: Iterable[ProtectedSpan]) -> list[ProtectedSpan]:
    ordered = sorted((span for span in spans if span.end > span.start), key=lambda item: (item.start, item.end))
    if not ordered:
        return []
    merged: list[ProtectedSpan] = [ordered[0]]
    for span in ordered[1:]:
        current = merged[-1]
        if span.start <= current.end:
            kinds = sorted(set(current.kind.split("+") + span.kind.split("+")))
            merged[-1] = ProtectedSpan(
                start=current.start,
                end=max(current.end, span.end),
                kind="+".join(kinds),
                exclude_from_analysis=current.exclude_from_analysis or span.exclude_from_analysis,
                exclude_from_edit=current.exclude_from_edit or span.exclude_from_edit,
            )
        else:
            merged.append(span)
    return merged


def _find_fenced_code_spans(text: str) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    line_matches = list(re.finditer(r".*?(?:\r\n|\n|\r|$)", text))
    open_start: int | None = None
    fence_char = ""
    fence_len = 0
    for match in line_matches:
        line = match.group(0)
        stripped = line.rstrip("\r\n")
        if open_start is None:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})(?:[^`~].*)?$", stripped)
            if opening:
                token = opening.group(1)
                open_start = match.start()
                fence_char = token[0]
                fence_len = len(token)
        else:
            closing = re.match(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$", stripped)
            if closing:
                spans.append(ProtectedSpan(open_start, match.end(), "markdown_fenced_code"))
                open_start = None
                fence_char = ""
                fence_len = 0
    if open_start is not None:
        spans.append(ProtectedSpan(open_start, len(text), "markdown_fenced_code"))
    return spans


def _find_markdown_indented_code_spans(text: str, existing: Sequence[ProtectedSpan]) -> list[ProtectedSpan]:
    """Protect conservative Markdown indented-code lines outside fences."""

    spans: list[ProtectedSpan] = []
    for match in re.finditer(r".*?(?:\r\n|\n|\r|$)", text):
        if match.start() == match.end() or _span_at(existing, match.start(), match.end(), for_edit=False):
            continue
        line = match.group(0).rstrip("\r\n")
        if re.match(r"^(?: {4}|\t)\S", line):
            spans.append(ProtectedSpan(match.start(), match.end(), "markdown_indented_code"))
    return spans


def _find_markdown_blockquote_spans(text: str) -> list[ProtectedSpan]:
    """Keep Markdown blockquote lines editable only by explicit manual review."""

    spans: list[ProtectedSpan] = []
    for match in re.finditer(r".*?(?:\r\n|\n|\r|$)", text):
        if match.start() == match.end():
            continue
        line = match.group(0).rstrip("\r\n")
        if re.match(r"^ {0,3}>(?:\s|$)", line):
            spans.append(
                ProtectedSpan(
                    match.start(),
                    match.end(),
                    "markdown_blockquote",
                    exclude_from_analysis=False,
                    exclude_from_edit=True,
                )
            )
    return spans


def _find_inline_code_spans(text: str, existing: Sequence[ProtectedSpan]) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "`" or _span_at(existing, index, index + 1, for_edit=False):
            index += 1
            continue
        run_end = index
        while run_end < length and text[run_end] == "`":
            run_end += 1
        token = text[index:run_end]
        close = text.find(token, run_end)
        if close == -1:
            index = run_end
            continue
        candidate_end = close + len(token)
        if "\n\n" not in text[run_end:close]:
            spans.append(ProtectedSpan(index, candidate_end, "markdown_inline_code"))
            index = candidate_end
        else:
            index = run_end
    return spans


def _find_latex_verbatim_spans(text: str) -> list[ProtectedSpan]:
    names = r"verbatim|verbatim\*|Verbatim|Verbatim\*|lstlisting|lstlisting\*|minted|minted\*|alltt"
    pattern = re.compile(
        rf"\\begin\{{(?P<name>{names})\}}.*?\\end\{{(?P=name)\}}",
        re.DOTALL,
    )
    return [ProtectedSpan(match.start(), match.end(), "latex_verbatim") for match in pattern.finditer(text)]


def _find_quote_spans(text: str, *, allow_edit: bool = False) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    patterns = [
        re.compile(r"“[^”\r\n]*”"),
        re.compile(r"‘[^’\r\n]*’"),
        re.compile(r'(?<!\\)"(?:\\.|[^"\r\n]){2,}(?<!\\)"'),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            spans.append(
                ProtectedSpan(
                    match.start(),
                    match.end(),
                    "quoted_text",
                    exclude_from_analysis=False,
                    exclude_from_edit=not allow_edit,
                )
            )
    return spans


def _collect_protected_spans(
    text: str,
    format_name: str,
    profile: AuthorProfile | None,
    *,
    edit_quotes: bool = False,
) -> list[ProtectedSpan]:
    spans: list[ProtectedSpan] = []
    if format_name == "markdown":
        fenced = _find_fenced_code_spans(text)
        spans.extend(fenced)
        spans.extend(_find_markdown_indented_code_spans(text, fenced))
        spans.extend(_find_markdown_blockquote_spans(text))
        spans.extend(_find_inline_code_spans(text, fenced))
        markdown_link = re.compile(r"!?\[[^\]\r\n]*\]\((?:\\.|[^)\r\n])*\)")
        spans.extend(ProtectedSpan(m.start(), m.end(), "markdown_link") for m in markdown_link.finditer(text))
        reference_link = re.compile(r"!?\[[^\]\r\n]+\]\[[^\]\r\n]*\]")
        spans.extend(ProtectedSpan(m.start(), m.end(), "markdown_reference_link") for m in reference_link.finditer(text))
        footnote = re.compile(r"\[\^[^\]\r\n]+\]")
        spans.extend(ProtectedSpan(m.start(), m.end(), "markdown_footnote") for m in footnote.finditer(text))
    if format_name == "html":
        html_code = re.compile(r"(?is)<(?P<tag>pre|code)\b[^>]*>.*?</(?P=tag)\s*>")
        spans.extend(ProtectedSpan(m.start(), m.end(), "html_code") for m in html_code.finditer(text))
        html_tag = re.compile(r"(?s)<!--.*?-->|<![^>]*>|<[^>]+>")
        spans.extend(ProtectedSpan(m.start(), m.end(), "html_markup") for m in html_tag.finditer(text))
    if format_name == "latex":
        spans.extend(_find_latex_verbatim_spans(text))

    spans.extend(ProtectedSpan(m.start(), m.end(), "url") for m in URL_RE.finditer(text))
    spans.extend(ProtectedSpan(m.start(), m.end(), "email") for m in EMAIL_RE.finditer(text))
    spans.extend(ProtectedSpan(m.start(), m.end(), "doi") for m in DOI_RE.finditer(text))
    autolink = re.compile(r"<(?:(?:https?://|mailto:)[^>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})>", re.I)
    spans.extend(ProtectedSpan(m.start(), m.end(), "autolink") for m in autolink.finditer(text))

    numeric_citation = re.compile(r"\[(?:\d+(?:\s*[-–,]\s*\d+)*)\]")
    spans.extend(
        ProtectedSpan(m.start(), m.end(), "numeric_citation", exclude_from_analysis=False, exclude_from_edit=True)
        for m in numeric_citation.finditer(text)
    )
    author_date = re.compile(r"\((?:[A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?(?:\s*&\s*[A-Z][A-Za-z'’-]+)?),?\s+\d{4}[a-z]?\)")
    spans.extend(
        ProtectedSpan(m.start(), m.end(), "author_date_citation", exclude_from_analysis=False, exclude_from_edit=True)
        for m in author_date.finditer(text)
    )
    spans.extend(_find_quote_spans(text, allow_edit=edit_quotes))

    json_value = re.compile(r'(?m)^\s*"(?:\\.|[^"\r\n])+"\s*:\s*(?P<value>"(?:\\.|[^"\r\n])*")')
    for match in json_value.finditer(text):
        spans.append(ProtectedSpan(match.start("value"), match.end("value"), "json_string"))

    if profile:
        for index, pattern_text in enumerate(profile.protected_patterns, start=1):
            pattern = re.compile(pattern_text)
            spans.extend(
                ProtectedSpan(match.start(), match.end(), f"profile_pattern_{index}")
                for match in pattern.finditer(text)
            )
    return _merge_protected_spans(spans)


def _slug(value: str) -> str:
    normalized = value.lower().replace("’", "'")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return normalized[:56] or "rule"


def _literal_pattern(phrase: str) -> str:
    escaped = re.escape(phrase.strip())
    escaped = escaped.replace(r"\ ", r"\s+")
    escaped = escaped.replace("'", "(?:'|’)")
    prefix = r"(?<!\w)" if phrase and (phrase[0].isalnum() or phrase[0] in "'’") else ""
    suffix = r"(?!\w)" if phrase and (phrase[-1].isalnum() or phrase[-1] in "'’") else ""
    return prefix + escaped + suffix


def _make_rule(
    rule_id: str,
    category: str,
    description: str,
    evidence_class: str,
    signal_weight: float,
    false_positive_risk: str,
    action: str,
    pattern: str | None,
    replacement_strategy: str = "flag_only",
    enabled_by_default: bool = True,
    suggested_replacements: Sequence[str] = (),
    min_occurrences: int = 1,
    confidence: str = "medium",
    flags: int = re.IGNORECASE,
    notes: str = "",
) -> StyleRule:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"Invalid evidence class for {rule_id}: {evidence_class}")
    if false_positive_risk not in RISK_LEVELS:
        raise ValueError(f"Invalid risk for {rule_id}: {false_positive_risk}")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"Invalid confidence for {rule_id}: {confidence}")
    return StyleRule(
        rule_id=rule_id,
        category=category,
        description=description,
        evidence_class=evidence_class,
        signal_weight=signal_weight,
        false_positive_risk=false_positive_risk,
        action=action,
        pattern=pattern,
        replacement_strategy=replacement_strategy,
        enabled_by_default=enabled_by_default,
        suggested_replacements=tuple(suggested_replacements),
        min_occurrences=min_occurrences,
        confidence=confidence,
        flags=flags,
        notes=notes,
    )


def _build_rules() -> tuple[StyleRule, ...]:
    rules: list[StyleRule] = []
    used_ids: set[str] = set()

    def add(rule: StyleRule) -> None:
        if rule.rule_id in used_ids:
            raise RuntimeError(f"Duplicate rule id: {rule.rule_id}")
        used_ids.add(rule.rule_id)
        rules.append(rule)

    def add_literals(
        prefix: str,
        category: str,
        entries: Sequence[str | tuple[str, Sequence[str]]],
        *,
        description: str,
        evidence: str,
        weight: float,
        risk: str,
        action: str = "suggest",
        strategy: str = "literal_suggestion",
        min_occurrences: int = 1,
        confidence: str = "medium",
        enabled: bool = True,
    ) -> None:
        for index, entry in enumerate(entries, start=1):
            if isinstance(entry, tuple):
                phrase, replacements = entry
            else:
                phrase, replacements = entry, ()
            add(
                _make_rule(
                    f"{prefix}_{index:03d}_{_slug(phrase)}",
                    category,
                    f"{description}: {phrase!r}.",
                    evidence,
                    weight,
                    risk,
                    action,
                    _literal_pattern(phrase),
                    strategy,
                    enabled,
                    replacements,
                    min_occurrences,
                    confidence,
                )
            )

    # Unicode and mechanical sanitation. Joiners and directional controls are
    # intentionally flag-only because they may be linguistically meaningful.
    add(_make_rule("UNICODE_ZWSP", "unicode_sanitation", "Zero-width space.", "safety-sanitation", 1.0, "low", "safe_replace", "\u200b", "delete", True, ("",), confidence="high"))
    add(_make_rule("UNICODE_BOM", "unicode_sanitation", "Byte-order mark inside text.", "safety-sanitation", 1.0, "low", "safe_replace", "\ufeff", "delete", True, ("",), confidence="high"))
    add(_make_rule("UNICODE_SOFT_HYPHEN", "unicode_sanitation", "Invisible soft hyphen.", "safety-sanitation", 1.0, "low", "safe_replace", "\u00ad", "delete", True, ("",), confidence="high"))
    add(_make_rule("UNICODE_NBSP", "unicode_sanitation", "Non-breaking space in ordinary prose.", "safety-sanitation", 0.5, "medium", "safe_replace", "\u00a0", "literal_suggestion", True, (" ",), confidence="high"))
    add(_make_rule("UNICODE_NNBSP", "unicode_sanitation", "Narrow non-breaking space.", "safety-sanitation", 0.5, "medium", "suggest", "\u202f", "literal_suggestion", True, (" ",), confidence="medium"))
    add(_make_rule("UNICODE_WORD_JOINER", "unicode_sanitation", "Invisible word joiner.", "safety-sanitation", 1.0, "high", "flag", "\u2060", "flag_only", True, (), confidence="medium"))
    add(_make_rule("UNICODE_ZWNJ", "unicode_sanitation", "Zero-width non-joiner; may be meaningful in some scripts.", "safety-sanitation", 1.0, "high", "flag", "\u200c", "flag_only", True, (), confidence="medium"))
    add(_make_rule("UNICODE_ZWJ", "unicode_sanitation", "Zero-width joiner; may be meaningful in some scripts or emoji.", "safety-sanitation", 1.0, "high", "flag", "\u200d", "flag_only", True, (), confidence="medium"))
    add(_make_rule("UNICODE_BIDI_CONTROL", "unicode_sanitation", "Bidirectional-formatting control character.", "safety-sanitation", 2.0, "high", "flag", r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "flag_only", True, (), confidence="high"))
    add(_make_rule("MECH_REPEATED_SPACES", "mechanical_cleanup", "Repeated internal horizontal whitespace outside protected spans.", "house-style-heuristic", 0.5, "low", "safe_replace", r"(?<=\S)[^\S\r\n]{2,}(?=\S)", "collapse_space", True, (" ",), confidence="high"))
    add(_make_rule("MECH_REPEATED_EXCLAMATION", "mechanical_cleanup", "Repeated exclamation marks.", "house-style-heuristic", 0.5, "low", "safe_replace", r"!{2,}", "collapse_punctuation", True, ("!",), confidence="high"))
    add(_make_rule("MECH_REPEATED_QUESTION", "mechanical_cleanup", "Repeated question marks.", "house-style-heuristic", 0.5, "low", "safe_replace", r"\?{2,}", "collapse_punctuation", True, ("?",), confidence="high"))

    # Safe prefix removals and redundant modality.
    safe_prefixes = [
        "It is important to note that",
        "It is worth noting that",
        "It should be noted that",
    ]
    for index, phrase in enumerate(safe_prefixes, start=1):
        add(
            _make_rule(
                f"SAFE_PREFIX_{index:02d}_{_slug(phrase)}",
                "vague_importance",
                f"Redundant importance framing: {phrase!r}.",
                "corroborated-heuristic",
                2.5,
                "low",
                "safe_replace",
                _literal_pattern(phrase) + r"\s+(?P<next>[a-z])",
                "delete_prefix_capitalize",
                True,
                (),
                1,
                "high",
            )
        )
    for modal in ("may", "could", "can"):
        add(
            _make_rule(
                f"SAFE_REDUNDANT_MODAL_{modal.upper()}",
                "epistemic_hedging",
                f"Redundant modal phrase: {modal} potentially.",
                "house-style-heuristic",
                1.5,
                "low",
                "safe_replace",
                rf"\b{modal}\s+potentially\b",
                "modal_redundancy",
                True,
                (modal,),
                1,
                "high",
            )
        )

    # Negative and contrastive parallelism. These are high-priority editorial
    # markers when clustered, but remain suggest-only because contrast may be real.
    negative_patterns = [
        ("NEG_NOT_JUST_BUT", r"\bnot\s+just\s+(?P<x>[^,;.!?\n]{1,100}?)[,;]?\s+but(?:\s+also)?\s+(?P<y>[^.!?\n]{1,120})", "not_just"),
        ("NEG_NOT_ONLY_BUT_ALSO", r"\bnot\s+only\s+(?P<x>[^,;.!?\n]{1,100}?)[,;]?\s+but\s+also\s+(?P<y>[^.!?\n]{1,120})", "not_only"),
        ("NEG_NOT_BECAUSE_BUT", r"\bnot\s+because\s+(?P<x>[^,;.!?\n]{1,100}?)[,;]?\s+but\s+because\s+(?P<y>[^.!?\n]{1,120})", "not_because"),
        ("NEG_ITS_NOT_ITS", r"\b(?:it|this)\s+(?:is|was)(?:n't|\s+not)\s+(?P<x>[^,;.!?\n]{1,90}?)[,;]\s*(?:but\s+)?(?:it\s+)?(?:is|was)\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_NOT_MERELY", r"\b(?:it|this|\w+)\s+(?:is|was)(?:n't|\s+not)\s+(?:merely|simply)\s+(?P<x>[^,;.!?\n]{1,90}?)[,;]\s*(?:it\s+)?(?:is|was)\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_ISSUE_NOT_ISSUE", r"\bthe\s+issue\s+(?:is|was)\s+not\s+(?P<x>[^.!?\n]{1,100})[.!?]\s*the\s+issue\s+(?:is|was)\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_WHAT_MATTERS", r"\bwhat\s+matters\s+(?:is|was)(?:n't|\s+not)\s+(?P<x>[^,;.!?\n]{1,100})[,;]\s*(?:it(?:'s|\s+is)\s+)?(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_QUESTION_NOT_WHETHER", r"\bthe\s+question\s+(?:is|was)(?:n't|\s+not)\s+whether\s+(?P<x>[^,;.!?\n]{1,100})[,;]\s*but\s+(?P<y>(?:how|why|when|where|what)\s+[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_LESS_MORE", r"\bless\s+about\s+(?P<x>[^,;.!?\n]{1,100})\s+and\s+more\s+about\s+(?P<y>[^.!?\n]{1,120})", "less_more"),
        ("NEG_MORE_THAN_JUST", r"\bmore\s+than\s+just\s+(?P<x>[^,;.!?\n]{1,100})[,;]\s*(?:it\s+)?(?:is|was)\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_BEYOND_REPRESENTS", r"\bbeyond\s+(?P<x>[^,;.!?\n]{1,100})[,;]\s*(?:it|this)\s+represents\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_NO_NO_JUST", r"\bno\s+(?P<x>[^.!?\n]{1,60})[.!]\s*no\s+(?P<y>[^.!?\n]{1,60})[.!]\s*just\s+(?P<z>[^.!?\n]{1,100})", "no_no_just"),
        ("NEG_NEVER_ALWAYS", r"\b(?:it|this|that)\s+(?:is|was)\s+never\s+(?P<x>[^.!?\n]{1,100})[.!]\s*(?:it|this|that)\s+(?:is|was)\s+always\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_NOT_SIMPLY_FUNDAMENTALLY", r"\b(?P<subject>[^.!?\n]{1,50})\s+does\s+not\s+simply\s+(?P<x>[^,;.!?\n]{1,100})[,;]\s*(?:it\s+)?fundamentally\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_STOPS_BEGINS", r"\bthis\s+is\s+where\s+(?P<x>[^.!?\n]{1,100})\s+stops\s+and\s+(?P<y>[^.!?\n]{1,100})\s+begins\b", "stops_begins"),
        ("NEG_NO_LONGER_BECOME", r"\b(?P<subject>[^.!?\n]{1,50})\s+is\s+no\s+longer\s+(?:merely\s+)?(?P<x>[^.!?\n]{1,100})[.!]\s*(?:it|this|that)\s+has\s+become\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
        ("NEG_NOT_X_PERIOD_Y", r"\b(?:it|this)\s+(?:is|was)(?:n't|\s+not)\s+(?P<x>[^.!?\n]{1,90})[.!]\s*(?:it|this)\s+(?:is|was)\s+(?P<y>[^.!?\n]{1,120})", "not_x_but_y"),
    ]
    for rule_id, pattern, strategy in negative_patterns:
        add(_make_rule(rule_id, "negative_parallelism", "Formulaic negative or contrastive parallelism.", "corroborated-heuristic", 4.5, "medium", "suggest", pattern, strategy, True, (), 1, "high"))

    discourse = [
        ("At its core", ("",)), ("At the heart of", ("In",)), ("In essence", ("",)),
        ("Fundamentally", ("",)), ("Ultimately", ("",)), ("In conclusion", ("",)),
        ("To conclude", ("",)), ("To summarize", ("",)), ("In summary", ("",)),
        ("Taken together", ("",)), ("All things considered", ("",)),
        ("With that in mind", ("",)), ("Against this backdrop", ("",)),
        ("In this context", ("",)), ("Viewed through this lens", ("",)),
        ("From this perspective", ("",)), ("This brings us to", ("Next, consider",)),
        ("This leads to", ("This causes", "This supports")), ("The key takeaway", ("The main point",)),
        ("The bottom line", ("The conclusion",)), ("The central point", ("The point",)),
        ("The broader implication", ("The implication",)), ("The answer lies in", ("The answer is",)),
        ("Here is where", ("Here",)), ("To understand", ("Consider",)),
        ("Before examining", ("Before",)), ("There are several factors to consider", ("Several factors affect this",)),
        ("The following sections explore", ("The sections cover",)), ("Let us examine", ("Consider",)),
        ("Let's unpack", ("Consider",)), ("Let's delve into", ("Let's examine",)),
        ("Let's break this down", ("The parts are",)),
    ]
    add_literals("DISC", "discourse_signposting", discourse, description="Formulaic discourse signpost", evidence="corroborated-heuristic", weight=2.0, risk="medium", min_occurrences=1, confidence="medium")

    this_verbs = [
        "This highlights", "This underscores", "This demonstrates", "This illustrates", "This reveals",
        "This reflects", "This suggests", "This indicates", "This reinforces", "This emphasizes",
        "This showcases", "This ensures", "This enables", "This allows", "This positions",
        "This represents", "This serves as", "This speaks to", "This points to",
    ]
    add_literals("THIS", "this_evaluative", this_verbs, description="Vague or repetitive sentence-initial evaluative construction", evidence="corroborated-heuristic", weight=2.5, risk="medium", min_occurrences=2, confidence="medium")

    research_lexicon: list[tuple[str, Sequence[str]]] = [
        ("camaraderie", ("rapport", "cooperation")),
        ("tapestry", ("mix", "pattern")),
        ("intricate", ("complex", "detailed")),
        ("palpable", ("clear", "noticeable")),
        ("amidst", ("among", "during")),
        ("underscore", ("show", "emphasize")),
        ("underscores", ("shows", "emphasizes")),
        ("underscoring", ("showing", "emphasizing")),
        ("unspoken", ("implicit", "unstated")),
        ("solace", ("comfort",)),
        ("fleeting", ("brief", "short-lived")),
        ("unravel", ("explain", "trace")),
        ("cacophony", ("noise", "conflict")),
        ("grapple", ("address", "deal with")),
        ("ignite", ("start", "prompt")),
        ("vibrant", ("active", "varied")),
        ("pivotal", ("important", "decisive")),
        ("crucial", ("important", "necessary")),
        ("delve", ("examine", "analyze")),
        ("delves", ("examines", "analyzes")),
        ("delving", ("examining", "analyzing")),
        ("showcase", ("show",)),
        ("showcases", ("shows",)),
        ("showcasing", ("showing",)),
        ("notably", ("",)),
        ("potential", ("possible",)),
        ("findings", ("results",)),
    ]
    for index, (phrase, replacements) in enumerate(research_lexicon, start=1):
        min_count = 1
        risk = "medium"
        weight = 4.0
        confidence = "high"
        if phrase in {"potential", "findings", "notably", "crucial", "pivotal"}:
            min_count = 2 if phrase in {"crucial", "pivotal", "notably"} else 3
            risk = "high"
            weight = 2.5
            confidence = "medium"
        add(_make_rule(f"RESEARCH_LEX_{index:03d}_{_slug(phrase)}", "research_lexicon", f"Context-sensitive lexical item reported as overrepresented in one or more LLM-output corpora: {phrase!r}.", "research-supported", weight, risk, "suggest", _literal_pattern(phrase), "literal_suggestion", True, replacements, min_count, confidence, notes="Occurrence is not authorship evidence; genre and topic govern interpretation."))

    grandiose = [
        ("rich tapestry", ("mix", "set")), ("intricate tapestry", ("complex pattern",)),
        ("complex interplay", ("interaction",)), ("delicate balance", ("trade-off", "balance")),
        ("broader landscape", ("field", "context")), ("evolving landscape", ("changing field",)),
        ("dynamic ecosystem", ("system", "network")), ("interconnected ecosystem", ("network",)),
        ("vibrant mosaic", ("varied set",)), ("symphony of", ("combination of",)),
        ("beacon of", ("example of",)), ("cornerstone of", ("basis of",)),
        ("testament to", ("evidence of",)), ("catalyst for change", ("cause of change",)),
        ("bridge between", ("link between",)), ("journey toward", ("progress toward",)),
        ("path forward", ("next step",)), ("realm of possibilities", ("options",)),
        ("unlock the potential", ("enable", "use")), ("navigate the complexities", ("handle the complexity",)),
        ("weave together", ("combine",)), ("multifaceted challenge", ("complex problem",)),
        ("profound transformation", ("major change",)), ("paradigm shift", ("change in approach",)),
        ("ripple effect", ("secondary effect",)),
    ]
    add_literals("META", "grandiose_metaphor", grandiose, description="Grandiose or generic relational metaphor", evidence="corroborated-heuristic", weight=3.0, risk="medium", min_occurrences=1, confidence="medium")

    participial = [
        "By leveraging", "By embracing", "By understanding", "By recognizing", "By adopting",
        "By integrating", "By prioritizing", "By fostering", "By navigating", "Drawing on",
        "Building on", "Recognizing the importance of", "Considering the broader context",
        "Taking these factors into account", "Having established",
    ]
    add_literals("PART", "participial_framing", participial, description="Formulaic participial or prepositional sentence framing", evidence="research-supported", weight=2.0, risk="medium", min_occurrences=2, confidence="medium")

    nominalizations = [
        "implementation", "utilization", "facilitation", "optimization", "integration", "enhancement",
        "consideration", "evaluation", "assessment", "development", "establishment", "identification",
        "recognition", "exploration", "transformation", "prioritization", "alignment", "enablement",
        "operationalization", "contextualization", "democratization", "personalization",
    ]
    add_literals("NOM", "nominalization", nominalizations, description="Abstract action noun that may contribute to noun-heavy prose when clustered", evidence="research-supported", weight=1.0, risk="high", min_occurrences=3, confidence="low")

    vague_importance = [
        "It is essential to understand", "It is crucial to recognize", "One must consider",
        "We must acknowledge", "A critical consideration is", "A key aspect is",
        "An important distinction is", "A notable feature is", "A significant factor is",
        "This cannot be overstated", "The importance of", "It bears mentioning",
    ]
    add_literals("IMP", "vague_importance", vague_importance, description="Vague importance or evaluation framing", evidence="corroborated-heuristic", weight=2.5, risk="medium", min_occurrences=1, confidence="medium")

    epistemic = [
        "appears to suggest", "seems to indicate", "may be seen as", "can be understood as",
        "in many cases", "to some extent", "generally speaking", "broadly speaking",
        "depending on the context", "under certain circumstances", "it is possible that",
        "there is reason to believe", "evidence may suggest",
    ]
    add_literals("HEDGE", "epistemic_hedging", epistemic, description="Generic or stacked epistemic hedge", evidence="corroborated-heuristic", weight=1.5, risk="high", min_occurrences=2, confidence="medium")
    unsupported = ["research indicates", "experts suggest", "studies have shown", "research suggests", "evidence shows"]
    add_literals("SOURCE", "unsupported_sourcing", unsupported, description="Potentially unsupported source formula", evidence="corroborated-heuristic", weight=3.0, risk="medium", min_occurrences=1, confidence="medium")

    artificial_balance = [
        "On the one hand", "On the other hand", "offers benefits, it also presents challenges",
        "is valuable, it is not without limitations", "presents both opportunities and challenges",
        "The answer depends on several factors", "There is no simple answer",
        "There is no one-size-fits-all solution", "Both perspectives have merit",
        "The truth likely lies somewhere in between", "A balanced approach is essential",
        "Balance is key", "Context matters",
    ]
    add_literals("BAL", "artificial_balance", artificial_balance, description="Generic balance or trade-off framing", evidence="corroborated-heuristic", weight=2.5, risk="high", min_occurrences=1, confidence="medium")

    corporate = [
        "rapidly evolving landscape", "ever-changing landscape", "today's digital landscape",
        "increasingly interconnected world", "fast-paced environment", "competitive marketplace",
        "at the forefront", "cutting-edge", "best-in-class", "future-proof", "scalable solution",
        "robust framework", "seamless integration", "holistic approach", "comprehensive solution",
        "tailored solution", "innovative strategy", "transformative potential", "meaningful impact",
        "sustainable growth", "drive engagement", "drive outcomes", "drive innovation", "unlock value",
        "maximize potential", "empower users", "elevate the experience", "foster collaboration",
        "facilitate communication", "streamline operations", "optimize performance", "leverage technology",
        "harness the power of", "redefine what is possible",
    ]
    add_literals("CORP", "corporate_abstraction", corporate, description="Corporate or promotional abstraction", evidence="house-style-heuristic", weight=2.0, risk="medium", min_occurrences=1, confidence="medium")

    soft_intensifiers = [
        "deeply", "truly", "profoundly", "incredibly", "remarkably", "particularly", "notably",
        "increasingly", "fundamentally", "meaningfully", "thoughtfully", "intentionally",
        "strategically", "effectively", "seamlessly", "uniquely", "distinctly", "quietly",
    ]
    add_literals("INT", "soft_intensifier", soft_intensifiers, description="Soft intensifier that becomes conspicuous when clustered", evidence="corroborated-heuristic", weight=0.75, risk="high", min_occurrences=3, confidence="low")
    polished_pairs = [
        "quietly powerful", "deeply transformative", "profoundly important", "truly meaningful",
        "remarkably nuanced", "increasingly vital", "fundamentally reshapes", "thoughtfully designed",
        "intentionally crafted", "uniquely positioned",
    ]
    add_literals("PAIR_INT", "soft_intensifier", polished_pairs, description="Polished-vagueness pairing", evidence="corroborated-heuristic", weight=2.5, risk="medium", min_occurrences=1, confidence="medium")

    hollow = [
        "valuable insights", "meaningful insights", "actionable insights", "valuable perspective",
        "unique perspective", "critical role", "pivotal role", "significant impact", "profound impact",
        "lasting impact", "meaningful change", "positive change", "broader implications",
        "key considerations", "important considerations", "complex challenges", "unique challenges",
        "exciting opportunities", "endless possibilities", "powerful tool", "compelling case",
        "nuanced understanding", "deeper understanding",
    ]
    add_literals("HOLLOW", "hollow_pairing", hollow, description="Evaluative pairing that may be semantically thin", evidence="corroborated-heuristic", weight=2.0, risk="medium", min_occurrences=1, confidence="medium")

    assistant_residue = [
        "That's a great question", "Excellent question", "You're absolutely right",
        "You've identified an important issue", "I completely understand", "It's understandable to feel",
        "I hear your concern", "I'm glad you asked", "Certainly", "Absolutely", "I'd be happy to",
        "Let's work through this", "Let's break it down", "Don't worry", "You've got this",
        "Remember, you're not alone", "I hope this helps", "Feel free to ask", "Please let me know",
        "Happy to help", "Here to help",
    ]
    add_literals("ASST", "assistant_residue", assistant_residue, description="Assistant-interaction residue in reusable copy", evidence="house-style-heuristic", weight=2.0, risk="medium", min_occurrences=1, confidence="medium")

    generic_audience = [
        "Whether you are a beginner or an expert", "or somewhere in between", "From students to professionals",
        "For individuals and organizations alike", "Regardless of your background",
        "No matter where you are on your journey", "Everyone's journey is different",
        "Every situation is unique", "Results may vary", "The right choice depends on your needs",
        "Consider your individual circumstances", "Consult a qualified professional",
        "Use this information responsibly",
    ]
    add_literals("AUD", "generic_audience", generic_audience, description="Generic audience or universalizing construction", evidence="house-style-heuristic", weight=1.5, risk="high", min_occurrences=1, confidence="low")

    generic_examples = [
        "Imagine a world where", "Picture this", "Consider a scenario in which", "Suppose you are",
        "For example, imagine", "Take the case of", "Think of it as", "A useful analogy is",
        "To illustrate", "In practical terms", "In real-world applications",
    ]
    add_literals("EX", "generic_example", generic_examples, description="Generic example or scenario framing", evidence="house-style-heuristic", weight=1.5, risk="medium", min_occurrences=1, confidence="medium")

    controlled_revelation = [
        "But there is a catch", "The real issue is", "The deeper problem is", "The surprising part is",
        "That is where things get interesting", "Here's why", "Here's how", "Here's what matters",
        "Here's the thing",
    ]
    add_literals("REVEAL", "controlled_revelation", controlled_revelation, description="Controlled-revelation or dramatic-pivot formula", evidence="corroborated-heuristic", weight=2.0, risk="medium", min_occurrences=2, confidence="medium")

    # Structural and density analyzers live in the same registry so list-rules,
    # evidence metadata, weighting, and reporting remain coherent.
    em_dash_specs = [
        ("EMDASH_PAIRED_PARENTHETICAL", "Paired parenthetical em dash.", 1.0, "medium", "high"),
        ("EMDASH_INDEPENDENT_CLAUSES", "Em dash joining two plausible independent clauses.", 1.0, "medium", "medium"),
        ("EMDASH_APPOSITIONAL", "Appositional or explanatory em dash.", 1.0, "medium", "medium"),
        ("EMDASH_INTERRUPTION", "Abrupt interruption or self-correction em dash.", 0.5, "high", "high"),
        ("EMDASH_LIST_INTRODUCTION", "Em dash introducing a list or summary.", 1.0, "medium", "high"),
        ("EMDASH_ATTRIBUTION", "Attribution or signature em dash.", 0.25, "high", "high"),
        ("EMDASH_NUMERIC_RANGE", "Em dash used between numeric endpoints.", 1.0, "low", "high"),
        ("EMDASH_AMBIGUOUS", "Unclassified em dash requiring review.", 0.5, "high", "low"),
    ]
    for rule_id, description, weight, risk, confidence in em_dash_specs:
        add(_make_rule(rule_id, "punctuation", description, "house-style-heuristic", weight, risk, "suggest", None, "em_dash", True, (), 1, confidence))

    structural_specs = [
        ("STRUCT_UNIFORM_SENTENCE_LENGTH", "structural_repetition", "Unusually uniform sentence lengths.", "research-supported", 4.0, "medium", "medium"),
        ("STRUCT_UNIFORM_PARAGRAPH_LENGTH", "structural_repetition", "Unusually uniform paragraph lengths.", "corroborated-heuristic", 3.0, "medium", "medium"),
        ("STRUCT_REPEATED_SENTENCE_OPENING", "structural_repetition", "Repeated sentence openings.", "corroborated-heuristic", 4.0, "medium", "high"),
        ("STRUCT_EXCESSIVE_HEADINGS", "formatting", "Heading density is high for the document length.", "house-style-heuristic", 2.0, "medium", "medium"),
        ("STRUCT_THREE_ITEM_BLOCKS", "formulaic_architecture", "Repeated three-item list architecture.", "corroborated-heuristic", 3.0, "medium", "medium"),
        ("STRUCT_BOLD_LABEL_BULLETS", "formatting", "Repetitive bold-label-plus-colon bullets.", "house-style-heuristic", 2.0, "medium", "high"),
        ("STRUCT_DRAMATIC_PIVOT_PARAGRAPHS", "formulaic_architecture", "Repeated short dramatic-pivot paragraphs.", "corroborated-heuristic", 3.0, "medium", "medium"),
        ("STRUCT_QUESTION_FRAGMENTS", "formulaic_architecture", "Repeated rhetorical question fragments.", "corroborated-heuristic", 3.0, "medium", "high"),
        ("STRUCT_HERES_REPETITION", "formulaic_architecture", "Repeated 'here is/here's' revelation openings.", "corroborated-heuristic", 3.0, "medium", "high"),
        ("STRUCT_CONTROLLED_REVELATION", "formulaic_architecture", "Repeated controlled-revelation phrases.", "corroborated-heuristic", 3.0, "medium", "medium"),
        ("STRUCT_INITIAL_CONJUNCTIONS", "structural_repetition", "High density of sentence-initial conjunctions.", "house-style-heuristic", 2.0, "medium", "low"),
        ("STRUCT_PARTICIPIAL_OPENINGS", "structural_repetition", "High density of participial sentence openings.", "research-supported", 3.0, "medium", "medium"),
        ("STRUCT_NOMINALIZATION_DENSITY", "nominalization", "High abstract-noun density.", "research-supported", 4.0, "medium", "high"),
        ("STRUCT_COLON_DENSITY", "formatting", "High colon density.", "house-style-heuristic", 1.5, "medium", "low"),
        ("STRUCT_EM_DASH_DENSITY", "punctuation", "High em-dash density.", "house-style-heuristic", 1.0, "high", "medium"),
        ("STRUCT_BULLET_UNIFORMITY", "structural_repetition", "Bullet lengths and openings are unusually uniform.", "corroborated-heuristic", 3.0, "medium", "medium"),
        ("STRUCT_INTRO_CONCLUSION_OVERLAP", "formulaic_architecture", "Conclusion closely restates the introduction.", "corroborated-heuristic", 4.0, "medium", "medium"),
        ("STRUCT_PARAGRAPH_ENDING_FORMULA", "formulaic_architecture", "Repeated paragraph-final implication formula.", "corroborated-heuristic", 3.0, "medium", "medium"),
        ("STRUCT_GENERIC_EXAMPLE_WITHOUT_DETAIL", "generic_example", "Generic example lacks names, quantities, mechanisms, or constraints.", "house-style-heuristic", 2.0, "high", "low"),
        ("STRUCT_UNSUPPORTED_CONSENSUS", "unsupported_sourcing", "Consensus or research claim lacks a nearby citation or named source.", "corroborated-heuristic", 3.0, "medium", "medium"),
    ]
    for rule_id, category, description, evidence, weight, risk, confidence in structural_specs:
        add(_make_rule(rule_id, category, description, evidence, weight, risk, "flag", None, "structural", True, (), 1, confidence))

    return tuple(rules)


RULES: tuple[StyleRule, ...] = _build_rules()
RULES_BY_ID: dict[str, StyleRule] = {rule.rule_id: rule for rule in RULES}
COMPILED_RULES: dict[str, re.Pattern[str]] = {
    rule.rule_id: re.compile(rule.pattern, rule.flags)
    for rule in RULES
    if rule.pattern is not None
}
STRUCTURAL_RULE_IDS = tuple(rule.rule_id for rule in RULES if rule.pattern is None)


def _mask_for_analysis(text: str, spans: Sequence[ProtectedSpan]) -> str:
    chars = list(text)
    for span in spans:
        if not span.exclude_from_analysis:
            continue
        for index in range(span.start, min(span.end, len(chars))):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return conservative sentence spans while preserving source offsets."""

    spans: list[tuple[int, int]] = []
    abbreviations = {
        "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "fig", "eq",
    }
    start: int | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if start is None and not char.isspace():
            start = index
        if start is None:
            index += 1
            continue
        if char in ".!?":
            if char == "." and index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
                index += 1
                continue
            token_match = re.search(r"([A-Za-z](?:[A-Za-z.]*)?)\.$", text[start:index + 1])
            token = token_match.group(1).lower().rstrip(".") if token_match else ""
            if token in abbreviations or (len(token) == 1 and token.isalpha()):
                index += 1
                continue
            next_index = index + 1
            while next_index < len(text) and text[next_index] in "\"'”’)]}":
                next_index += 1
            if next_index >= len(text) or text[next_index].isspace():
                spans.append((start, next_index))
                start = None
                index = next_index
                continue
        if char in "\r\n" and index + 1 < len(text):
            match = re.match(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)", text[index:])
            if match:
                end = index
                if text[start:end].strip():
                    spans.append((start, end))
                start = None
                index += match.end()
                continue
        index += 1
    if start is not None and text[start:].strip():
        spans.append((start, len(text)))
    return [(start, end) for start, end in spans if WORD_RE.search(text[start:end])]


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    separator = re.compile(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)+")
    for match in separator.finditer(text):
        start, end = cursor, match.start()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
        cursor = match.end()
    start, end = cursor, len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((start, end))
    return spans


def _word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _capitalize_first(text: str) -> str:
    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1 :]
    return text


def _strip_terminal(text: str) -> str:
    return text.strip().rstrip(".;:!?").strip()


def _group(match: re.Match[str], name: str) -> str:
    value = match.groupdict().get(name, "") or ""
    return _strip_terminal(value)


def _candidate_replacements(rule: StyleRule, match: re.Match[str]) -> tuple[str, ...]:
    strategy = rule.replacement_strategy
    if strategy == "delete":
        return ("",)
    if strategy in {"collapse_space", "collapse_punctuation", "modal_redundancy", "literal_suggestion"}:
        return rule.suggested_replacements
    if strategy == "delete_prefix_capitalize":
        next_char = match.groupdict().get("next", "")
        return (next_char.upper(),) if next_char else ()
    if strategy in {"not_just", "not_only"}:
        x, y = _group(match, "x"), _group(match, "y")
        return (f"{_capitalize_first(x)} and {y}.",) if x and y else ()
    if strategy == "not_because":
        x, y = _group(match, "x"), _group(match, "y")
        return (f"{_capitalize_first(y)}. { _capitalize_first(x) } is not the reason.",) if x and y else ()
    if strategy == "not_x_but_y":
        x, y = _group(match, "x"), _group(match, "y")
        return (f"{_capitalize_first(y)} rather than {x}.",) if x and y else ()
    if strategy == "less_more":
        x, y = _group(match, "x"), _group(match, "y")
        return (f"The emphasis is {y}, rather than {x}.",) if x and y else ()
    if strategy == "no_no_just":
        x, y, z = _group(match, "x"), _group(match, "y"), _group(match, "z")
        return (f"{_capitalize_first(z)}. Neither {x} nor {y}.",) if x and y and z else ()
    if strategy == "stops_begins":
        x, y = _group(match, "x"), _group(match, "y")
        return (f"{_capitalize_first(y)} begins when {x} ends.",) if x and y else ()
    fallback_by_category = {
        "participial_framing": "Rewrite with a finite verb and name the acting agent where known.",
        "nominalization": "Consider replacing the abstract action noun with a verb and explicit agent.",
        "vague_importance": "State the underlying claim directly and remove the importance announcement.",
        "epistemic_hedging": "Keep only the modality required by the evidence; remove stacked or generic hedging.",
        "unsupported_sourcing": "Name and cite the source, or remove the borrowed-authority formula.",
        "artificial_balance": "State the evidence-weighted conclusion and specify the variables that alter it.",
        "corporate_abstraction": "Replace the abstraction with the concrete mechanism, actor, or outcome.",
        "soft_intensifier": "Delete the intensifier or replace it with a measurable description.",
        "hollow_pairing": "Specify what the insight, impact, role, or change consists of.",
        "assistant_residue": "Remove conversational service language from reusable copy unless the interaction requires it.",
        "generic_audience": "Name the actual audience or remove the universalizing preface.",
        "generic_example": "Use a concrete case with a mechanism, constraint, name, date, or quantity.",
        "controlled_revelation": "State the claim directly instead of staging another reveal.",
        "discourse_signposting": "Delete the announcement or replace it with the specific relation between sentences.",
        "this_evaluative": "Replace vague 'This' with the actual antecedent and the precise relation.",
        "research_lexicon": "Use a plainer alternative only when it fits the genre and preserves the intended meaning.",
        "grandiose_metaphor": "Replace the metaphor with the concrete relationship it is meant to describe.",
    }
    fallback = fallback_by_category.get(rule.category)
    return (fallback,) if fallback and rule.action == "suggest" else rule.suggested_replacements


def _is_sentence_start(text: str, offset: int) -> bool:
    index = offset - 1
    while index >= 0 and text[index] in " \t\"'“‘([{":
        index -= 1
    return index < 0 or text[index] in ".!?\r\n"


def _has_nearby_source(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 100) : min(len(text), end + 240)]
    if URL_RE.search(window) or DOI_RE.search(window):
        return True
    if re.search(r"\[(?:\d+(?:\s*[-–,]\s*\d+)*)\]", window):
        return True
    if re.search(r"\([A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?,?\s+\d{4}[a-z]?\)", window):
        return True
    if re.search(r"\baccording\s+to\s+[A-Z][A-Za-z'’-]+", window):
        return True
    return False


def _suppress_contextual_match(rule: StyleRule, text: str, match: re.Match[str]) -> bool:
    phrase = match.group(0).lower()
    start, end = match.span()
    window = text[max(0, start - 100) : min(len(text), end + 100)].lower()

    if rule.category == "research_lexicon" and "tapestry" in phrase:
        textile_terms = {"textile", "weaving", "woven", "fabric", "loom", "thread", "embroidery", "carpet"}
        if any(term in window for term in textile_terms):
            return True
    if rule.category == "research_lexicon" and phrase.strip() in {"findings", "potential"}:
        # These are high-frequency content words. Only repeated-use thresholds
        # should surface them; no extra single-occurrence interpretation.
        pass
    if rule.category == "unsupported_sourcing" and _has_nearby_source(text, start, end):
        return True
    if rule.category in {"participial_framing", "discourse_signposting", "generic_example", "generic_audience"}:
        if not _is_sentence_start(text, start):
            return True
    if rule.category == "assistant_residue" and phrase.strip() in {"certainly", "absolutely"}:
        if not _is_sentence_start(text, start):
            return True
    return False


def _profile_rule_enabled(rule: StyleRule, config: RefinerConfig) -> bool:
    builtin = BUILTIN_PROFILES[config.profile]
    author = config.author_profile
    if author and rule.rule_id in author.enabled_rules:
        return True
    if author and rule.rule_id in author.disabled_rules:
        return False
    if rule.category in builtin.disabled_categories:
        return False
    return rule.enabled_by_default


def _rule_weight(rule: StyleRule, config: RefinerConfig) -> float:
    multiplier = BUILTIN_PROFILES[config.profile].weight_map().get(rule.category, 1.0)
    if config.author_profile:
        multiplier *= config.author_profile.category_weight_map().get(rule.category, 1.0)
    return round(rule.signal_weight * multiplier, 4)


def _make_finding(
    rule: StyleRule,
    text: str,
    line_map: LineMap,
    start: int,
    end: int,
    matched_text: str,
    candidates: Sequence[str],
    config: RefinerConfig,
    spans: Sequence[ProtectedSpan],
    explanation: str | None = None,
    confidence: str | None = None,
) -> Finding:
    line, column = line_map.line_col(start)
    edit_span = _span_at(spans, start, end, for_edit=True)
    return Finding(
        rule_id=rule.rule_id,
        category=rule.category,
        start=start,
        end=end,
        line=line,
        column=column,
        matched_text=matched_text,
        explanation=explanation or rule.description,
        signal_weight=_rule_weight(rule, config),
        false_positive_risk=rule.false_positive_risk,
        confidence=confidence or rule.confidence,
        candidates=tuple(candidate for candidate in candidates if candidate is not None),
        evidence_class=rule.evidence_class,
        protected=edit_span is not None,
        protected_kind=edit_span.kind if edit_span else None,
    )


def _deduplicate_findings(findings: Sequence[Finding]) -> list[Finding]:
    ordered = sorted(findings, key=lambda item: (item.start, -(item.end - item.start), -item.signal_weight, item.rule_id))
    kept: list[Finding] = []
    exact: dict[tuple[int, int], Finding] = {}
    for finding in ordered:
        key = (finding.start, finding.end)
        previous = exact.get(key)
        if previous is not None:
            if finding.signal_weight > previous.signal_weight:
                kept.remove(previous)
                kept.append(finding)
                exact[key] = finding
            continue
        suppress = False
        for prior in kept[-8:]:
            if prior.category != finding.category or prior.rule_id != finding.rule_id:
                continue
            overlap = max(0, min(prior.end, finding.end) - max(prior.start, finding.start))
            smaller = min(max(1, prior.end - prior.start), max(1, finding.end - finding.start))
            if overlap / smaller >= 0.8:
                suppress = True
                break
        if not suppress:
            kept.append(finding)
            exact[key] = finding
    return sorted(kept, key=lambda item: (item.start, item.end, item.rule_id))


def _lexical_findings(
    text: str,
    spans: Sequence[ProtectedSpan],
    line_map: LineMap,
    config: RefinerConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    min_rank = CONFIDENCE_LEVELS[config.min_confidence]
    for rule in RULES:
        if rule.pattern is None or not _profile_rule_enabled(rule, config):
            continue
        if CONFIDENCE_LEVELS[rule.confidence] < min_rank:
            continue
        pattern = COMPILED_RULES[rule.rule_id]
        matches: list[re.Match[str]] = []
        for match in pattern.finditer(text):
            if _span_at(spans, match.start(), match.end(), for_edit=False):
                continue
            if _suppress_contextual_match(rule, text, match):
                continue
            if (
                config.author_profile
                and rule.category == "discourse_signposting"
                and any(match.group(0).strip().casefold() == item.strip().casefold() for item in config.author_profile.preferred_transitions)
            ):
                continue
            matches.append(match)
        if len(matches) < rule.min_occurrences:
            continue
        for match in matches:
            findings.append(
                _make_finding(
                    rule,
                    text,
                    line_map,
                    match.start(),
                    match.end(),
                    match.group(0),
                    _candidate_replacements(rule, match),
                    config,
                    spans,
                )
            )
    return findings


def _profile_phrase_findings(
    text: str,
    spans: Sequence[ProtectedSpan],
    line_map: LineMap,
    config: RefinerConfig,
) -> list[Finding]:
    profile = config.author_profile
    if profile is None:
        return []
    findings: list[Finding] = []
    groups = [
        ("discouraged", profile.discouraged_phrases, 3.0, "medium"),
        ("forbidden", profile.forbidden_phrases, 5.0, "low"),
    ]
    for label, phrases, weight, risk in groups:
        for index, phrase in enumerate(phrases, start=1):
            pattern = re.compile(_literal_pattern(phrase), re.IGNORECASE)
            for match in pattern.finditer(text):
                if _span_at(spans, match.start(), match.end(), for_edit=False):
                    continue
                rule = _make_rule(
                    f"PROFILE_{label.upper()}_{index:03d}",
                    "author_profile",
                    f"Phrase is {label} by the active author profile: {phrase!r}.",
                    "house-style-heuristic",
                    weight,
                    risk,
                    "suggest",
                    pattern.pattern,
                    "flag_only",
                    True,
                    (),
                    1,
                    "high",
                )
                findings.append(_make_finding(rule, text, line_map, match.start(), match.end(), match.group(0), (), config, spans))
    return findings


CONTRACTION_RE = re.compile(
    r"\b(?:I[’'](?:m|ve|ll|d)|you[’'](?:re|ve|ll|d)|we[’'](?:re|ve|ll|d)|"
    r"they[’'](?:re|ve|ll|d)|he[’'](?:s|ll|d)|she[’'](?:s|ll|d)|it[’'](?:s|ll|d)|"
    r"that[’']s|there[’']s|what[’']s|who[’']s|[A-Za-z]+n[’']t)\b",
    re.IGNORECASE,
)


def _profile_preference_findings(
    text: str,
    masked: str,
    spans: Sequence[ProtectedSpan],
    line_map: LineMap,
    config: RefinerConfig,
) -> list[Finding]:
    """Report measurable divergence from an explicit author profile."""

    profile = config.author_profile
    if profile is None:
        return []

    findings: list[Finding] = []
    sentences = _sentence_spans(masked)
    minimum_words, maximum_words = profile.preferred_sentence_words
    sentence_outliers = [
        (start, end, _word_count(masked[start:end]))
        for start, end in sentences
        if not minimum_words <= _word_count(masked[start:end]) <= maximum_words
    ]
    if len(sentences) >= 4 and len(sentence_outliers) >= 2 and len(sentence_outliers) / len(sentences) >= 0.40:
        start = sentence_outliers[0][0]
        end = sentence_outliers[-1][1]
        rule = _make_rule(
            "PROFILE_SENTENCE_WORD_RANGE",
            "author_profile",
            "Sentence lengths diverge from the explicit author-profile range.",
            "house-style-heuristic",
            3.0,
            "medium",
            "suggest",
            None,
            "profile_preference",
            True,
            (),
            1,
            "high",
        )
        findings.append(
            _make_finding(
                rule, text, line_map, start, end,
                f"{len(sentence_outliers)} of {len(sentences)} sentences fall outside {minimum_words}-{maximum_words} words",
                (f"Revise only where useful toward the configured {minimum_words}-{maximum_words}-word range.",),
                config, spans,
            )
        )

    paragraphs = _paragraph_spans(masked)
    minimum_sentences, maximum_sentences = profile.preferred_paragraph_sentences
    paragraph_counts: list[tuple[int, int, int]] = []
    for start, end in paragraphs:
        count = len(_sentence_spans(masked[start:end]))
        if count:
            paragraph_counts.append((start, end, count))
    paragraph_outliers = [
        item for item in paragraph_counts if not minimum_sentences <= item[2] <= maximum_sentences
    ]
    if len(paragraph_counts) >= 3 and len(paragraph_outliers) >= 2 and len(paragraph_outliers) / len(paragraph_counts) >= 0.50:
        start = paragraph_outliers[0][0]
        end = paragraph_outliers[-1][1]
        rule = _make_rule(
            "PROFILE_PARAGRAPH_SENTENCE_RANGE",
            "author_profile",
            "Paragraph sentence counts diverge from the explicit author-profile range.",
            "house-style-heuristic",
            3.0,
            "medium",
            "suggest",
            None,
            "profile_preference",
            True,
            (),
            1,
            "high",
        )
        findings.append(
            _make_finding(
                rule, text, line_map, start, end,
                f"{len(paragraph_outliers)} of {len(paragraph_counts)} paragraphs fall outside {minimum_sentences}-{maximum_sentences} sentences",
                (
                    f"Adjust paragraph boundaries only where the argument supports the configured "
                    f"{minimum_sentences}-{maximum_sentences}-sentence range.",
                ),
                config, spans,
            )
        )

    if not profile.allow_contractions:
        rule = _make_rule(
            "PROFILE_CONTRACTION_DISALLOWED",
            "author_profile",
            "The active author profile disallows contractions.",
            "house-style-heuristic",
            2.5,
            "medium",
            "suggest",
            CONTRACTION_RE.pattern,
            "profile_preference",
            True,
            (),
            1,
            "high",
        )
        for match in CONTRACTION_RE.finditer(masked):
            if _span_at(spans, match.start(), match.end(), for_edit=False):
                continue
            findings.append(
                _make_finding(
                    rule, text, line_map, match.start(), match.end(), text[match.start():match.end()],
                    ("Expand the contraction after confirming the intended auxiliary and modality.",),
                    config, spans,
                )
            )

    return findings


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "been", "but", "by", "for", "from", "had",
    "has", "have", "he", "her", "his", "i", "in", "is", "it", "its", "of", "on", "or", "our", "she",
    "that", "the", "their", "them", "they", "this", "to", "was", "we", "were", "will", "with", "you", "your",
}
NOMINALIZATION_TERMS = {
    "implementation", "utilization", "facilitation", "optimization", "integration", "enhancement",
    "consideration", "evaluation", "assessment", "development", "establishment", "identification",
    "recognition", "exploration", "transformation", "prioritization", "alignment", "enablement",
    "operationalization", "contextualization", "democratization", "personalization",
}
PARTICIPIAL_OPENERS = {
    "leveraging", "embracing", "understanding", "recognizing", "adopting", "integrating", "prioritizing",
    "fostering", "navigating", "drawing", "building", "considering", "taking", "having",
}
CONTROLLED_REVELATION_PHRASES = (
    "but there is a catch", "the real issue is", "the deeper problem is", "the surprising part is",
    "that is where things get interesting", "here's why", "here is why", "here's how", "here is how",
    "here's what matters", "here is what matters", "here's the thing", "here is the thing",
)
GENERIC_EXAMPLE_OPENERS = (
    "imagine a world where", "picture this", "consider a scenario in which", "suppose you are",
    "for example, imagine", "take the case of", "think of it as", "a useful analogy is", "to illustrate",
    "in practical terms", "in real-world applications",
)


def _content_tokens(text: str) -> list[str]:
    return [token.lower() for token in WORD_RE.findall(text) if token.lower() not in STOPWORDS and len(token) > 2]


def _coefficient_of_variation(values: Sequence[int]) -> float:
    if len(values) < 2:
        return float("inf")
    mean = statistics.fmean(values)
    if mean == 0:
        return float("inf")
    return statistics.pstdev(values) / mean


def _structural_finding(
    rule_id: str,
    text: str,
    line_map: LineMap,
    config: RefinerConfig,
    spans: Sequence[ProtectedSpan],
    start: int,
    end: int,
    matched_text: str,
    candidates: Sequence[str],
    explanation: str | None = None,
    confidence: str | None = None,
) -> Finding | None:
    rule = RULES_BY_ID[rule_id]
    if not _profile_rule_enabled(rule, config):
        return None
    selected_confidence = confidence or rule.confidence
    if CONFIDENCE_LEVELS[selected_confidence] < CONFIDENCE_LEVELS[config.min_confidence]:
        return None
    return _make_finding(
        rule,
        text,
        line_map,
        start,
        end,
        matched_text,
        candidates,
        config,
        spans,
        explanation,
        selected_confidence,
    )


def _line_records(text: str) -> list[tuple[int, int, str]]:
    records: list[tuple[int, int, str]] = []
    for match in re.finditer(r".*?(?:\r\n|\n|\r|$)", text):
        if match.start() == match.end():
            continue
        records.append((match.start(), match.end(), match.group(0).rstrip("\r\n")))
    return records


def _bullet_blocks(text: str) -> list[list[tuple[int, int, str]]]:
    blocks: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    bullet = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$")
    for start, end, line in _line_records(text):
        match = bullet.match(line)
        if match:
            current.append((start, end, match.group(1)))
            continue
        if current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _analyze_structural(
    text: str,
    masked: str,
    spans: Sequence[ProtectedSpan],
    line_map: LineMap,
    config: RefinerConfig,
) -> list[Finding]:
    findings: list[Finding] = []
    sentences = _sentence_spans(masked)
    paragraphs = _paragraph_spans(masked)
    words = max(1, _word_count(masked))

    # 1. Sentence-length uniformity.
    sentence_lengths = [_word_count(masked[start:end]) for start, end in sentences]
    sentence_lengths = [length for length in sentence_lengths if length >= 4]
    if len(sentence_lengths) >= 8:
        cv = _coefficient_of_variation(sentence_lengths)
        mean = statistics.fmean(sentence_lengths)
        if mean >= 8 and cv <= 0.22:
            finding = _structural_finding(
                "STRUCT_UNIFORM_SENTENCE_LENGTH", text, line_map, config, spans,
                sentences[0][0], sentences[-1][1],
                f"{len(sentence_lengths)} sentences; mean={mean:.1f} words; CV={cv:.2f}",
                ("Vary sentence length only where it improves emphasis or comprehension.",),
                f"Sentence lengths are unusually uniform across {len(sentence_lengths)} sentences (CV {cv:.2f}).",
            )
            if finding:
                findings.append(finding)

    # 2. Paragraph-length uniformity.
    paragraph_lengths = [_word_count(masked[start:end]) for start, end in paragraphs if _word_count(masked[start:end]) >= 8]
    if len(paragraph_lengths) >= 5:
        cv = _coefficient_of_variation(paragraph_lengths)
        mean = statistics.fmean(paragraph_lengths)
        if mean >= 20 and cv <= 0.20:
            finding = _structural_finding(
                "STRUCT_UNIFORM_PARAGRAPH_LENGTH", text, line_map, config, spans,
                paragraphs[0][0], paragraphs[-1][1],
                f"{len(paragraph_lengths)} paragraphs; mean={mean:.1f} words; CV={cv:.2f}",
                ("Let paragraph length follow argument structure rather than a fixed template.",),
                f"Paragraph lengths are unusually uniform (CV {cv:.2f}).",
            )
            if finding:
                findings.append(finding)

    # 3. Repeated sentence openings.
    openings: defaultdict[str, list[tuple[int, int]]] = defaultdict(list)
    for start, end in sentences:
        tokens = WORD_RE.findall(masked[start:end])
        if len(tokens) >= 2:
            key = " ".join(token.lower() for token in tokens[:2])
            openings[key].append((start, end))
    for opening, occurrences in sorted(openings.items()):
        if len(occurrences) >= 3:
            start, end = occurrences[0]
            finding = _structural_finding(
                "STRUCT_REPEATED_SENTENCE_OPENING", text, line_map, config, spans,
                start, min(end, start + 80), opening,
                ("Vary the opening syntax or combine sentences that perform the same rhetorical job.",),
                f"The opening {opening!r} appears at the start of {len(occurrences)} sentences.",
            )
            if finding:
                findings.append(finding)

    # 4. Heading density.
    headings = [(start, end, line) for start, end, line in _line_records(masked) if re.match(r"^\s{0,3}#{1,6}\s+\S", line)]
    if len(headings) >= 4 and words / len(headings) < 85:
        finding = _structural_finding(
            "STRUCT_EXCESSIVE_HEADINGS", text, line_map, config, spans,
            headings[0][0], headings[-1][1], f"{len(headings)} headings across {words} words",
            ("Merge headings that divide a short argument into cosmetic sections.",),
        )
        if finding:
            findings.append(finding)

    # 5. Repeated exactly-three-item blocks.
    bullet_blocks = _bullet_blocks(masked)
    three_blocks = [block for block in bullet_blocks if len(block) == 3]
    if len(three_blocks) >= 2:
        finding = _structural_finding(
            "STRUCT_THREE_ITEM_BLOCKS", text, line_map, config, spans,
            three_blocks[0][0][0], three_blocks[-1][-1][1], f"{len(three_blocks)} separate three-item lists",
            ("Use the number of items required by the material, not a repeated three-part template.",),
        )
        if finding:
            findings.append(finding)

    # 6. Bold label plus colon bullets.
    bullets = [item for block in bullet_blocks for item in block]
    bold_label = [item for item in bullets if re.match(r"\*\*[^*\r\n]{1,60}:\*\*", item[2].strip())]
    if len(bullets) >= 4 and len(bold_label) >= 3 and len(bold_label) / len(bullets) >= 0.6:
        finding = _structural_finding(
            "STRUCT_BOLD_LABEL_BULLETS", text, line_map, config, spans,
            bold_label[0][0], bold_label[-1][1], f"{len(bold_label)} bold-label bullets",
            ("Keep labels only where they improve scanning; otherwise use ordinary sentences or fewer categories.",),
        )
        if finding:
            findings.append(finding)

    # 7. Short dramatic-pivot paragraphs.
    pivot_paragraphs: list[tuple[int, int]] = []
    for start, end in paragraphs:
        body = masked[start:end].strip()
        if 1 <= _word_count(body) <= 8 and len(_sentence_spans(body)) <= 1:
            lower = body.lower()
            if body.endswith("?") or any(phrase in lower for phrase in CONTROLLED_REVELATION_PHRASES):
                pivot_paragraphs.append((start, end))
    if len(pivot_paragraphs) >= 3:
        finding = _structural_finding(
            "STRUCT_DRAMATIC_PIVOT_PARAGRAPHS", text, line_map, config, spans,
            pivot_paragraphs[0][0], pivot_paragraphs[-1][1], f"{len(pivot_paragraphs)} short pivot paragraphs",
            ("Integrate pivots into adjacent paragraphs unless the break carries genuine emphasis.",),
        )
        if finding:
            findings.append(finding)

    # 8. Rhetorical question fragments.
    question_fragments: list[tuple[int, int, str]] = []
    fragment_re = re.compile(r"^(?:the|a|your|our|one)\s+[\w’'-]{1,24}\?$", re.I)
    for start, end in paragraphs:
        body = masked[start:end].strip()
        if fragment_re.match(body):
            question_fragments.append((start, end, body))
    if len(question_fragments) >= 2:
        finding = _structural_finding(
            "STRUCT_QUESTION_FRAGMENTS", text, line_map, config, spans,
            question_fragments[0][0], question_fragments[-1][1], f"{len(question_fragments)} question fragments",
            ("State the transition directly or retain only the strongest question.",),
        )
        if finding:
            findings.append(finding)

    # 9. Repeated Here is / Here's sentence starts.
    here_starts = [(start, end) for start, end in sentences if re.match(r"\s*here(?:'|’)s\b|\s*here\s+is\b", masked[start:end], re.I)]
    if len(here_starts) >= 3:
        finding = _structural_finding(
            "STRUCT_HERES_REPETITION", text, line_map, config, spans,
            here_starts[0][0], here_starts[-1][1], f"{len(here_starts)} 'here is' sentence openings",
            ("Replace announcement phrases with the announced claim.",),
        )
        if finding:
            findings.append(finding)

    # 10. Controlled revelation density.
    lower_masked = masked.lower()
    revelation_hits: list[tuple[int, int, str]] = []
    for phrase in CONTROLLED_REVELATION_PHRASES:
        for match in re.finditer(re.escape(phrase), lower_masked):
            revelation_hits.append((match.start(), match.end(), phrase))
    revelation_hits.sort()
    if len(revelation_hits) >= 3:
        finding = _structural_finding(
            "STRUCT_CONTROLLED_REVELATION", text, line_map, config, spans,
            revelation_hits[0][0], revelation_hits[-1][1], f"{len(revelation_hits)} controlled-revelation phrases",
            ("State claims in their argumentative order instead of repeatedly staging a reveal.",),
        )
        if finding:
            findings.append(finding)

    # 11. Sentence-initial conjunction density.
    initial_conjunctions = [
        (start, end) for start, end in sentences
        if re.match(r"\s*(?:and|but|so|yet|however|still)\b", masked[start:end], re.I)
    ]
    if len(sentences) >= 8 and len(initial_conjunctions) >= 3 and len(initial_conjunctions) / len(sentences) >= 0.35:
        finding = _structural_finding(
            "STRUCT_INITIAL_CONJUNCTIONS", text, line_map, config, spans,
            initial_conjunctions[0][0], initial_conjunctions[-1][1],
            f"{len(initial_conjunctions)} of {len(sentences)} sentences begin with conjunctions",
            ("Vary transitions where the repeated opening does not encode a real relation.",),
        )
        if finding:
            findings.append(finding)

    # 12. Participial-opening density.
    participial_starts: list[tuple[int, int]] = []
    for start, end in sentences:
        first = WORD_RE.findall(masked[start:end])[:2]
        if first and (first[0].lower() in PARTICIPIAL_OPENERS or first[0].lower().endswith("ing")):
            if "," in masked[start:min(end, start + 100)]:
                participial_starts.append((start, end))
    if len(sentences) >= 6 and len(participial_starts) >= 3 and len(participial_starts) / len(sentences) >= 0.25:
        finding = _structural_finding(
            "STRUCT_PARTICIPIAL_OPENINGS", text, line_map, config, spans,
            participial_starts[0][0], participial_starts[-1][1],
            f"{len(participial_starts)} of {len(sentences)} sentences use participial openings",
            ("Convert some openings to finite clauses with explicit agents.",),
        )
        if finding:
            findings.append(finding)

    # 13. Nominalization density.
    tokens = [token.lower() for token in WORD_RE.findall(masked)]
    nominalization_count = sum(1 for token in tokens if token in NOMINALIZATION_TERMS)
    nominalization_rate = nominalization_count * 100 / max(1, len(tokens))
    if nominalization_count >= 5 and nominalization_rate >= 4.0:
        first_match = next((m for m in WORD_RE.finditer(masked) if m.group(0).lower() in NOMINALIZATION_TERMS), None)
        start = first_match.start() if first_match else 0
        end = first_match.end() if first_match else min(len(text), 1)
        finding = _structural_finding(
            "STRUCT_NOMINALIZATION_DENSITY", text, line_map, config, spans,
            start, end, f"{nominalization_count} abstract action nouns ({nominalization_rate:.1f}% of tokens)",
            ("Replace selected action nouns with verbs and name the acting agent where known.",),
        )
        if finding:
            findings.append(finding)

    # 14. Colon density.
    colon_count = masked.count(":")
    colon_rate = colon_count * 1000 / words
    if colon_count >= 4 and colon_rate >= 12:
        start = masked.find(":")
        finding = _structural_finding(
            "STRUCT_COLON_DENSITY", text, line_map, config, spans,
            start, start + 1, f"{colon_count} colons ({colon_rate:.1f} per 1,000 words)",
            ("Check whether repeated label-and-explanation syntax can be varied or combined.",),
        )
        if finding:
            findings.append(finding)

    # 15. Em-dash density.
    dash_positions = [match.start() for match in re.finditer("—", masked)]
    dash_rate = len(dash_positions) * 1000 / words
    if len(dash_positions) >= 3 and dash_rate >= 8:
        finding = _structural_finding(
            "STRUCT_EM_DASH_DENSITY", text, line_map, config, spans,
            dash_positions[0], dash_positions[-1] + 1, f"{len(dash_positions)} em dashes ({dash_rate:.1f} per 1,000 words)",
            ("Keep em dashes that carry interruption or emphasis; revise repeated structural uses.",),
        )
        if finding:
            findings.append(finding)

    # 16. Bullet uniformity.
    if len(bullets) >= 5:
        lengths = [_word_count(item[2]) for item in bullets]
        cv = _coefficient_of_variation(lengths)
        first_words = [WORD_RE.findall(item[2])[:1] for item in bullets]
        normalized_first = [items[0].lower() for items in first_words if items]
        repeated_first_ratio = max(Counter(normalized_first).values(), default=0) / max(1, len(normalized_first))
        if cv <= 0.18 or repeated_first_ratio >= 0.6:
            finding = _structural_finding(
                "STRUCT_BULLET_UNIFORMITY", text, line_map, config, spans,
                bullets[0][0], bullets[-1][1], f"{len(bullets)} bullets; length CV={cv:.2f}; repeated-opening ratio={repeated_first_ratio:.2f}",
                ("Let bullet syntax reflect content; combine or vary items that perform identical rhetorical work.",),
            )
            if finding:
                findings.append(finding)

    # 17. Introduction/conclusion overlap.
    substantive_paragraphs = [(start, end) for start, end in paragraphs if _word_count(masked[start:end]) >= 20]
    if len(substantive_paragraphs) >= 4:
        first_start, first_end = substantive_paragraphs[0]
        last_start, last_end = substantive_paragraphs[-1]
        first_tokens = set(_content_tokens(masked[first_start:first_end]))
        last_tokens = set(_content_tokens(masked[last_start:last_end]))
        union = first_tokens | last_tokens
        jaccard = len(first_tokens & last_tokens) / len(union) if union else 0.0
        sequence = SequenceMatcher(None, " ".join(sorted(first_tokens)), " ".join(sorted(last_tokens))).ratio()
        if jaccard >= 0.42 and sequence >= 0.55:
            finding = _structural_finding(
                "STRUCT_INTRO_CONCLUSION_OVERLAP", text, line_map, config, spans,
                last_start, last_end, f"introduction/conclusion lexical overlap={jaccard:.2f}",
                ("Use the conclusion for consequence, decision, or unresolved issue rather than restating the opening.",),
            )
            if finding:
                findings.append(finding)

    # 18. Repeated paragraph-final implication formulas.
    ending_hits: list[tuple[int, int]] = []
    ending_pattern = re.compile(r"(?:this\s+(?:highlights|underscores|demonstrates|shows|suggests)|ultimately|taken\s+together)[^.!?]*[.!?]?\s*$", re.I)
    for start, end in paragraphs:
        body = masked[start:end]
        match = ending_pattern.search(body)
        if match:
            ending_hits.append((start + match.start(), start + match.end()))
    if len(ending_hits) >= 3:
        finding = _structural_finding(
            "STRUCT_PARAGRAPH_ENDING_FORMULA", text, line_map, config, spans,
            ending_hits[0][0], ending_hits[-1][1], f"{len(ending_hits)} formulaic paragraph endings",
            ("End paragraphs on the specific consequence or evidence instead of a generic implication sentence.",),
        )
        if finding:
            findings.append(finding)

    # 19. Generic examples without concrete detail.
    for start, end in sentences:
        body = masked[start:end].strip()
        lower = body.lower()
        if not any(lower.startswith(opener) for opener in GENERIC_EXAMPLE_OPENERS):
            continue
        has_number = bool(NUMBER_RE.search(body))
        proper_names = re.findall(r"(?<![.!?]\s)\b[A-Z][a-z]{2,}\b", text[start:end])
        concrete_terms = re.search(r"\b(?:because|when|after|before|using|measured|cost|rate|date|named|called)\b", lower)
        if not has_number and not proper_names and not concrete_terms:
            finding = _structural_finding(
                "STRUCT_GENERIC_EXAMPLE_WITHOUT_DETAIL", text, line_map, config, spans,
                start, end, text[start:end].strip(),
                ("Replace the hypothetical with a concrete case, mechanism, constraint, or quantity.",),
            )
            if finding:
                findings.append(finding)

    # 20. Unsupported consensus claims.
    consensus_pattern = re.compile(
        r"\b(?:researchers|experts|scientists|analysts|many\s+people|the\s+literature)\s+(?:agree|believe|suggest|indicate|show|have\s+shown)\b",
        re.I,
    )
    for match in consensus_pattern.finditer(masked):
        if not _has_nearby_source(text, match.start(), match.end()):
            finding = _structural_finding(
                "STRUCT_UNSUPPORTED_CONSENSUS", text, line_map, config, spans,
                match.start(), match.end(), text[match.start():match.end()],
                ("Name and cite the relevant source population, or state the claim without borrowed authority.",),
            )
            if finding:
                findings.append(finding)

    return findings


FINITE_VERB_RE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being|have|has|had|do|does|did|can|could|may|might|must|shall|should|will|would|"
    r"seem|seems|seemed|become|becomes|became|remain|remains|remained|show|shows|showed|indicate|indicates|indicated|"
    r"produce|produces|produced|cause|causes|caused|support|supports|supported|need|needs|needed|matter|matters|mattered|"
    r"[A-Za-z]{3,}(?:ed|es))\b",
    re.I,
)
SUBJECT_START_RE = re.compile(
    r"^(?:i|you|he|she|it|we|they|the|a|an|this|that|these|those|my|your|our|their|[A-Z][a-z]+)\b",
    re.I,
)


def _looks_independent_clause(fragment: str) -> bool:
    cleaned = fragment.strip(" \t,;:()[]{}\"'“”‘’")
    if _word_count(cleaned) < 3:
        return False
    if not SUBJECT_START_RE.search(cleaned):
        return False
    return FINITE_VERB_RE.search(cleaned) is not None


def _dash_replace_span(text: str, position: int) -> tuple[int, int]:
    start = position
    end = position + 1
    while start > 0 and text[start - 1] in " \t":
        start -= 1
    while end < len(text) and text[end] in " \t":
        end += 1
    return start, end


def _analyze_em_dashes(
    text: str,
    masked: str,
    spans: Sequence[ProtectedSpan],
    line_map: LineMap,
    config: RefinerConfig,
) -> tuple[list[Finding], list[Edit]]:
    if config.em_dash_policy == "preserve":
        return [], []

    findings: list[Finding] = []
    candidate_edits: list[Edit] = []
    sentences = _sentence_spans(masked)
    positions = [match.start() for match in re.finditer("—", masked)]
    consumed: set[int] = set()

    # Paired parentheticals are handled as a single finding and a single edit.
    for sentence_start, sentence_end in sentences:
        sentence_positions = [pos for pos in positions if sentence_start <= pos < sentence_end]
        if len(sentence_positions) != 2:
            continue
        first, second = sentence_positions
        inner = text[first + 1 : second].strip()
        before = text[sentence_start:first].strip()
        after = text[second + 1 : sentence_end].strip()
        if not before or not after or not inner or _word_count(inner) > 20:
            continue
        if re.search(r"[!?]", inner):
            continue
        rule = RULES_BY_ID["EMDASH_PAIRED_PARENTHETICAL"]
        comma_sentence = text[sentence_start:first].rstrip() + ", " + inner + ", " + text[second + 1 : sentence_end].lstrip()
        paren_sentence = text[sentence_start:first].rstrip() + " (" + inner + ") " + text[second + 1 : sentence_end].lstrip()
        finding = _make_finding(
            rule,
            text,
            line_map,
            first,
            second + 1,
            text[first : second + 1],
            (comma_sentence.strip(), paren_sentence.strip()),
            config,
            spans,
            "Paired em dashes appear to mark a parenthetical insertion. Commas or parentheses may preserve the relation with less punctuation emphasis.",
            "high",
        )
        findings.append(finding)
        candidate_edits.append(
            Edit(
                rule_id=rule.rule_id,
                start=first,
                end=second + 1,
                original=text[first : second + 1],
                replacement=", " + inner + ", ",
                mode="candidate",
                rationale="Replace a short paired parenthetical with commas.",
            )
        )
        consumed.update({first, second})

    for position in positions:
        if position in consumed:
            continue
        sentence_start, sentence_end = next(
            ((start, end) for start, end in sentences if start <= position < end),
            (max(0, text.rfind("\n", 0, position) + 1), text.find("\n", position) if text.find("\n", position) != -1 else len(text)),
        )
        left = text[sentence_start:position].strip()
        right = text[position + 1 : sentence_end].strip()
        line_start = max(text.rfind("\n", 0, position), text.rfind("\r", 0, position)) + 1
        before_on_line = text[line_start:position]
        after_on_line = text[position + 1 : sentence_end].strip()
        prior_nonspace = next((text[index] for index in range(position - 1, -1, -1) if not text[index].isspace()), "")
        next_nonspace = next((text[index] for index in range(position + 1, len(text)) if not text[index].isspace()), "")

        rule_id: str
        candidates: tuple[str, ...]
        replacement: str | None = None
        rationale = ""
        confidence: str | None = None

        if prior_nonspace.isdigit() and next_nonspace.isdigit():
            rule_id = "EMDASH_NUMERIC_RANGE"
            candidates = ("Use an en dash between numeric endpoints.",)
            edit_start, edit_end = _dash_replace_span(text, position)
            replacement = "–"
            rationale = "Normalize a numeric range to an en dash."
            confidence = "high"
        elif not before_on_line.strip() and re.match(r"[A-Z][A-Za-z.'’-]+(?:\s+[A-Z][A-Za-z.'’-]+){0,4}\s*$", after_on_line):
            rule_id = "EMDASH_ATTRIBUTION"
            candidates = ("Preserve the attribution dash unless the house style specifies another signature format.",)
            confidence = "high"
        elif re.match(r"(?i)(?:actually|well|wait|sorry|rather|no\b|i\s+mean|more\s+precisely|at\s+least)\b", right):
            rule_id = "EMDASH_INTERRUPTION"
            candidates = ("Preserve the em dash; it marks interruption or self-correction.",)
            confidence = "high"
        elif _looks_independent_clause(left) and _looks_independent_clause(right):
            rule_id = "EMDASH_INDEPENDENT_CLAUSES"
            right_cap = _capitalize_first(right)
            candidates = (
                left.rstrip() + "; " + right.lstrip(),
                left.rstrip() + ". " + right_cap.lstrip(),
            )
            edit_start, edit_end = _dash_replace_span(text, position)
            replacement = "; "
            rationale = "Join two plausible independent clauses with a semicolon."
            confidence = "medium"
        elif ("," in right or ";" in right) and re.search(
            r"(?i)\b(?:includes?|included|following|namely|such\s+as|parts?|items?|elements?|components?|steps?|reasons?|factors?)\b[^—.!?]{0,40}$",
            left,
        ):
            rule_id = "EMDASH_LIST_INTRODUCTION"
            candidates = (left.rstrip() + ": " + right.lstrip(),)
            edit_start, edit_end = _dash_replace_span(text, position)
            replacement = ": "
            rationale = "Introduce an explicit list with a colon."
            confidence = "high"
        elif re.search(r"(?i)\b(?:explanation|reason|answer|result|goal|problem|fact|conclusion|requirement|choice|option|remained|was|is)\s*$", left) and not _looks_independent_clause(right):
            rule_id = "EMDASH_APPOSITIONAL"
            candidates = (
                left.rstrip() + ": " + right.lstrip(),
                left.rstrip() + ". " + _capitalize_first(right).lstrip(),
            )
            edit_start, edit_end = _dash_replace_span(text, position)
            replacement = ": "
            rationale = "Introduce an appositional or explanatory expansion with a colon."
            confidence = "medium"
        else:
            rule_id = "EMDASH_AMBIGUOUS"
            candidates = ("Review manually; comma, semicolon, colon, period, and preservation encode different relations.",)
            confidence = "low"

        rule = RULES_BY_ID[rule_id]
        finding = _make_finding(
            rule,
            text,
            line_map,
            position,
            position + 1,
            "—",
            candidates,
            config,
            spans,
            confidence=confidence,
        )
        if CONFIDENCE_LEVELS[finding.confidence] >= CONFIDENCE_LEVELS[config.min_confidence]:
            findings.append(finding)
            if replacement is not None:
                candidate_edits.append(
                    Edit(
                        rule_id=rule_id,
                        start=edit_start,
                        end=edit_end,
                        original=text[edit_start:edit_end],
                        replacement=replacement,
                        mode="candidate",
                        rationale=rationale,
                    )
                )

    return findings, candidate_edits


EM_DASH_AUTO_RULES = {
    "EMDASH_PAIRED_PARENTHETICAL",
    "EMDASH_INDEPENDENT_CLAUSES",
    "EMDASH_APPOSITIONAL",
    "EMDASH_LIST_INTRODUCTION",
    "EMDASH_NUMERIC_RANGE",
}


def _edits_from_safe_findings(text: str, findings: Sequence[Finding]) -> list[Edit]:
    edits: list[Edit] = []
    for finding in findings:
        rule = RULES_BY_ID.get(finding.rule_id)
        if rule is None or rule.action != "safe_replace":
            continue
        pattern = COMPILED_RULES.get(rule.rule_id)
        if pattern is None:
            continue
        match = pattern.match(text, finding.start, finding.end)
        if match is None or match.end() != finding.end:
            continue
        candidates = _candidate_replacements(rule, match)
        if not candidates:
            continue
        edits.append(
            Edit(
                rule_id=rule.rule_id,
                start=finding.start,
                end=finding.end,
                original=text[finding.start:finding.end],
                replacement=candidates[0],
                mode="candidate",
                rationale=rule.description,
            )
        )
    return edits


def _eligible_em_dash_edit(edit: Edit, config: RefinerConfig, dash_count: int) -> bool:
    if edit.rule_id not in EM_DASH_AUTO_RULES:
        return False
    if config.em_dash_policy == "preserve":
        return False
    if edit.rule_id == "EMDASH_NUMERIC_RANGE":
        return True
    if config.em_dash_policy == "eliminate":
        return True
    if config.em_dash_policy == "reduce":
        return dash_count >= 2 or edit.rule_id in {"EMDASH_PAIRED_PARENTHETICAL", "EMDASH_LIST_INTRODUCTION"}
    return False


def _select_auto_edits(
    text: str,
    findings: Sequence[Finding],
    candidate_edits: Sequence[Edit],
    spans: Sequence[ProtectedSpan],
    config: RefinerConfig,
) -> list[Edit]:
    if config.mode != "auto":
        return []
    confidence_by_key = {(item.rule_id, item.start): item.confidence for item in findings}
    dash_count = text.count("—")
    eligible: list[tuple[int, float, Edit]] = []
    for edit in candidate_edits:
        rule = RULES_BY_ID.get(edit.rule_id)
        if rule is None:
            continue
        is_safe_rule = rule.action == "safe_replace"
        is_em_dash = _eligible_em_dash_edit(edit, config, dash_count)
        if not is_safe_rule and not is_em_dash:
            continue
        if RISK_LEVELS[rule.false_positive_risk] > RISK_LEVELS[config.max_auto_risk]:
            continue
        confidence = confidence_by_key.get((edit.rule_id, edit.start), rule.confidence)
        if CONFIDENCE_LEVELS[confidence] < CONFIDENCE_LEVELS[config.min_confidence]:
            continue
        if _span_at(spans, edit.start, edit.end, for_edit=True):
            continue
        priority = 3 if is_safe_rule else 2
        eligible.append((priority, rule.signal_weight, edit))

    # Prefer longer, higher-priority edits when candidate spans overlap.
    eligible.sort(key=lambda item: (item[2].start, -item[0], -(item[2].end - item[2].start), -item[1], item[2].rule_id))
    selected: list[Edit] = []
    for _, _, edit in eligible:
        if any(edit.start < prior.end and edit.end > prior.start for prior in selected):
            continue
        selected.append(replace(edit, mode="auto"))
    return sorted(selected, key=lambda item: (item.start, item.end))


def _apply_edits(text: str, edits: Sequence[Edit]) -> str:
    result = text
    for edit in sorted(edits, key=lambda item: (item.start, item.end), reverse=True):
        if result[edit.start:edit.end] != edit.original:
            raise ValueError(
                f"Edit precondition failed for {edit.rule_id} at {edit.start}:{edit.end}; source text changed"
            )
        result = result[:edit.start] + edit.replacement + result[edit.end:]
    return result


def _citation_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    tokens.extend(match.group(0) for match in DOI_RE.finditer(text))
    tokens.extend(match.group(0) for match in re.finditer(r"\[(?:\d+(?:\s*[-–,]\s*\d+)*)\]", text))
    tokens.extend(match.group(0) for match in re.finditer(r"\[\^[^\]\r\n]+\]", text))
    return tokens


def _validate_semantic_invariants(
    original: str,
    refined: str,
    edits: Sequence[Edit],
    spans: Sequence[ProtectedSpan],
) -> list[str]:
    errors: list[str] = []
    if original.endswith(("\n", "\r")) != refined.endswith(("\n", "\r")):
        errors.append("Final-newline state changed")
    if NUMBER_RE.findall(original) != NUMBER_RE.findall(refined):
        errors.append("Numeric tokens changed")
    if URL_RE.findall(original) != URL_RE.findall(refined):
        errors.append("URLs changed")
    if EMAIL_RE.findall(original) != EMAIL_RE.findall(refined):
        errors.append("Email addresses changed")
    if _citation_tokens(original) != _citation_tokens(refined):
        errors.append("Citation tokens changed")
    for edit in edits:
        protected = _span_at(spans, edit.start, edit.end, for_edit=True)
        if protected:
            errors.append(f"Edit {edit.rule_id} overlaps protected span {protected.kind}")
    return errors


def _dimension_band(score: float) -> str:
    if score < 5:
        return "low"
    if score < 12:
        return "moderate"
    if score < 25:
        return "high"
    return "very_high"


def _diminishing_weight(findings: Sequence[Finding]) -> float:
    by_rule: defaultdict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_rule[finding.rule_id].append(finding)
    total = 0.0
    factors = (1.0, 0.75, 0.5)
    for items in by_rule.values():
        for index, item in enumerate(items):
            factor = factors[index] if index < len(factors) else 0.25
            total += item.signal_weight * factor
    return total


def _score_dimensions(findings: Sequence[Finding], word_count: int) -> dict[str, dict[str, object]]:
    category_groups: dict[str, set[str]] = {
        "formulaic_construction_density": {
            "negative_parallelism", "discourse_signposting", "formulaic_architecture", "controlled_revelation",
            "this_evaluative", "vague_importance",
        },
        "negative_parallelism_density": {"negative_parallelism"},
        "discourse_signposting_density": {"discourse_signposting", "this_evaluative", "vague_importance"},
        "register_mismatch": {
            "research_lexicon", "grandiose_metaphor", "corporate_abstraction", "hollow_pairing", "soft_intensifier",
        },
        "abstract_noun_density": {"nominalization"},
        "unsupported_epistemic_language": {"unsupported_sourcing", "epistemic_hedging", "artificial_balance"},
        "structural_repetition": {"structural_repetition", "formulaic_architecture"},
        "punctuation_concentration": {"punctuation", "formatting"},
        "author_profile_divergence": {"author_profile"},
        "assistant_residue": {"assistant_residue"},
    }
    denominator = max(250, word_count)
    result: dict[str, dict[str, object]] = {}
    for name, categories in category_groups.items():
        selected = [finding for finding in findings if finding.category in categories]
        raw = _diminishing_weight(selected)
        normalized = round(raw * 1000 / denominator, 2)
        result[name] = {
            "score": normalized,
            "band": _dimension_band(normalized),
            "finding_count": len(selected),
        }
    return result


def _build_metrics(
    text: str,
    refined: str,
    findings: Sequence[Finding],
    edits: Sequence[Edit],
    spans: Sequence[ProtectedSpan],
    config: RefinerConfig,
    format_name: str,
) -> dict[str, object]:
    word_count = _word_count(text)
    sentence_count = len(_sentence_spans(_mask_for_analysis(text, spans)))
    paragraph_count = len(_paragraph_spans(_mask_for_analysis(text, spans)))
    category_counts = Counter(item.category for item in findings)
    confidence_counts = Counter(item.confidence for item in findings)
    evidence_counts = Counter(item.evidence_class for item in findings)
    risk_counts = Counter(item.false_positive_risk for item in findings)
    protected_chars = sum(span.end - span.start for span in spans if span.exclude_from_edit)
    weighted_total = _diminishing_weight(findings)
    marker_density = round(weighted_total * 1000 / max(250, word_count), 2)
    return {
        "mode": config.mode,
        "profile": config.profile,
        "author_profile": config.author_profile.name if config.author_profile else None,
        "format": format_name,
        "em_dash_policy": config.em_dash_policy,
        "min_confidence": config.min_confidence,
        "max_auto_risk": config.max_auto_risk,
        "word_count": word_count,
        "sentence_count": sentence_count,
        "paragraph_count": paragraph_count,
        "finding_count": len(findings),
        "applied_edit_count": len(edits),
        "category_counts": dict(sorted(category_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "evidence_counts": dict(sorted(evidence_counts.items())),
        "false_positive_risk_counts": dict(sorted(risk_counts.items())),
        "marker_density_score": marker_density,
        "marker_density_band": _dimension_band(marker_density),
        "dimensions": _score_dimensions(findings, word_count),
        "protected_span_count": len(spans),
        "protected_character_count": protected_chars,
        "protected_character_fraction": round(protected_chars / max(1, len(text)), 4),
        "em_dash_count": text.count("—"),
        "colon_count": text.count(":"),
        "registry_rule_count": len(RULES),
        "structural_analyzer_count": len(STRUCTURAL_RULE_IDS),
        "changed": text != refined,
        "idempotent_check": False,
    }


def _run_refinement_once(text: str, config: RefinerConfig) -> RefinementResult:
    config = _resolve_config(config)
    format_name = _detect_format(config.format, config.source_name, text)
    spans = _collect_protected_spans(text, format_name, config.author_profile, edit_quotes=config.edit_quotes)
    masked = _mask_for_analysis(text, spans)
    line_map = LineMap(text)

    findings: list[Finding] = []
    findings.extend(_lexical_findings(text, spans, line_map, config))
    findings.extend(_profile_phrase_findings(text, spans, line_map, config))
    findings.extend(_profile_preference_findings(text, masked, spans, line_map, config))
    findings.extend(_analyze_structural(text, masked, spans, line_map, config))
    dash_findings, dash_candidates = _analyze_em_dashes(text, masked, spans, line_map, config)
    findings.extend(dash_findings)
    findings = _deduplicate_findings(findings)

    safe_candidates = _edits_from_safe_findings(text, findings)
    candidate_edits = safe_candidates + dash_candidates
    selected_edits = _select_auto_edits(text, findings, candidate_edits, spans, config)
    warnings: list[str] = []

    refined = text
    if selected_edits:
        try:
            refined = _apply_edits(text, selected_edits)
        except ValueError as exc:
            warnings.append(str(exc))
            selected_edits = []
            refined = text

    invariant_errors = _validate_semantic_invariants(text, refined, selected_edits, spans)
    if invariant_errors:
        warnings.extend(f"Automatic edits reverted: {error}" for error in invariant_errors)
        selected_edits = []
        refined = text

    metrics = _build_metrics(text, refined, findings, selected_edits, spans, config, format_name)
    return RefinementResult(
        original_text=text,
        refined_text=refined,
        findings=findings,
        edits=selected_edits,
        warnings=warnings,
        metrics=metrics,
        protected_spans=spans,
    )


def _run_refinement(text: str, config: RefinerConfig) -> RefinementResult:
    result = _run_refinement_once(text, config)
    if config.mode == "auto":
        second = _run_refinement_once(result.refined_text, config)
        idempotent = second.refined_text == result.refined_text
        result.metrics["idempotent_check"] = idempotent
        if not idempotent:
            result.warnings.append("Idempotency check failed: a second auto pass would make additional edits")
    else:
        result.metrics["idempotent_check"] = True
    return result


def analyze_text(text: str, config: RefinerConfig | None = None) -> RefinementResult:
    """Analyze text without modifying it, regardless of the supplied mode."""

    effective = config or RefinerConfig()
    return _run_refinement(text, replace(effective, mode="audit"))


def refine_text(text: str, config: RefinerConfig | None = None) -> RefinementResult:
    """Analyze and optionally refine text according to the configured mode."""

    return _run_refinement(text, config or RefinerConfig())


def render_unified_diff(result: RefinementResult, source_name: str = "input") -> str:
    """Render a stable unified diff for a refinement result."""

    if not result.changed:
        return ""
    return "".join(
        unified_diff(
            result.original_text.splitlines(keepends=True),
            result.refined_text.splitlines(keepends=True),
            fromfile=f"{source_name} (original)",
            tofile=f"{source_name} (refined)",
        )
    )


def render_human_report(result: RefinementResult, source_name: str = "input") -> str:
    """Render a concise but inspectable human-readable report."""

    metrics = result.metrics
    lines = [
        f"{TOOL_NAME} {VERSION}",
        f"Source: {source_name}",
        f"Mode/profile: {metrics.get('mode')} / {metrics.get('profile')}",
        f"Format/em-dash policy: {metrics.get('format')} / {metrics.get('em_dash_policy')}",
        f"Words/sentences/paragraphs: {metrics.get('word_count')} / {metrics.get('sentence_count')} / {metrics.get('paragraph_count')}",
        f"Findings/applied edits: {len(result.findings)} / {len(result.edits)}",
        f"Marker-density band: {metrics.get('marker_density_band')} ({metrics.get('marker_density_score')})",
        f"Protected spans: {metrics.get('protected_span_count')}",
        f"Idempotent: {metrics.get('idempotent_check')}",
    ]
    category_counts = metrics.get("category_counts", {})
    confidence_counts = metrics.get("confidence_counts", {})
    if isinstance(category_counts, dict) and category_counts:
        lines.append("Categories: " + ", ".join(f"{key}={value}" for key, value in category_counts.items()))
    if isinstance(confidence_counts, dict) and confidence_counts:
        lines.append("Confidence counts: " + ", ".join(f"{key}={value}" for key, value in confidence_counts.items()))
    dimensions = metrics.get("dimensions", {})
    if isinstance(dimensions, dict):
        lines.append("Dimensions:")
        for name, payload in dimensions.items():
            if isinstance(payload, dict):
                lines.append(f"  - {name}: {payload.get('band')} ({payload.get('score')}; {payload.get('finding_count')} findings)")
    if result.findings:
        lines.append("Findings:")
        for finding in result.findings:
            protection = f"; protected={finding.protected_kind}" if finding.protected else ""
            lines.append(
                f"  - L{finding.line}:C{finding.column} [{finding.rule_id}] "
                f"{finding.matched_text!r} | confidence={finding.confidence}; risk={finding.false_positive_risk}; "
                f"evidence={finding.evidence_class}{protection}"
            )
            lines.append(f"    {finding.explanation}")
            for candidate in finding.candidates[:3]:
                lines.append(f"    Candidate: {candidate}")
    if result.edits:
        lines.append("Applied edits:")
        for edit in result.edits:
            lines.append(f"  - [{edit.rule_id}] {edit.original!r} -> {edit.replacement!r}: {edit.rationale}")
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    lines.append("Limitation: findings are editorial signals, not evidence of authorship.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Command-line interface and deterministic file/report handling
# ---------------------------------------------------------------------------

import contextlib
import os


def _read_utf8_source(path: Path) -> str:
    """Read UTF-8 without newline translation so CRLF survives round trips."""

    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise OSError(f"Cannot read UTF-8 input {path}: {exc}") from exc


def _atomic_write_utf8(path: Path, text: str, *, preserve_mode_from: Path | None = None) -> None:
    """Atomically write UTF-8 text, creating parent directories when needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    mode: int | None = None
    if preserve_mode_from is not None:
        with contextlib.suppress(OSError):
            mode = preserve_mode_from.stat().st_mode & 0o7777
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as exc:
        with contextlib.suppress(Exception):
            temporary.unlink()  # type: ignore[possibly-undefined]
        raise OSError(f"Cannot write {path}: {exc}") from exc


def _write_json_report(path: Path, result: RefinementResult, source: str | None) -> None:
    payload = result.to_payload(source)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write_utf8(path, serialized)


def _rule_table() -> str:
    """Return the embedded registry as stable tab-separated text."""

    columns = (
        "rule_id",
        "category",
        "evidence_class",
        "weight",
        "false_positive_risk",
        "action",
        "confidence",
        "min_occurrences",
        "description",
    )
    lines = ["\t".join(columns)]
    for rule in RULES:
        lines.append(
            "\t".join(
                (
                    rule.rule_id,
                    rule.category,
                    rule.evidence_class,
                    f"{rule.signal_weight:g}",
                    rule.false_positive_risk,
                    rule.action,
                    rule.confidence,
                    str(rule.min_occurrences),
                    rule.description.replace("\t", " ").replace("\n", " "),
                )
            )
        )
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm_copy_refiner.py",
        description=(
            "Audit and conservatively refine formulaic or mechanically patterned copy. "
            "The tool reports editorial signals; it does not infer authorship."
        ),
    )
    parser.add_argument("input", nargs="?", help="UTF-8 input path, or '-' for standard input")
    parser.add_argument("--mode", choices=sorted(MODES), default="audit", help="audit, suggest, or conservative auto mode")
    parser.add_argument("--output", type=Path, help="write refined text to a new path")
    parser.add_argument("--check", action="store_true", help="return status 1 when enabled findings exist; never write text")
    parser.add_argument("--apply", action="store_true", help="atomically overwrite the input; requires --mode auto")
    parser.add_argument("--diff", action="store_true", help="print a unified diff when text changes")
    parser.add_argument("--report-json", type=Path, help="write a deterministic JSON audit report")
    parser.add_argument("--profile", choices=sorted(BUILTIN_PROFILES), default="plain", help="built-in editorial profile")
    parser.add_argument("--em-dash-policy", choices=sorted(EM_DASH_POLICIES), help="preserve, reduce, or eliminate em dashes")
    parser.add_argument("--format", choices=sorted(FORMATS), default="auto", help="input format or automatic detection")
    parser.add_argument("--author-profile", type=Path, help="optional author/house-style JSON profile")
    parser.add_argument("--min-confidence", choices=sorted(CONFIDENCE_LEVELS, key=CONFIDENCE_LEVELS.get), default="low")
    parser.add_argument("--max-auto-risk", choices=("low", "medium"), default="low")
    parser.add_argument("--edit-quotes", action="store_true", help="permit automatic edits inside quoted prose (off by default)")
    parser.add_argument("--list-rules", action="store_true", help="print the embedded rule registry and exit")
    parser.add_argument("--self-test", action="store_true", help="run the embedded standard-library test suite")
    parser.add_argument("--quiet", action="store_true", help="suppress the ordinary human-readable report")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _validate_cli_arguments(args: argparse.Namespace) -> None:
    """Validate option relationships independently of argparse parsing."""

    utility_modes = int(bool(args.list_rules)) + int(bool(args.self_test))
    if utility_modes > 1:
        raise ValueError("--list-rules and --self-test cannot be combined")
    if utility_modes:
        if args.input is not None:
            raise ValueError("an input path cannot be combined with --list-rules or --self-test")
        if args.output or args.apply or args.check or args.diff or args.report_json or args.author_profile:
            raise ValueError("file-processing options cannot be combined with --list-rules or --self-test")
        return

    if args.input is None:
        raise ValueError("an input path or '-' is required")
    if args.check and (args.apply or args.output):
        raise ValueError("--check cannot be combined with --apply or --output")
    if args.apply and args.output:
        raise ValueError("--apply and --output are mutually exclusive")
    if args.apply and args.input == "-":
        raise ValueError("--apply cannot overwrite standard input")
    if args.apply and args.mode != "auto":
        raise ValueError("--apply requires --mode auto")
    if args.output and args.mode != "auto":
        raise ValueError("--output requires --mode auto")
    if args.mode == "auto" and not args.output and not args.apply and args.diff:
        raise ValueError("--diff with auto output on stdout requires --output or --apply")
    if args.output and args.input != "-":
        try:
            if Path(args.input).resolve() == args.output.resolve():
                raise ValueError("--output must differ from the input path; use --apply to overwrite")
        except OSError:
            # Resolution failure is handled during normal I/O.
            pass


def _config_from_args(args: argparse.Namespace, source_name: str | None) -> RefinerConfig:
    profile = load_author_profile(args.author_profile) if args.author_profile else None
    mode = "audit" if args.check else args.mode
    return RefinerConfig(
        mode=mode,
        profile=args.profile,
        em_dash_policy=args.em_dash_policy,
        format=args.format,
        min_confidence=args.min_confidence,
        max_auto_risk=args.max_auto_risk,
        author_profile=profile,
        edit_quotes=args.edit_quotes,
        source_name=source_name,
    )


def _process_cli(args: argparse.Namespace) -> int:
    source_label = "stdin" if args.input == "-" else str(args.input)
    try:
        if args.input == "-":
            original = sys.stdin.read()
        else:
            input_path = Path(args.input)
            if not input_path.exists():
                raise OSError(f"Input does not exist: {input_path}")
            if not input_path.is_file():
                raise OSError(f"Input is not a regular file: {input_path}")
            original = _read_utf8_source(input_path)
        config = _config_from_args(args, source_label)
        result = refine_text(original, config)
    except (OSError, ValueError) as exc:
        print(f"[{TOOL_NAME}] error: {exc}", file=sys.stderr)
        return EXIT_IO

    if args.report_json:
        try:
            _write_json_report(args.report_json, result, source_label)
        except OSError as exc:
            print(f"[{TOOL_NAME}] error: {exc}", file=sys.stderr)
            return EXIT_IO

    if args.apply or args.output:
        if not bool(result.metrics.get("idempotent_check", False)):
            print(f"[{TOOL_NAME}] error: refusing to write non-idempotent output", file=sys.stderr)
            return EXIT_IO
        target = Path(args.input) if args.apply else args.output
        assert target is not None
        try:
            _atomic_write_utf8(target, result.refined_text, preserve_mode_from=Path(args.input) if args.apply else None)
        except OSError as exc:
            print(f"[{TOOL_NAME}] error: {exc}", file=sys.stderr)
            return EXIT_IO

    report = render_human_report(result, source_label)
    diff_text = render_unified_diff(result, source_label)

    if args.mode == "auto" and not args.apply and not args.output and not args.check:
        sys.stdout.write(result.refined_text)
        if not args.quiet:
            sys.stderr.write(report)
    else:
        if args.diff and diff_text:
            sys.stdout.write(diff_text)
            if diff_text and not diff_text.endswith("\n"):
                sys.stdout.write("\n")
        if not args.quiet:
            sys.stdout.write(report)

    if args.check and result.findings:
        return EXIT_FINDINGS
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a deterministic process status."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        _validate_cli_arguments(args)
    except ValueError as exc:
        parser.error(str(exc))

    if args.list_rules:
        sys.stdout.write(_rule_table())
        return EXIT_OK
    if args.self_test:
        return run_self_tests()
    return _process_cli(args)


# ---------------------------------------------------------------------------
# Embedded self-tests
# ---------------------------------------------------------------------------

_SELF_TEST_ASSERTIONS = 0


class _CountingTestCase(unittest.TestCase):
    """TestCase with explicit assertion accounting for the CLI summary."""

    def check_equal(self, first: object, second: object, message: str | None = None) -> None:
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        super().assertEqual(first, second, message)

    def check_not_equal(self, first: object, second: object, message: str | None = None) -> None:
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        super().assertNotEqual(first, second, message)

    def check_true(self, expression: object, message: str | None = None) -> None:
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        super().assertTrue(expression, message)

    def check_false(self, expression: object, message: str | None = None) -> None:
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        super().assertFalse(expression, message)

    def check_in(self, member: object, container: object, message: str | None = None) -> None:
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        super().assertIn(member, container, message)

    def check_not_in(self, member: object, container: object, message: str | None = None) -> None:
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        super().assertNotIn(member, container, message)

    def check_greater_equal(self, first: object, second: object, message: str | None = None) -> None:
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        super().assertGreaterEqual(first, second, message)

    @contextlib.contextmanager
    def check_raises(self, exception: type[BaseException]):
        global _SELF_TEST_ASSERTIONS
        _SELF_TEST_ASSERTIONS += 1
        with super().assertRaises(exception):
            yield


class _RefinerSelfTests(_CountingTestCase):
    def test_01_clean_text_unchanged(self) -> None:
        text = "The trial lasted six weeks. It produced no measurable effect.\n"
        audit = refine_text(text, RefinerConfig(mode="audit"))
        auto = refine_text(text, RefinerConfig(mode="auto"))
        self.check_equal(audit.refined_text, text)
        self.check_equal(auto.refined_text, text)
        self.check_false(audit.changed)

    def test_02_audit_never_edits(self) -> None:
        text = "It is important to note that the sample was small."
        result = refine_text(text, RefinerConfig(mode="audit"))
        self.check_equal(result.refined_text, text)
        self.check_equal(result.edits, [])
        self.check_true(bool(result.findings))

    def test_03_suggest_never_applies_semantic_rewrite(self) -> None:
        text = "It is not speed; it is reliability."
        result = refine_text(text, RefinerConfig(mode="suggest"))
        self.check_equal(result.refined_text, text)
        self.check_equal(result.edits, [])
        self.check_true(any(item.category == "negative_parallelism" for item in result.findings))

    def test_04_auto_safe_prefix(self) -> None:
        text = "It is important to note that the sample was small."
        result = refine_text(text, RefinerConfig(mode="auto"))
        self.check_equal(result.refined_text, "The sample was small.")
        self.check_equal(len(result.edits), 1)
        self.check_true(result.metrics["idempotent_check"])

    def test_05_final_newline_preserved(self) -> None:
        text = "It is worth noting that the result is stable.\n"
        result = refine_text(text, RefinerConfig(mode="auto"))
        self.check_true(result.refined_text.endswith("\n"))
        self.check_equal(result.refined_text, "The result is stable.\n")

    def test_06_absent_final_newline_preserved(self) -> None:
        text = "It should be noted that the result is stable."
        result = refine_text(text, RefinerConfig(mode="auto"))
        self.check_false(result.refined_text.endswith("\n"))
        self.check_equal(result.refined_text, "The result is stable.")

    def test_07_crlf_preserved(self) -> None:
        text = "It is important to note that the sample was small.\r\nNext line.\r\n"
        result = refine_text(text, RefinerConfig(mode="auto"))
        self.check_equal(result.refined_text.count("\r\n"), 2)
        self.check_not_in("\nNext line.\n", result.refined_text)

    def test_08_unicode_and_zero_width(self) -> None:
        text = "Café\u200b data remain multilingual: العربية, 日本語."
        result = refine_text(text, RefinerConfig(mode="auto"))
        self.check_not_in("\u200b", result.refined_text)
        self.check_in("Café", result.refined_text)
        self.check_in("العربية", result.refined_text)

    def test_09_internal_bom_removed(self) -> None:
        text = "Alpha\ufeffbeta."
        result = refine_text(text, RefinerConfig(mode="auto"))
        self.check_equal(result.refined_text, "Alphabeta.")
        self.check_true(any(edit.rule_id == "UNICODE_BOM" for edit in result.edits))

    def test_10_paired_parenthetical_dash(self) -> None:
        text = "The trial—which lasted six weeks—produced no measurable effect."
        suggest = refine_text(text, RefinerConfig(mode="suggest", em_dash_policy="eliminate"))
        auto = refine_text(text, RefinerConfig(mode="auto", em_dash_policy="eliminate", max_auto_risk="medium"))
        self.check_true(any(item.rule_id == "EMDASH_PAIRED_PARENTHETICAL" for item in suggest.findings))
        self.check_equal(auto.refined_text, "The trial, which lasted six weeks, produced no measurable effect.")

    def test_11_independent_clause_dash(self) -> None:
        text = "The sample was small—the effect was nevertheless consistent."
        result = refine_text(text, RefinerConfig(mode="suggest", em_dash_policy="eliminate"))
        finding = next(item for item in result.findings if item.rule_id == "EMDASH_INDEPENDENT_CLAUSES")
        self.check_in("The sample was small; the effect was nevertheless consistent.", finding.candidates)
        self.check_in("The sample was small. The effect was nevertheless consistent.", finding.candidates)

    def test_12_appositional_dash(self) -> None:
        text = "Only one explanation remained—measurement error."
        result = refine_text(text, RefinerConfig(mode="suggest", em_dash_policy="eliminate"))
        finding = next(item for item in result.findings if item.rule_id == "EMDASH_APPOSITIONAL")
        self.check_in("Only one explanation remained: measurement error.", finding.candidates)

    def test_13_interruption_dash_preserved(self) -> None:
        text = "I thought the result would—actually, never mind."
        result = refine_text(text, RefinerConfig(mode="auto", em_dash_policy="eliminate", max_auto_risk="medium"))
        self.check_equal(result.refined_text, text)
        self.check_true(any(item.rule_id == "EMDASH_INTERRUPTION" for item in result.findings))

    def test_14_numeric_range_dash(self) -> None:
        text = "The study ran from 2019—2024."
        result = refine_text(text, RefinerConfig(mode="auto", em_dash_policy="eliminate"))
        self.check_equal(result.refined_text, "The study ran from 2019–2024.")
        self.check_true(any(edit.rule_id == "EMDASH_NUMERIC_RANGE" for edit in result.edits))

    def test_15_list_introduction_dash(self) -> None:
        text = "The system includes three parts—input, processing, and output."
        result = refine_text(text, RefinerConfig(mode="suggest", em_dash_policy="eliminate"))
        finding = next(item for item in result.findings if item.rule_id == "EMDASH_LIST_INTRODUCTION")
        self.check_in("The system includes three parts: input, processing, and output.", finding.candidates)

    def test_16_markdown_fence_protected(self) -> None:
        text = 'Before.  \n```python\nprint("It is important to note that x")\n```\n    It is important to note that indented_code = True\nAfter.\n'
        result = refine_text(text, RefinerConfig(mode="auto", format="markdown"))
        self.check_equal(result.refined_text, text)
        self.check_in("Before.  \n", result.refined_text)
        self.check_true(any("markdown_fenced_code" in span.kind for span in result.protected_spans))
        self.check_true(any("markdown_indented_code" in span.kind for span in result.protected_spans))

    def test_17_tilde_fence_protected(self) -> None:
        text = "~~~\nIt is important to note that x.\n~~~\n"
        result = refine_text(text, RefinerConfig(mode="auto", format="markdown"))
        self.check_equal(result.refined_text, text)
        self.check_true(any("markdown_fenced_code" in span.kind for span in result.protected_spans))

    def test_18_inline_code_protected(self) -> None:
        text = "Use `It is important to note that x` exactly."
        result = refine_text(text, RefinerConfig(mode="auto", format="markdown"))
        self.check_equal(result.refined_text, text)
        self.check_true(any("markdown_inline_code" in span.kind for span in result.protected_spans))

    def test_19_markdown_link_protected(self) -> None:
        text = "Read [the reference](https://example.com/a—b) before editing."
        result = refine_text(text, RefinerConfig(mode="auto", format="markdown", em_dash_policy="eliminate"))
        self.check_equal(result.refined_text, text)
        self.check_true(any("markdown_link" in span.kind for span in result.protected_spans))

    def test_20_url_and_email_protected(self) -> None:
        text = "Visit https://example.com/a—b and write editor@example.com."
        result = refine_text(text, RefinerConfig(mode="auto", em_dash_policy="eliminate"))
        self.check_equal(result.refined_text, text)
        kinds = "+".join(span.kind for span in result.protected_spans)
        self.check_in("url", kinds)
        self.check_in("email", kinds)

    def test_21_html_code_protected(self) -> None:
        text = '<div title="It is important to note that x">Label</div><pre>It is important to note that x—a.</pre> Outside.'
        result = refine_text(text, RefinerConfig(mode="auto", format="html", em_dash_policy="eliminate"))
        self.check_equal(result.refined_text, text)
        self.check_true(any("html_code" in span.kind for span in result.protected_spans))
        self.check_true(any("html_markup" in span.kind for span in result.protected_spans))

    def test_22_latex_verbatim_protected(self) -> None:
        text = "\\begin{verbatim}\nIt is important to note that x—a.\n\\end{verbatim}\nOutside."
        result = refine_text(text, RefinerConfig(mode="auto", format="latex", em_dash_policy="eliminate"))
        self.check_equal(result.refined_text, text)
        self.check_true(any("latex_verbatim" in span.kind for span in result.protected_spans))

    def test_23_quotes_flagged_but_not_edited(self) -> None:
        text = "She wrote, “It is important to note that the sample was small.”"
        protected = refine_text(text, RefinerConfig(mode="auto"))
        editable = refine_text(text, RefinerConfig(mode="auto", edit_quotes=True))
        self.check_equal(protected.refined_text, text)
        self.check_not_equal(editable.refined_text, text)
        self.check_in("“The sample was small.”", editable.refined_text)
        blockquote = "> It is important to note that quoted Markdown stays literal.\n"
        block_result = refine_text(blockquote, RefinerConfig(mode="auto", format="markdown"))
        self.check_equal(block_result.refined_text, blockquote)
        self.check_true(any("markdown_blockquote" in span.kind for span in block_result.protected_spans))

    def test_24_negative_parallelism_detected(self) -> None:
        text = "It is not speed; it is reliability. Not just output, but evidence."
        result = analyze_text(text)
        count = sum(item.category == "negative_parallelism" for item in result.findings)
        self.check_greater_equal(count, 2)
        self.check_true(all(item.candidates for item in result.findings if item.category == "negative_parallelism"))

    def test_25_repeated_signpost_detected(self) -> None:
        text = "At its core, the method is simple. At its core, the result is inspectable. At its core, the record is auditable."
        result = analyze_text(text)
        signposts = [item for item in result.findings if item.category == "discourse_signposting"]
        self.check_greater_equal(len(signposts), 2)
        self.check_true(all(item.matched_text.lower() == "at its core" for item in signposts))

    def test_26_nominalization_density_detected(self) -> None:
        text = "The implementation, utilization, optimization, integration, and operationalization required evaluation and assessment."
        result = analyze_text(text)
        self.check_true(any(item.rule_id == "STRUCT_NOMINALIZATION_DENSITY" for item in result.findings))
        self.check_in("abstract_noun_density", result.metrics["dimensions"])

    def test_27_unsupported_source_formula(self) -> None:
        unsupported = analyze_text("Studies have shown that the method works. Research indicates that it scales.")
        sourced = analyze_text("Studies have shown that the method works [1].")
        self.check_true(any(item.category == "unsupported_sourcing" for item in unsupported.findings))
        self.check_false(any(item.category == "unsupported_sourcing" for item in sourced.findings))

    def test_28_structural_repetition_detected(self) -> None:
        text = "This improves speed. This improves clarity. This improves safety. This improves accuracy."
        result = analyze_text(text)
        self.check_true(any(item.rule_id == "STRUCT_REPEATED_SENTENCE_OPENING" for item in result.findings))
        dimension = result.metrics["dimensions"]["structural_repetition"]
        self.check_in(dimension["band"], {"moderate", "high", "very_high"})

    def test_29_domain_context_false_positive_guard(self) -> None:
        ordinary = analyze_text("The museum conserves a woven tapestry made on a seventeenth-century loom.")
        metaphor = analyze_text("The platform creates a rich tapestry of cybersecurity challenges.")
        self.check_false(any(item.category == "research_lexicon" for item in ordinary.findings))
        self.check_true(any("tapestry" in item.matched_text.lower() for item in metaphor.findings))

    def test_30_json_serialization_and_schema(self) -> None:
        result = analyze_text("At its core, the system is a robust framework.")
        payload = result.to_payload("sample.txt")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.check_in('"tool": "llm_copy_refiner"', serialized)
        self.check_in('"findings"', serialized)
        self.check_not_in("ai_probability", serialized.lower())

    def test_31_deterministic_payload(self) -> None:
        text = "At its core, this is not merely a framework; it is a dynamic ecosystem."
        first = analyze_text(text).to_payload("x")
        second = analyze_text(text).to_payload("x")
        self.check_equal(first, second)

    def test_32_auto_idempotency(self) -> None:
        text = "It is important to note that the trial ran from 2019—2024."
        config = RefinerConfig(mode="auto", em_dash_policy="eliminate", max_auto_risk="medium")
        first = refine_text(text, config)
        second = refine_text(first.refined_text, config)
        self.check_equal(first.refined_text, second.refined_text)
        self.check_true(first.metrics["idempotent_check"])

    def test_33_invalid_author_profile_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"em_dash_policy": "obliterate"}', encoding="utf-8")
            with self.check_raises(ValueError):
                load_author_profile(path)
            unknown_rule = Path(directory) / "unknown-rule.json"
            unknown_rule.write_text('{"disabled_rules": ["NOT_A_REAL_RULE"]}', encoding="utf-8")
            with self.check_raises(ValueError):
                load_author_profile(unknown_rule)
            unknown_category = Path(directory) / "unknown-category.json"
            unknown_category.write_text('{"category_weights": {"imaginary_category": 2}}', encoding="utf-8")
            with self.check_raises(ValueError):
                load_author_profile(unknown_category)
            unsafe_pattern = Path(directory) / "unsafe-pattern.json"
            unsafe_pattern.write_text('{"protected_patterns": ["(a+)+"]}', encoding="utf-8")
            with self.check_raises(ValueError):
                load_author_profile(unsafe_pattern)

    def test_34_author_profile_rules_and_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "Test",
                        "discouraged_phrases": ["move the needle"],
                        "protected_patterns": ["KEEP:.*"],
                    }
                ),
                encoding="utf-8",
            )
            profile = load_author_profile(path)
            text = "We should move the needle. KEEP: It is important to note that x."
            result = refine_text(text, RefinerConfig(mode="auto", author_profile=profile))
            self.check_true(any(item.category == "author_profile" for item in result.findings))
            self.check_in("KEEP: It is important to note that x.", result.refined_text)

            strict_path = Path(directory) / "strict.json"
            strict_path.write_text(
                json.dumps({"name": "Strict", "allow_contractions": False, "preferred_sentence_words": [4, 6]}),
                encoding="utf-8",
            )
            strict = load_author_profile(strict_path)
            strict_result = analyze_text("It isn't ready. This compact sentence is acceptable. This deliberately overlong sentence exceeds the configured range by several unnecessary words. We won't ship.", RefinerConfig(author_profile=strict))
            self.check_true(any(item.rule_id == "PROFILE_CONTRACTION_DISALLOWED" for item in strict_result.findings))
            self.check_true(any(item.rule_id == "PROFILE_SENTENCE_WORD_RANGE" for item in strict_result.findings))

    def test_35_registry_minimums(self) -> None:
        substantive_multiword = sum(1 for rule in RULES if rule.pattern and (r"\s" in rule.pattern or " " in rule.pattern))
        high_priority = sum(1 for rule in RULES if rule.signal_weight >= 4.0)
        self.check_greater_equal(len(RULES), 120)
        self.check_greater_equal(len(STRUCTURAL_RULE_IDS), 10)
        self.check_greater_equal(substantive_multiword, 20)
        self.check_greater_equal(high_priority, 35)
        self.check_true(all(rule.evidence_class in EVIDENCE_CLASSES for rule in RULES))
        self.check_true(all(rule.false_positive_risk in RISK_LEVELS for rule in RULES))

    def test_36_unified_diff(self) -> None:
        result = refine_text("It is important to note that x.\n", RefinerConfig(mode="auto"))
        diff = render_unified_diff(result, "sample.txt")
        self.check_in("--- sample.txt (original)", diff)
        self.check_in("+++ sample.txt (refined)", diff)
        self.check_in("+X.", diff)

    def test_37_cli_conflicts(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["input.txt", "--mode", "audit", "--apply"])
        with self.check_raises(ValueError):
            _validate_cli_arguments(args)
        args = parser.parse_args(["input.txt", "--check", "--output", "out.txt"])
        with self.check_raises(ValueError):
            _validate_cli_arguments(args)

    def test_38_cli_check_status_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.txt"
            original = "At its core, the system is a robust framework."
            path.write_text(original, encoding="utf-8")
            args = _build_parser().parse_args([str(path), "--check", "--quiet"])
            _validate_cli_arguments(args)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = _process_cli(args)
            self.check_equal(status, EXIT_FINDINGS)
            self.check_equal(path.read_text(encoding="utf-8"), original)

    def test_39_cli_auto_stdout_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.txt"
            original = "It is important to note that the sample was small."
            path.write_text(original, encoding="utf-8")
            args = _build_parser().parse_args([str(path), "--mode", "auto", "--quiet"])
            _validate_cli_arguments(args)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                status = _process_cli(args)
            self.check_equal(status, EXIT_OK)
            self.check_equal(stdout.getvalue(), "The sample was small.")
            self.check_equal(path.read_text(encoding="utf-8"), original)

    def test_40_cli_apply_and_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.txt"
            report = Path(directory) / "report.json"
            path.write_text("It is important to note that the sample was small.", encoding="utf-8")
            args = _build_parser().parse_args(
                [str(path), "--mode", "auto", "--apply", "--report-json", str(report), "--quiet"]
            )
            _validate_cli_arguments(args)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = _process_cli(args)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.check_equal(status, EXIT_OK)
            self.check_equal(path.read_text(encoding="utf-8"), "The sample was small.")
            self.check_equal(payload["tool"], TOOL_NAME)
            self.check_true(payload["idempotent_check"])

    def test_41_list_rules_output(self) -> None:
        table = _rule_table()
        self.check_true(table.startswith("rule_id\tcategory\t"))
        self.check_greater_equal(len(table.splitlines()), 121)

    def test_42_public_api_is_callable(self) -> None:
        self.check_true(callable(analyze_text))
        self.check_true(callable(refine_text))
        self.check_true(callable(render_unified_diff))
        self.check_true(callable(load_author_profile))


def run_self_tests() -> int:
    """Run embedded tests and return EXIT_OK or EXIT_SELF_TEST."""

    global _SELF_TEST_ASSERTIONS
    _SELF_TEST_ASSERTIONS = 0
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(_RefinerSelfTests)
    transcript = io.StringIO()
    result = unittest.TextTestRunner(stream=transcript, verbosity=2).run(suite)
    if result.wasSuccessful():
        print(
            f"SELF-TEST PASS: tests={result.testsRun}; assertions={_SELF_TEST_ASSERTIONS}; "
            f"rules={len(RULES)}; structural_or_procedural={len(STRUCTURAL_RULE_IDS)}"
        )
        return EXIT_OK
    sys.stderr.write(transcript.getvalue())
    print(
        f"SELF-TEST FAIL: tests={result.testsRun}; assertions={_SELF_TEST_ASSERTIONS}; "
        f"failures={len(result.failures)}; errors={len(result.errors)}",
        file=sys.stderr,
    )
    return EXIT_SELF_TEST


if __name__ == "__main__":
    raise SystemExit(main())
