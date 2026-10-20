-- Google AI plan integration (docs/ai-assistant-plan.md section 9):
-- 1. gemini_plan_mcp: subscription type (the user's own Gemini CLI / Antigravity connects to Body Engine's remote /mcp);
-- 2. gemini_cli: the deployer's server-side Gemini CLI (signed in with a Google AI plan account) used for in-page chat.
ALTER TABLE ai_providers DROP CONSTRAINT IF EXISTS ai_providers_kind_check;
ALTER TABLE ai_providers ADD CONSTRAINT ai_providers_kind_check
  CHECK (kind IN ('claude_plan_mcp', 'codex_plan_mcp', 'gemini_plan_mcp', 'generic_mcp_client',
                  'anthropic_api', 'openai_api', 'gemini_api', 'gemini_cli', 'openai_compatible'));
