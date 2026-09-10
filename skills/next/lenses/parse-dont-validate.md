---
name: parse-dont-validate
signals: [validate, validation, parse, input, form, schema, type, enum, optional, nullable, string, id, slug, date, query, param]
---

# Parse, don't validate

A parser turns less-structured input into more-structured output, once, at the boundary. Find the places where the story lets a value into the system as something weaker than what the system needs, then checks it later, or never: a date carried as a string, an id that might not exist, an enum that is really any text, an optional that is required by the third call.

Two smells give it away. Shotgun parsing: checks on the same value scattered through the code instead of one parse where it enters. Illegal states left representable: a type that admits values the system can never handle, so every consumer defends against them again.

A finding names the value, where it enters, the shape it should have at that point, and where the missing shape bites. For a plan, name the task that should construct the stronger type at the boundary.

Evidence is the interface line or plan step, or the `file:line` of the boundary in the code.

Nothing found is a valid result. Say so in one line.

Source: Alexis King, "Parse, don't validate" (2019).
