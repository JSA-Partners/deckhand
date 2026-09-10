---
name: unknowns
always: true
signals: [assume, probably, should, might, later, existing, current, legacy, unclear, depends]
---

# Unknowns

List what the artifact assumes without saying. Look for verbs without subjects, nouns without definitions, "the existing" anything, numbers with no source, and any step whose success depends on a fact nobody checked. Then look at the repository for the ones you can settle, and settle them.

A finding is an assumption, whether it holds (checked, with `file:line`), fails (checked, with `file:line`), or cannot be checked from here (say what would settle it). Rank the ones that would change the plan above the ones that would change a sentence.

Nothing found is rare for this lens and is still valid. Say so in one line.
