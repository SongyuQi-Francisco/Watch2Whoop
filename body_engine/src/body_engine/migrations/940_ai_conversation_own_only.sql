-- In-page chat token economy (issue #78, ai/chat.py): a conversation started by someone nobody shares data with sends the
-- tools without their `user` parameter (it could only ever be "me"; about 1k tokens per call on the Today page). Fixed when
-- the conversation starts, so the tool definitions never change mid-conversation (prompt cache, Claude's preserved
-- thinking); older conversations keep the parameter (false). Not health data: no grant to grafana_ro.
ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS own_only BOOLEAN NOT NULL DEFAULT false;
