---
name: pen-test
signals: [auth, token, secret, password, key, upload, sql, query, redirect, url, cookie, header, cors, admin, export, csv, pdf, html]
---

# Pen test

Walk every interface the artifact names as a tester with a checklist and no manners: what goes in, where it is trusted, where it comes out. Look for trust that skips a boundary: input used before it is bounded, output that carries something it should not, identity read from the wrong place, a limit that exists in prose but not in the design.

A finding names the interface, the input, and the effect, in a form someone could reproduce. For a plan, name the task where the check belongs and is absent.

Evidence is the artifact text that omits the control, or the `file:line` where a similar control already lives and is not reused.

Nothing found is a valid result. Say so in one line.
