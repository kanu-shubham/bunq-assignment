---
title: Code review guidelines
source: notion
space: engineering
team: platform
doc_type: guide
updated: 2026-04-25
tags: [code-review, pull-request, process, quality]
---

# Code review guidelines

## For authors

Keep pull requests small. Under 400 changed lines is the target; above 1,000 you
should expect a request to split it. Write the description for someone who was
not in the meeting: what changes, why now, and what you considered and rejected.

Self-review before requesting a review. Half the comments you would get are ones
you would have caught reading your own diff.

## For reviewers

Respond within one business day, even if the response is "I will get to this
tomorrow afternoon". A pull request waiting three days costs more than the bug it
might contain.

Distinguish blocking from non-blocking. Prefix optional suggestions with `nit:`
and mean it — a `nit:` must never block a merge.

Review the change, not the person, and not the code you would have written.
"There is a simpler way here" is feedback; "this is over-engineered" is a verdict.

## Approvals

Two approvals for anything touching money movement, authentication, or
migrations; one for everything else. CODEOWNERS enforces the mapping. Approving
your own change is technically possible in an emergency and must be flagged in
the incident channel when it happens.

## What automation owns

Formatting, linting, import ordering, and coverage thresholds are enforced in
CI. Never spend review time on them.
