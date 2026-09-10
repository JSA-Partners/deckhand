---
name: coverage
always: true
signals: [task, step, test, acceptance]
---

# Coverage

Map every acceptance criterion to the task that makes it true and the step that proves it. Then read the plan backwards: every task must serve a criterion, every step must show its code and its expected result, and every name used in a later task must be defined in an earlier one.

A finding is one of: a criterion with no task; a task with no criterion; a step that says what to do without showing how; a placeholder (TBD, TODO, "handle edge cases", "similar to Task N"); a type, function, or file named in one task and spelled differently in another.

Evidence is the criterion text and the task number, or the two mismatched names with their task numbers.

Nothing found is a valid result. Say so in one line.
