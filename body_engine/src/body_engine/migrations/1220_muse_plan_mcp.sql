-- Meta Muse as a subscription AI source (docs/ai-assistant-plan.md 9.24, ai/catalog.py muse_plan_mcp): the user adds
-- Body Engine's /mcp address as a custom connector in Muse and approves it on the consent page (OAuth, ai/oauth.py).
-- Like the other *_plan_mcp kinds the server stores nothing for it and never calls it; the row only records which AI the
-- user uses. The constraint keeps every earlier kind, the retired gemini_cli included (460_retire_gemini_cli.sql).
ALTER TABLE ai_providers DROP CONSTRAINT IF EXISTS ai_providers_kind_check;
ALTER TABLE ai_providers ADD CONSTRAINT ai_providers_kind_check
  CHECK (kind IN ('claude_plan_mcp', 'codex_plan_mcp', 'gemini_plan_mcp', 'muse_plan_mcp', 'generic_mcp_client',
                  'anthropic_api', 'openai_api', 'gemini_api', 'gemini_cli', 'openai_compatible'));
