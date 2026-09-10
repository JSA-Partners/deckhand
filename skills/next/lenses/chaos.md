---
name: chaos
signals: [job, queue, worker, retry, timeout, concurrent, background, schedule, cache, webhook, external, network, migration, batch, poll]
---

# Chaos

State the steady state the story assumes, then vary the real-world events around it. Take each dependency the artifact names or implies and ask what the story does when that dependency is slow, absent, duplicated, reordered, or half-finished: a job that runs twice, a request that arrives during a migration, a cache that outlives the row, a timeout that fires after the write.

A finding is a failure mode the acceptance criteria neither prevent nor acknowledge, with the state it leaves behind and the blast radius, meaning what else breaks. Prefer the ones that corrupt data or strand a user over the ones that merely error.

Evidence is the sentence that makes the failure possible, or the `file:line` that shows the assumption already exists in the code.

Nothing found is a valid result. Say so in one line.

Source: Principles of Chaos Engineering, principlesofchaos.org.
