"""The eight columns a story moves through, in order, and the one place each name is written.

`Status` carries the whole pipeline and deckhand is its only writer, so a name spelled by hand in a
step would be a second copy of the project's own option list to keep in step with it. Every step
names its column from here, and `checklist` verifies the project offers exactly these, in this order.
"""

from __future__ import annotations

DRAFT = "Draft"
REFINEMENT = "Refinement"
READY = "Ready"
BACKLOG = "Backlog"
IN_PROGRESS = "In Progress"
IN_REVIEW = "In Review"
VERIFICATION = "Verification"
DONE = "Done"

# The order a story moves through them, which is the order the project offers them in.
ORDER = (DRAFT, REFINEMENT, READY, BACKLOG, IN_PROGRESS, IN_REVIEW, VERIFICATION, DONE)
