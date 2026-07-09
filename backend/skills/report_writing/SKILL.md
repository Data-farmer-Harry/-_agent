---
id: report_writing
name: 科研任务报告生成
description: Turn run context, artifacts, tool results, and traces into concise reproducible Markdown reports.
triggers:
  - 报告
  - 实验记录
  - 总结成 markdown
  - 生成markdown
  - report
  - write-up
preferred_tools:
  - report.generate
  - workspace.search
  - file.read
  - data.profile
  - physics.check
---

When this skill is active, write reports as reproducible research records rather than polished marketing copy.

A useful report should include:

1. User request and run id.
2. Inputs and assumptions.
3. Tool/runtime chain with trace-grounded evidence.
4. Key outputs and artifact links.
5. Validation, warnings, and limitations.
6. Next recommended checks.

Do not hide failures. If a tool failed or evidence is missing, state it plainly. Do not claim external search, real LAMMPS execution, or full-text reading unless those actions appear in the trace.
