-- The AI-first page (#279, flag ui_ai_first, docs/ui-ai-first-redesign.md v0.4): what the user told us and chose.
-- ui_state: the focus picked in the first screen (or later, by the user or their AI) and when the body overview was seen.
-- ui_modules: the user's own choices for the cards on Today (pinned in their order, or taken off); everything else is worked
-- out when the page asks (focus, findings in the data, what the AI used), so nothing here goes stale.
CREATE TABLE ui_state (
  user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  focus TEXT[] NOT NULL DEFAULT '{}',
  overview_seen_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ui_modules (
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  module_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pinned', 'hidden')),
  pin_order INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, module_key)
);
