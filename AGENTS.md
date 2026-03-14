Vibrant is a vibe coding scaffold. Here is what you need to know when working on the project.

- The project uses `uv` to manage dependencies. Always use `uv` to add, remove, or update dependencies. Use `uv run vibrant` to run the project, and `uv run pytest` to run tests. Never use the system's Python or pip.
- Never write code solely for compatibility with older versions of the project. If a feature is stricken from the project, remove it directly. Always keep the codebase clean and up-to-date.
- When breaking changes are introduced, tests may break. Delete testcases which are no longer relevant, and edit testcases where needed. Do not adjust code for compatibility with old testcases.

## Python Code Style

- Favor type-safe Python. New code should use explicit Python 3.11 type annotations for public functions, methods, class attributes, and non-trivial module constants. Prefer precise types, `TypedDict`, `Protocol`, `Literal`, generics, and discriminated models over `Any`, untyped `dict`, or shape-shifting return values.
- Model structured data explicitly. When data crosses module boundaries or represents application state, prefer `dataclass` or `pydantic` models over loose dictionaries and tuples.
- Keep behavior traceable. Pass important IDs and context through function boundaries, logs, and events so a run can be reconstructed without guesswork. Do not swallow exceptions, hide failures behind broad fallback behavior, or emit logs that omit the operation and outcome.
- Make side effects obvious. Favor small functions with clear inputs and outputs, isolate I/O boundaries, and add concise context when raising exceptions so failures can be tied back to the calling operation.
