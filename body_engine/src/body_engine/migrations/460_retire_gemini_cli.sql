-- Retire the server-side Gemini CLI provider (kind gemini_cli, added in 225_gemini_plan.sql): since 2026-06-18 Google no longer
-- lets AI Pro / Ultra plans sign in to Gemini CLI, so it could not work any more. The adapter and the catalog entry are gone.
-- Existing rows are kept (no key is stored for this kind) but turned off and no longer the default: the AI settings page shows
-- them as 「已下线」 with only a delete button, and Jarvis falls back by the usual rule (ai/choice.effective: an unusable default
-- provider -> the butler link for the deployer, else the backup AI).
-- The kind stays in the CHECK constraint on purpose: dropping it would require deleting these rows (and their usage history);
-- new rows can't be created because the kind is no longer in ai/catalog.KINDS.
UPDATE ai_providers SET status = 'disabled', is_default = false, updated_at = now()
WHERE kind = 'gemini_cli' AND (status <> 'disabled' OR is_default);
