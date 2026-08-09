---
title: Postmortem - Search served an empty index after a rebuild
source: confluence
space: engineering
team: platform
doc_type: postmortem
updated: 2026-04-07
tags: [postmortem, index-alias-flip, sev2]
---

# Postmortem: Search served an empty index after a rebuild (2026-04-07)

**Severity:** SEV2
**Duration:** 45m
**Customer impact:** help centre search returned no results

## Summary

An index rebuild flipped the serving alias before the new index had finished ingesting, so search served an index containing 2 percent of the content.

## Root cause

The flip was triggered on the ingest job exiting successfully, and the job exits when it has submitted the last batch, not when the batch is searchable.

## What we changed

1. Flip is now gated on a document count within 1 percent of the source of truth.
2. A post-flip smoke query set runs against the new alias before the old index is deleted.
3. The old index is retained for 24 hours so a bad flip is one command to undo.

## Lesson

'The job succeeded' and 'the work is done' are different claims. Verify the outcome, not the exit code.
