-- In-page chat: the Today page a conversation was started on (issue #99). It picks the tool groups sent with every model
-- call of this conversation besides the resident core (ai/params.PAGE_GROUPS); fixed for the conversation so the cached
-- prompt prefix (tools come first) stays the same. NULL = core only (older conversations, clients that send no page).
ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS page TEXT;
