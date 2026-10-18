-- Which AI Jarvis uses (ai/choice.py): the user's pick between their own default provider, the fallback AI and (deployer only) the body butler.
-- No row = automatic. A pick that cannot be used (provider disabled / deleted / all AI tokens revoked) falls through to the next source.
CREATE TABLE ai_choice (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  source TEXT NOT NULL CHECK (source IN ('provider', 'fallback', 'butler')),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
