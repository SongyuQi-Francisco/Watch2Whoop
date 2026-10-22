-- Cards in the in-page chat (issue #53, rollout flag jarvis_rich, ai/cards.py): the card a show_card call or an
-- automatic card (cards.AUTO, after log_food / log_workout / log_weight ...) showed, kept so a reopened conversation draws it again.
ALTER TABLE ai_tool_calls ADD COLUMN IF NOT EXISTS card JSONB;
