-- A picture stays with the AI it was sent to (ai/posture.py for_model). images_for names the AI this conversation's pictures go to
-- (posture.ai_key); images_upto is the last message that existed when the conversation moved to another AI: pictures up to it go out as
-- a line of text, and stay stored until they expire (image_retention.py). A conversation from before this names no AI: its next turn
-- counts as a move, the safe direction (the most it costs is the AI not looking at an old photo again).
ALTER TABLE ai_conversations ADD COLUMN images_for TEXT;
ALTER TABLE ai_conversations ADD COLUMN images_upto BIGINT;
