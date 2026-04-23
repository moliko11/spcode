# File Edit Skill

Use this skill when the agent needs to read, modify, or write files in the workspace.

## When to Use
- The user asks to create, update, or delete a file
- The user wants to add a function, fix a bug, or refactor code in an existing file
- The user asks to rename or move content within a file

## Steps
1. **Read first** – call `FileReadTool` with `{"path": "<file>"}` to understand existing content
2. **Plan the edit** – identify exact lines/sections to change; avoid touching unrelated code
3. **Apply the edit** – call `FileEditTool` with `{"path": "<file>", "old_string": "...", "new_string": "..."}`
4. **Verify** – re-read the modified section with `FileReadTool` to confirm the change is correct

## Notes
- Never overwrite the whole file when only a small section needs to change
- Use `GlobTool` to locate files if the path is unknown
- Use `GrepTool` to search for the exact string before constructing `old_string`
- For new files use `FileWriteTool` instead of `FileEditTool`
