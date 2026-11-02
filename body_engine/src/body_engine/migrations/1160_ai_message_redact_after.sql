-- Posture photos (issue #174, docs/posture-photos-plan.md section 4.1): a user message whose pictures must not stay on the server.
-- redact_after is set when the message is stored and cleared when its pictures are replaced by a placeholder (ai/posture.py):
-- normally when the turn ends; the hourly job replaces whatever is still here after this time (a crash, a confirmation never
-- answered). NULL = nothing to replace, which is every other message: ordinary chat photos are not touched by this.
ALTER TABLE ai_messages ADD COLUMN redact_after TIMESTAMPTZ;
CREATE INDEX ai_messages_redact_after_idx ON ai_messages (redact_after) WHERE redact_after IS NOT NULL;
