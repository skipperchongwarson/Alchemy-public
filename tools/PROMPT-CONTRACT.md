# Prompt contract

A lightweight construction template for repeatable, consequential, multi-source, or easily misinterpreted tasks.

Project-specific governing files take priority. This contract helps structure a task. It does not override project briefs, methodology, source authority, voice rules, commercial boundaries, or factual reference files.

Use only the sections the task needs.

---

## When to use a prompt contract

Use this template when:

* Several files or sources may conflict
* The task will be repeated
* Unsupported claims would create risk
* The required output must follow a stable structure
* More than one person or model may run the prompt
* The difference between evidence and inference matters
* A shallow but plausible answer would not be useful

For ordinary questions, small revisions, and exploratory collaboration, direct instructions are usually enough.

---

# Compact prompt contract

```text
TASK

State the exact work to perform and the artifact or decision required.

PURPOSE

Explain who will use the result, what it will support, and what should become easier afterward.

SOURCE AUTHORITY

Name the governing sources in priority order.

State:
- What may be used as evidence
- What is reference only
- Whether outside research is allowed
- Whether inference is allowed
- How conflicts should be handled

CONTEXT

Provide only the background, current state, prior decisions, definitions, and constraints needed for this task.

SCOPE

State what must be covered.

NON-GOALS

State what plausible but unwanted work must not be done.

METHOD

Describe the visible work procedure.

Do not request private chain-of-thought. Require inspectable criteria, evidence, checks, and conclusions instead.

CONSTRAINTS

List hard requirements and forbidden behavior.

When constraints conflict, state the priority order.

OUTPUT CONTRACT

Define the required structure, labels, format, length, citation behavior, or status fields.

EVIDENCE AND UNCERTAINTY

Define how the result should distinguish:
- Confirmed
- Inferred
- Unknown
- Contradictory
- Not established

ACCEPTANCE CHECK

State the observable conditions the result must meet to pass.

FAILURE BEHAVIOR

State what to return if evidence is missing, sources conflict, tools fail, or the task cannot be fully completed.

Require honest partial completion rather than invented completion.
```

---

# Optional sections

Add these only when they materially improve the task.

## Audience

Use when vocabulary, expertise, density, or framing must change for a particular reader.

## Definitions

Use when project terms could drift or be interpreted generically.

## Granularity

Use when “detailed” or “thorough” is not precise enough.

Example:

```text
For each recommendation, include:
- Evidence
- Rationale
- Tradeoff
- Next action
- Validation required
```

## Examples

Use annotated positive and negative examples when abstract instructions are unlikely to calibrate the result.

## Tool policy

Use when the model may need files, search, code execution, tests, connected sources, or other tools.

State:

* Which tools may be used
* When their use is required
* What must actually be verified
* What cannot be claimed without execution
* What to report if the tool fails

## Reproducibility

Use when another run must reconstruct the basis of the answer.

Possible fields:

* Prompt version
* Input snapshot
* Sources inspected
* Sources not inspected
* Tests or validation performed
* Known limitations

---

# Project-specific adapters

These are prompts for constructing a contract, not replacements for governing project files.

## Make adapter

For a substantial Make task, establish:

```text
Task mode:
Critique, develop, draft, rewrite, polish, or review.

Publishing surface:
LinkedIn, newsletter, website, case study, show, email, or another named context.

Audience:
Who will encounter the finished work?

Governing sources:
Use make/project-brief.md for project behavior.
Use make/voice-and-writing.md for style and mechanics.
Use make/content-system.md for workflow and editorial judgment.
Use factual reference files for claims.

Source boundary:
What facts, stories, quotations, examples, and proof may be used?
What must not be invented?

Required artifact:
What should be ready to publish, send, perform, or revise?

Acceptance:
Does it preserve Skipper’s voice?
Does it make only supported claims?
Does it perform the intended job for the intended reader?
```

Do not make every Make interaction formal. The contract is most useful when an artifact will be reused, published, or evaluated against several constraints.

---

## Biz dev adapter

For a substantial Biz dev task, establish:

```text
Motion:
Founder-direct or referral partner.

Decision:
What decision should this work support?

Governing sources:
Use biz-dev/project-brief.md for project behavior.
Use biz-dev/two-motions.md for motion strategy.
Use the relevant customer or investor profile for qualification.
Use htwco/company-profile.md for company identity, offers, pricing, and company-level claim boundaries.
Use biz-dev/outreach-voice-samples.md for calibration only.
Use the Google Sheet for current pipeline status.

Evidence state:
Separate Confirmed, Inferred, and Unknown.

Boundaries:
Do not invent a trigger, diagnosis, relationship, urgency, budget, authority, or willingness to act.

Required result:
Qualification, research recommendation, outreach draft, call preparation, thread review, or next action.

Acceptance:
Does the result identify the correct motion?
Does it preserve evidence discipline?
Does it recommend one credible next movement?
Does it avoid forcing an offer?
```

---

# Final contract check

Before running a complex prompt, verify:

```text
[ ] The task and artifact are explicit.
[ ] The downstream purpose is known.
[ ] Governing sources are named.
[ ] Scope and non-goals are bounded.
[ ] Evidence and inference cannot be confused.
[ ] The output shape is reviewable.
[ ] Success can be tested.
[ ] Honest partial completion is allowed.
[ ] The contract is no longer than the task requires.
```

A prompt contract is successful when it reduces ambiguity without replacing judgment.

Use enough structure to make the work reliable.

Then stop.
