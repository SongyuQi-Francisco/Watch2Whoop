-- In-page chat context management (issue #53, ai/context.py): earlier turns are folded into a summary the model sees
-- instead of the original messages; the user still sees every message. summary_upto = id of the last ai_messages row the
-- summary covers (the model gets the summary + the messages after it).
ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS summary TEXT,
  ADD COLUMN IF NOT EXISTS summary_upto BIGINT,
  ADD COLUMN IF NOT EXISTS summary_at TIMESTAMPTZ;
-- What a model call was for: 'chat' (answering the user) or 'compact' (folding history into the summary). Both count
-- against the monthly budget / fallback quota.
ALTER TABLE ai_usage ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'chat'
  CHECK (purpose IN ('chat', 'compact'));
