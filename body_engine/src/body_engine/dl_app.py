"""Watch2Whoop's registration with the datalayer framework (issue #116, docs/datalayer-design.md section 3.2): the one
place that says which AI tools exist, in which order, who may run them and what happens around a call.

Every tool, MCP or in-page chat, goes through here. To add one: write its spec in its own module (a dict of
datalayer/spec.Tool's fields, handler (ctx, args)) and register that list below. The order below is the order models and
MCP clients get (tests/test_datalayer_snapshots.py), so a new list goes at the end of the tools. Handlers that still
have the old signature are registered with legacy=True.

A tool that destroys data says confirm=True on its own spec: that is the whole list of what asks first (APP.confirm;
the butler's assistant/.claude/settings.json has to name the same tools, tests/test_butler.py). A module that writes a
kind of write-log row registers how to put it back in its register_rollbacks."""
from . import auth
from .ai import fallback_params, params
from .ai.datalayer_texts import MESSAGES
from .assistant import briefing, food_combine, memory, memory_review, sparky_write, writelog
from .assistant.butler_tools import TOOL_SPECS as BUTLER_TOOL_SPECS
from .assistant.diet_profile_tools import CONSTRAINT_TOOL_SPECS, TOOL_SPECS as DIET_PROFILE_TOOL_SPECS
from .assistant.health_tools import TOOL_SPECS as HEALTH_TOOL_SPECS
from .assistant.scene_tools import TOOL_SPECS as SCENE_TOOL_SPECS
from .assistant.sparky_tools import SPARKY_TOOLS
from .cardio.tools import TOOL_SPECS as CARDIO_TOOL_SPECS
from .datalayer import toolsets as ts
from .datalayer.audit import PgAudit
from .datalayer.registry import Registry
from .datalayer.views import View
from .feedback.tools import TOOL_SPECS as FEEDBACK_TOOL_SPECS
from .friends.tools import TOOL_SPECS as FRIENDS_TOOL_SPECS
from .goals.tools import TOOL_SPECS as GOAL_TOOL_SPECS
from .grocery import pantry_auto
from .grocery.tools import TOOL_SPECS as GROCERY_TOOL_SPECS
from .i18n import use_lang
from .insights.tools import TOOL_SPECS as INSIGHT_TOOL_SPECS
from .records import lab_catalog
from .records.tools import TOOL_SPECS as RECORD_TOOL_SPECS
from .rules.tools import TOOL_SPECS as RULE_TOOL_SPECS
from .toolspec import OPTIONAL_DEFAULTS
from .training import manual as manual_workouts, overrides
from .training.tools import TOOL_SPECS as TRAINING_TOOL_SPECS
from .ui.tools import TOOL_SPECS as UI_TOOL_SPECS
from .water import store as water_store
from .water.tools import TOOL_SPECS as WATER_TOOL_SPECS

reg = Registry(MESSAGES)

reg.tools(HEALTH_TOOL_SPECS)
reg.tools(SPARKY_TOOLS, legacy=True)
reg.tools(RECORD_TOOL_SPECS, legacy=True)  # health profile: supplements, lab results, journal, events, exercise rules
reg.tools(RULE_TOOL_SPECS, legacy=True)  # rule reminders
reg.tools(GOAL_TOOL_SPECS, legacy=True)  # bedtime goal
reg.tools(TRAINING_TOOL_SPECS, legacy=True)  # training calendar (watch + SparkyFitness merged)
reg.tools(INSIGHT_TOOL_SPECS, legacy=True)  # WHOOP-style analysis: body age, stress, coach, reports
reg.tools(CARDIO_TOOL_SPECS, legacy=True)  # heart rate recovery, recovery index, ECG summaries
reg.tools(BUTLER_TOOL_SPECS, legacy=True)  # body butler: write SparkyFitness, screenshot metrics, long-term memory
reg.tools(GROCERY_TOOL_SPECS, legacy=True)  # grocery: product library, weekly shopping list, pantry
reg.tools(DIET_PROFILE_TOOL_SPECS, legacy=True)  # 「我的饮食档案」: targets rationale, constraints, day plan, versions
reg.tools(CONSTRAINT_TOOL_SPECS, legacy=True)  # per-user diet constraints (nutrition/constraints.py, issue #76)
reg.tools(FRIENDS_TOOL_SPECS, legacy=True)  # Friends page: circle members' shared status and leaderboard ranks
reg.tools(WATER_TOOL_SPECS, legacy=True)  # daily water intake (issue #125)
reg.tools(UI_TOOL_SPECS, legacy=True)  # the AI-first page: my Today and the focus (ui/, flag ui_ai_first, #279)
reg.tools(FEEDBACK_TOOL_SPECS, legacy=True)  # root rules: bugs and feature requests to the developers
reg.tools(SCENE_TOOL_SPECS, legacy=True)  # scene context tools + playbooks (issue #100)

for module in (sparky_write, food_combine, overrides, manual_workouts, lab_catalog, water_store, pantry_auto, memory, memory_review):
    module.register_rollbacks(reg)

# What the fallback AI reaches (ai/fallback.py): its whitelist, or with the rollout flag ai_parity everything but what its
# consent keeps from DeepSeek. Always the caller's own data, never through shares.
reg.view("fallback", View(tools=fallback_params.TOOL_NAMES))
reg.view("fallback_parity", View(exclude=fallback_params.CONSENT_EXCLUDED))
# A conversation of someone nobody shares data with (ai_conversations.own_only, issue #78): the `user` parameter is only
# hidden to save tokens.
reg.view("own_only", View(force_self=False))

META_TOOLS = ts.meta_tools(params.GROUPS, MESSAGES)
AUDIT = PgAudit("assistant_audit", extra_columns=("provider_id", "model"))   # the two columns migration 110 added (in-page chat)


class _Policy:
    def can_view(self, conn, viewer_id, owner_id, scope):
        return auth.can_view(conn, viewer_id, owner_id, scope)   # shares + circles (looked up at call time)


def _toolset(confirm: frozenset) -> ts.ToolsetConfig:
    """Read from ai/params on every call, so that stays the single place to change the core list and the groups."""
    return ts.ToolsetConfig(core=params.CORE_TOOLS, groups=params.GROUPS, contexts=params.PAGE_GROUPS,
                            confirm=confirm, meta=META_TOOLS, find_max=params.FIND_TOOLS_MAX,
                            messages=MESSAGES, optional_defaults=OPTIONAL_DEFAULTS)


APP = reg.freeze(policy=_Policy(), audit=AUDIT, writelog=writelog.LOG, toolset=_toolset,
                 call_context=lambda: use_lang("zh"),   # AI-facing texts stay as they are, also when the in-page chat request carries X-Body-Lang: en
                 after_write=lambda viewer_id: briefing.invalidate(viewer_id),   # whatever it wrote may be in the opening briefing (SparkyFitness writes are not in its fingerprint)
                 optional_defaults=OPTIONAL_DEFAULTS)
EXECUTOR = APP.executor
