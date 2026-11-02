// Sync ranges and backfill settings, in one place so they can be changed without touching the logic.
//
// Owner's decision 2026-10-26 (from the sync investigation of that day): the first sync reads only the
// last [kFirstSyncDays] days. Reason: the Open Wearables SDK does not upload new data until its first export is complete,
// and a 90-day first export took up to 6 days in the background (one tester), so the Today page looked broken for a
// week. Older history is filled in afterwards ([kBackfillDays] or all history), in the background, after the newest data.

/// Days the first sync reads (Open Wearables SDK, RawSync and the app's own sync engine). Builds may override it with
/// `--dart-define=W2W_SYNC_DAYS_BACK=…` (config.dart kDefaultSyncDaysBack).
const int kFirstSyncDays = 14;

/// The usual backfill target offered by the Today page / the sync page ("近 90 天"); 0 = all history.
const int kBackfillDays = 90;

/// The engine's backfill (Backfill.swift) waits for external power in the background (BGProcessingTask
/// `requiresExternalPower`); false = also on battery while the phone is idle.
const bool kBackfillPowerOnly = false;

/// Seconds the foreground spends on the backfill after each round of new data (new data first, history after).
const double kBackfillPumpSeconds = 20;
