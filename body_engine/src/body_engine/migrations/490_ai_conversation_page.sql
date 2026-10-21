-- In-page chat: the Today pages a conversation has been on (issue #99). Their tool groups (ai/params.PAGE_GROUPS) are sent
-- with every model call of this conversation besides the resident core. Only added to, never removed: going to a new page
-- adds its groups once (the cached prompt prefix changes that one round, then is stable again); going back changes nothing.
-- Empty = core only (older conversations, clients that send no page).
ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS pages TEXT[] NOT NULL DEFAULT '{}';
