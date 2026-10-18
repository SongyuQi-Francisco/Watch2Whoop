"""AI assistant: tool registry (tools.py), long-term memory, status cards, quick meal logging, recipes.

Three routes sharing one set of tools and one authorization (docs/ai-assistant-plan.md sections 3 and 9):
- channel=butler: the body butler (the deployer's own Claude Code Remote Control session, local stdio MCP, using their Claude subscription).
- channel=mcp / mcp:<client>: the user in their own official client (Claude Code, Codex CLI, Gemini CLI..., with their own subscription)
  connects to the local stdio MCP or the remote MCP (/mcp, ai/mcp_http.py, each person's own AI token).
- channel=api:<kind>: in-Today-page chat (ai/chat.py), the backend calls Anthropic / OpenAI / Gemini / compatible APIs with this user's own API key.

Rules: the assistant always represents a logged-in user and only sees data this user can see (including shares authorization);
write tools are marked writes=True and need user confirmation before running; every call writes assistant_audit.
"""
