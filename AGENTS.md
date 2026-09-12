# AGENTS.md

## Code-writing instructions

- Use Python only, relying exclusively on the standard library. Do not add third-party dependencies.
- Prefer putting all production code in `cod.py`. The primary deliverable should remain a single file that end users can download and run directly.
- Keep the implementation as small as possible. Favor direct, readable code and simple control flow in the spirit of George Hotz.
- Prefer one source of truth. Avoid duplicated logic, configuration, and parallel implementations.
- Keep the core small and make the code beautiful: clear names, tight interfaces, and behavior that is easy to follow.
- Do not introduce abstractions unless there is a genuine present-day need. Avoid layers, frameworks, wrappers, and indirection that do not clearly simplify the current code.
- Do not build for hypothetical future requirements. Optimize for making the current code as relevant, efficient, and understandable as possible.
- A larger rewrite or a substantial abstraction is acceptable when a genuinely new idea requires it; do not preserve a weak structure merely to minimize the diff.
- Other files and modules may be created only when they serve development purposes or are entirely optional to the end user. Keep the standalone `cod.py` path intact.
- Do not create speculative plans or roadmap code. Implement the current requirement, verify it, and leave the repository in a working state.
- Before changing code, understand the existing behavior and preserve it unless the task explicitly requires a change.
- Use standard-library tooling for validation and tests. Keep tests focused on meaningful behavior and avoid test-only architecture in production code.

