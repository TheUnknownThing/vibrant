Vibrant is a vibe coding scaffold. Here is what you need to know when working on the project.

- The project uses `uv` to manage dependencies. Always use `uv` to add, remove, or update dependencies. Use `uv run vibrant` to run the project, and `uv run pytest` to run tests. Never use the system's Python or pip.
- Never write code solely for compatibility with older versions of the project. If a feature is stricken from the project, remove it directly. Always keep the codebase clean and up-to-date.
- When breaking changes are introduced, tests may break. Delete testcases which are no longer relevant, and edit testcases where needed. Do not adjust code for compatibility with old testcases.
