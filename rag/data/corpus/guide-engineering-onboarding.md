---
title: Engineering onboarding
source: notion
space: engineering
team: platform
doc_type: guide
updated: 2026-05-18
tags: [onboarding, newjoiner, setup, laptop, access]
---

# Engineering onboarding

Welcome. Your first week has one goal: ship something small to production. Not a
big thing. A copy change, a metric, a test. The point is to walk the whole path
once while someone is sitting next to you.

## Day one

* Collect your laptop from IT. Full-disk encryption and the MDM profile are
  mandatory and are checked nightly.
* Request access through the access portal, not by messaging people. Your
  manager approves; platform grants. Default access is read-only in production.
* Run `./scripts/bootstrap.sh` in the `platform` repo. It installs the toolchain,
  configures the internal module proxy, and logs you into the registry.

## Week one

1. Read the incident severity page and the code review guidelines.
2. Pair with your onboarding buddy on a real ticket from the `good-first-issue`
   label.
3. Shadow one on-call handover. You will not be on the rotation for at least six
   weeks and never alone before your third month.
4. Get your production read access and, once you have shipped twice, deploy
   rights.

## Environments

`local` uses docker compose. `staging` mirrors production topology at a fifth of
the capacity and holds synthetic data only — never copy production data into it,
which is both a policy violation and a GDPR problem.

## Asking for help

Ask in the team channel after fifteen minutes of being stuck. There is no prize
for silent struggling, and the person who answers you learned it from someone
too.
