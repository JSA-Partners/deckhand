---
name: document
description: Guide for writing project reference documents -- file types, structure, and quality criteria
---

# Documentation Skill

Write reference files that serve as context material for Claude. The default location is `docs/claude/`, but the calling skill may resolve to an alternative directory. These are topic-based reference documents, not changelogs or journals.

## Core Principle

Every reference file should read like documentation a senior engineer wrote for a new team member who has access to the code but needs context the code does not provide.

## File Types

| Type | Purpose | Example file |
| --- | --- | --- |
| Architecture | System structure, data flow, component relationships | `architecture.md` |
| Patterns | Step-by-step guides for recurring tasks (adding routes, forms, endpoints) | `patterns.md` |
| Decisions | Key architectural choices with rationale and rejected alternatives | `decisions.md` |
| Testing | Test infrastructure, factories, assertion helpers, how to run tests | `testing.md` |
| Code Style | Formatting rules enforced by linters, naming conventions | `code-style.md` |
| Domain Models | Entity relationships, business rules, constraints | `domain-models.md` |

Not every project needs all types. Create files as topics emerge.

## File Structure

Every file follows the same shape:

```markdown
# [Topic]

[One sentence describing what this document covers.]

## [Section]

[Explanation of the concept, pattern, or decision.]

[Code examples, tables, or diagrams as needed.]
```

### Key rules

- **One file per topic area** -- not per session, not per date
- **No date headers** -- content is organized by concept, not chronology
- **Integrate, do not append** -- new learnings merge into the relevant section
- **Cross-reference** -- link between reference files with relative paths (`[patterns.md](./patterns.md)`)
- **Another repository** -- link a file there by its URL; a backticked path is read as one in this repository

## Content Patterns

### Architecture docs

- Start with directory tree showing core structure
- Include data flow diagrams (text-based)
- Document the layers and their responsibilities
- Show construction and access patterns with code examples

### Decision docs

Each decision follows a consistent format:

```markdown
## [Decision Title]

**Decision**: [What was decided.]

**Rationale**:

- [Why this, not that]
- [Constraints that drove the choice]

**Example**:

[Code showing the pattern in action.]
```

### Pattern docs (step-by-step guides)

- Start with a table of contents linking to sections
- Each section is a self-contained recipe
- Include code examples showing the complete pattern
- Use tables for mapping concepts (routes, methods, file locations)

### Testing docs

- How to run tests (exact commands)
- Test infrastructure (server setup, factories, helpers)
- Assertion patterns with code examples
- Constants and utilities available

### Code style docs

- Rule, then good/bad code examples
- Note what enforces the rule (linter name, config)
- Explain why the rule exists

## What Belongs in Reference Docs

- Architecture and data flow that is not obvious from file structure alone
- Rationale behind non-obvious decisions (especially rejected alternatives)
- Patterns with enough boilerplate that Claude needs a template
- Testing infrastructure and helpers
- Linter-enforced style rules
- Domain model relationships and business constraints

## What Does NOT Belong

- Anything derivable from reading the code directly
- Git history or recent changes (use `git log`)
- Debugging solutions (the fix is in the code, the context is in the commit)
- Ephemeral task details or conversation context
- Content already in CLAUDE.md
- API documentation (use OpenAPI specs)

## Quality Checklist

- [ ] Reads as a coherent reference document, not a changelog
- [ ] No date-stamped sections
- [ ] Code examples are complete and copy-pasteable
- [ ] Tables used for mappings and comparisons
- [ ] Cross-references to related reference files
- [ ] No duplication with other reference files
- [ ] Content would genuinely help Claude work in this codebase
