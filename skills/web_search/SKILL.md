# Web Search Skill

Use this skill when the agent needs to look up current information from the internet.

## When to Use
- The user asks about recent news, prices, or events
- The user needs facts that may have changed after the model's training cutoff
- The user asks "what is the latest …" or "find information about …"

## Steps
1. Call `WebSearchTool` with `{"query": "<search terms>", "max_results": 5}`
2. Review the returned snippets and decide if any result is relevant
3. If more detail is needed, call `WebFetchTool` with the URL of the most relevant result
4. Summarise the findings and present them to the user

## Notes
- Prefer concise, targeted queries (5–10 words)
- If the first search returns irrelevant results, refine the query and retry once
- Always cite the source URL when presenting web-sourced facts
