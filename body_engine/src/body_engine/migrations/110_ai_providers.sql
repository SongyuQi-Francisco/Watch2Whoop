-- Multi-user, multi-provider AI integration layer (docs/ai-assistant-plan.md §9). Adds tables and columns only, no change to existing data.
-- Each user uses their own AI source, with no cross-over: configuration, keys, conversations and usage are all isolated by user_id; family sharing (shares) does not include these tables.
-- No grant to grafana_ro (they hold encrypted API keys and conversation content).

-- Provider configuration: each person can have several, at most one default.
-- kind: subscription type (only via the user's own official client + remote MCP, never called by the backend) claude_plan_mcp / codex_plan_mcp / generic_mcp_client;
--       API type (the backend calls with the user's own key) anthropic_api / openai_api / gemini_api / openai_compatible.
-- encrypted_secret: AES-256-GCM (master key in secrets/ai_master_key, associated data bound to user_id); no endpoint returns the plaintext, only the last 4 characters.
CREATE TABLE ai_providers (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('claude_plan_mcp', 'codex_plan_mcp', 'generic_mcp_client',
                                     'anthropic_api', 'openai_api', 'gemini_api', 'openai_compatible')),
  display_name TEXT NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 60),
  model TEXT,                               -- required for API types; empty for subscription types (the model is chosen in the user's own client)
  base_url TEXT,                            -- used only by openai_compatible (DeepSeek, Qwen, Ollama...)
  encrypted_secret BYTEA,                   -- API key ciphertext; empty for subscription types
  secret_last4 TEXT,                        -- used only for display "...abcd"
  monthly_budget_usd NUMERIC(10, 2) NOT NULL DEFAULT 5 CHECK (monthly_budget_usd >= 0),
  price_in_per_mtok NUMERIC(10, 4),         -- custom unit price (USD per million tokens); empty = use the built-in price table
  price_out_per_mtok NUMERIC(10, 4),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
  is_default BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (kind <> 'openai_compatible' OR base_url IS NOT NULL)
);
CREATE INDEX ai_providers_user_idx ON ai_providers (user_id);
CREATE UNIQUE INDEX ai_providers_default_uq ON ai_providers (user_id) WHERE is_default;

-- Conversations inside the Today page (API-type providers). system_prompt is fixed when the conversation starts and never changed (prompt caching and Claude's preserved thinking require append-only history).
CREATE TABLE ai_conversations (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider_id INT REFERENCES ai_providers(id) ON DELETE SET NULL,
  title TEXT,
  system_prompt TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'idle' CHECK (state IN ('idle', 'awaiting_confirmation')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ai_conversations_user_idx ON ai_conversations (user_id, updated_at);

-- Messages: content is a provider-neutral format (text / image / tool_call / tool_result blocks);
-- native is the provider's reply as-is (Claude thinking blocks, Gemini thought_signature, OpenAI reasoning items),
-- passed back unchanged when continuing with the same provider + model; after switching provider, content is used.
CREATE TABLE ai_messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
  content JSONB NOT NULL,
  native JSONB,
  native_key TEXT,                          -- "kind:model", decides whether native can be passed back unchanged
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ai_messages_conv_idx ON ai_messages (conversation_id, id);

-- Tools the model wants to call: read tools run directly; write tools go pending first, and the user confirms on the Today page (approved -> done / error) or rejects (rejected).
CREATE TABLE ai_tool_calls (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
  message_id BIGINT NOT NULL REFERENCES ai_messages(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  call_id TEXT NOT NULL,                    -- tool_use / function_call id given by the provider
  tool TEXT NOT NULL,
  args JSONB NOT NULL,
  writes BOOLEAN NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'error', 'rejected')),
  result TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE INDEX ai_tool_calls_msg_idx ON ai_tool_calls (message_id);
CREATE INDEX ai_tool_calls_pending_idx ON ai_tool_calls (user_id) WHERE status = 'pending';

-- Token usage and estimated cost of each model call (by ai_pricing or the custom unit price on the provider), used to enforce the monthly budget.
CREATE TABLE ai_usage (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider_id INT REFERENCES ai_providers(id) ON DELETE SET NULL,
  conversation_id BIGINT REFERENCES ai_conversations(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  cache_read_tokens INT NOT NULL DEFAULT 0,
  cache_write_tokens INT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
  at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ai_usage_user_idx ON ai_usage (user_id, at);

-- Audit also records provider and model (both columns empty for the MCP channel; channel records mcp:<client> / api:<kind>)
ALTER TABLE assistant_audit ADD COLUMN provider_id INT, ADD COLUMN model TEXT;
