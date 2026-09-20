# Prompt rubric

Reusable review support for complex, repeatable, or consequential prompts.

Project-specific governing files take priority. This rubric does not redefine project behavior, source authority, methodology, voice, commercial claims, or workflow.

Use it to judge whether a prompt gives an AI enough structure to complete the right task, using the right evidence, in the right form, without manufacturing certainty or completion.

---

## When to use this rubric

Use it for prompts that are:

* Repeated across multiple people, projects, or models
* Built around several source files
* Used for audits, research, qualification, analysis, or decisions
* Vulnerable to shallow compliance
* Expected to produce a reusable artifact
* High enough stakes that unsupported claims matter

Do not require a scored rubric for:

* Casual questions
* Small edits
* Straightforward transformations
* Early exploratory thinking
* Tasks where the user is still discovering the question

Prompt structure should serve the work. It should not become ceremony.

---

## Core principle

A strong prompt creates a bounded working environment.

It tells the model:

* What work it is doing
* Why the result matters
* Which evidence is valid
* What must and must not be covered
* What the output must look like
* What quality means
* How uncertainty should be handled
* What to do when the task cannot be fully completed

A prompt is not strong because it is long. It is strong when another competent person or model could use it to produce a comparable result and explain why that result passes or fails.

---

## Scoring rubric

Score each category against the prompt as written.

### 1. Task clarity — 10 points

Does the prompt identify the exact operation and intended artifact?

A strong prompt distinguishes among tasks such as:

* Audit
* Summarize
* Extract
* Compare
* Diagnose
* Classify
* Draft
* Rewrite
* Plan
* Validate
* Research
* Refactor

**Full credit:** The task class, object of work, and required artifact are unmistakable.

---

### 2. Purpose and downstream use — 8 points

Does the prompt explain why the result exists?

Look for:

* Who will use it
* What decision it supports
* What action should become easier
* What practical job the output must perform

**Full credit:** Relevance can be judged against a clear downstream use.

---

### 3. Audience — 6 points

Does the prompt identify the reader or user?

Look for:

* Expertise level
* Domain familiarity
* Vocabulary expectations
* Desired density
* Need for actionability
* Need for evidence or citations

**Full credit:** The audience definition meaningfully controls the output.

---

### 4. Source and evidence policy — 12 points

Does the prompt define what evidence is valid?

Look for:

* Allowed sources
* Forbidden sources
* Source hierarchy
* Whether outside research is allowed
* Whether inference is allowed
* How unsupported claims should be treated
* Whether citations or exact quotations are required

**Full credit:** The model cannot easily turn assumptions into facts.

---

### 5. Scope and non-goals — 10 points

Does the prompt define both what must be covered and what should not be done?

Strong non-goals prevent plausible but unwanted work, including:

* Generic best practices
* File-by-file inventories
* Unrequested rewriting
* Marketing copy
* Implementation before diagnosis
* Invented requirements
* Excessive simplification
* Unnecessary research

**Full credit:** Both the target and its boundaries are clear.

---

### 6. Definitions and domain precision — 6 points

Are important terms defined operationally?

Terms that often require definition include:

* Evidence
* Risk
* Done
* Validated
* Qualified
* Current
* Canonical
* Actionable
* Customer
* Trigger
* Audit

**Full credit:** Important terms cannot silently change meaning during execution.

---

### 7. Method and review discipline — 8 points

Does the prompt provide a visible work procedure?

Useful method instructions may require the model to:

1. Identify source authority
2. Extract relevant facts
3. Separate evidence from inference
4. Check contradictions
5. Evaluate against stated criteria
6. Produce the required artifact
7. Run a final review

Do not ask for private chain-of-thought. Ask for visible evidence, criteria, checks, and conclusions.

**Full credit:** The method makes the result inspectable without requiring hidden reasoning.

---

### 8. Output contract — 12 points

Does the prompt define the shape of the final result?

This may include:

* Required sections
* Tables
* Labels
* Status fields
* Length boundaries
* Citation placement
* JSON schema
* File format
* PASS, PARTIAL, FAIL, or BLOCKED status

**Full credit:** The output is predictable enough to review, reuse, compare, or parse.

---

### 9. Quality and acceptance criteria — 10 points

Does the prompt define what a successful result must accomplish?

Strong acceptance criteria are observable.

Examples:

* Every project-specific claim is sourced
* Every required section is present
* Recommendations include rationale and validation
* Unknowns are not presented as facts
* The reader can take the next action without interpretation
* No prohibited claims appear

**Full credit:** Passing and failing can be judged by more than tone or instinct.

---

### 10. Protection against shallow execution — 6 points

Could the model satisfy the prompt with a plausible surface-level answer?

Useful protections include requiring:

* Mechanisms, not abstractions
* Evidence for each conclusion
* Examples
* Counterarguments
* Failure modes
* Tradeoffs
* Acceptance criteria
* Required coverage for each unit of analysis

**Full credit:** Shallow compliance would visibly fail.

---

### 11. Failure and uncertainty handling — 8 points

Does the prompt tell the model what to do when evidence is missing, contradictory, stale, or inaccessible?

Useful states include:

* Confirmed
* Inferred
* Unknown
* Contradictory
* Partial
* Blocked
* Not established

A strong prompt does not permit the model to fill gaps merely to complete the requested format.

**Full credit:** The model knows how to fail honestly.

---

### 12. Examples and calibration — 4 points

Does the prompt provide useful examples of success or failure?

The strongest examples are annotated:

* Good example
* Bad example
* Why one passes
* Why the other fails

Examples should calibrate the quality that matters, not merely formatting or length.

**Full credit:** The examples make the intended standard easier to reproduce.

---

## Score interpretation

```text
90–100  Strong execution contract
75–89   Reliable prompt with limited gaps
60–74   Functional but vulnerable to drift
40–59   Weak; the model must infer too much
Below 40  Prompt-shaped request, not a dependable specification
```

A high score does not guarantee a correct result. It means the prompt provides a sound environment for performing and evaluating the work.

A lower score is not automatically a problem. Many simple tasks do not need a full execution contract.

---

## Review procedure

When reviewing a prompt:

1. Identify the real task and downstream use.
2. Check whether the stated sources can support the requested result.
3. Find any missing authority, scope, or uncertainty rules.
4. Test whether a shallow answer could technically comply.
5. Check whether success can be evaluated.
6. Recommend the smallest useful improvement.

Do not expand every prompt into the full template. Add only the structure needed to reduce the task’s actual risks.

---

## Common failure patterns

### Long but unbounded

The prompt contains extensive background but does not identify authority, scope, or output.

### Structured but unsupported

The output format is precise, but the allowed evidence is undefined.

### Accurate but irrelevant

The prompt asks for correctness without explaining the decision or action the output supports.

### Detailed but shallow

The prompt requests detail without defining what each unit of analysis must contain.

### Complete by invention

The prompt requires every section to be filled but does not permit honest partial completion.

### Theatrical role prompting

The prompt says “act as a genius” or “act as an expert” without changing priorities, method, or judgment.

### Process overload

The prompt demands the same elaborate structure for every task, including work that would be better handled through direct collaboration.

---

## Compact review checklist

Before treating a prompt as reusable, ask:

* Is the task unmistakable?
* Is the purpose clear?
* Are governing sources named?
* Are scope and non-goals explicit?
* Are important terms stable?
* Is the output shape defined?
* Can shallow compliance pass?
* Are uncertainty and failure handled?
* Can the result be evaluated?
* Is every instruction necessary?

The goal is not the longest prompt.

The goal is the smallest prompt that reliably creates the right working environment.
