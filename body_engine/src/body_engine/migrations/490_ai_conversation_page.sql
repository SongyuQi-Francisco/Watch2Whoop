-- In-page chat: the Today pages a conversation has been on, most recent last (issue #99). Their tool groups
-- (ai/params.PAGE_GROUPS) are sent with every model call of this conversation besides the resident core. A new page adds
-- its groups (the cached prompt prefix changes that one round, then is stable again); beyond PAGE_BUDGET_CHARS the oldest
-- pages are dropped (assistant/toolsets.visit_page). Only known page ids are kept. Empty = core only (older conversations,
-- clients that send no page).
ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS pages TEXT[] NOT NULL DEFAULT '{}';
