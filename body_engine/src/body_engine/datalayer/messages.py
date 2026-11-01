"""Fixed texts the framework shows the model: meta tool descriptions, find_tools hints, guardrail errors and the MCP
transports' errors (issue #116).

ENGLISH is the framework's default. An application in another language builds its own Messages: every field is required,
so one that forgets a text fails when it is built, never by silently showing the model another language
(docs/datalayer-design.md sections 3.8 and 7; dataclasses.replace(ENGLISH, ...) changes a few texts on purpose).
Placeholders in braces are filled with str.format; testing.check_messages compares them with ENGLISH.
Watch2Whoop passes its Chinese texts (ai/datalayer_texts.py)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Messages:
    # meta tools (datalayer/toolsets.py)
    find_description: str
    find_group_param_sep: str        # joins the group names into find_tools' `group` description
    find_query_param: str
    run_read_description: str
    run_write_description: str
    run_args_param: str
    # find_tools results
    run_direct: str                  # `run` of a tool that must be called by its own name
    unknown_group: str               # {group}, {groups}
    group_sep: str                   # joins the group names in unknown_group
    find_no_hits: str
    find_index_hint: str
    # resolve (run_read_tool / run_write_tool)
    args_not_object: str
    name_required: str
    no_such_tool: str                # {name}
    use_run_write: str               # {name}
    use_run_read: str                # {name}
    read_only: str
    call_directly: str               # {name}
    missing_args: str                # {name}, {missing}
    missing_sep: str                 # joins the missing argument names
    # running a tool: a required argument is missing (datalayer/execute.py)
    missing_param: str               # {param}: the missing argument names, joined with missing_sep
    # playbooks (datalayer/playbooks.py)
    unknown_playbook: str            # {name}, {names}
    playbook_sep: str                # joins the playbook names in unknown_playbook
    # running a tool (datalayer/execute.py)
    write_only_self: str
    no_grant: str                    # {owner}, {scope}
    # write log rollback (datalayer/writelog.py)
    no_write_log_row: str            # {id}
    already_rolled_back: str         # {at}: YYYY-MM-DD HH:MM
    cannot_roll_back: str
    # MCP server (datalayer/mcp/server.py)
    mcp_no_such_tool: str            # {name}
    mcp_read_only: str
    mcp_no_such_prompt: str          # {name}
    mcp_no_such_resource: str        # {uri}
    # MCP over HTTP (datalayer/mcp/http.py)
    mcp_not_signed_in: str
    mcp_not_started: str
    mcp_missing_bearer: str
    mcp_invalid_token: str


ENGLISH = Messages(
    find_description="Find tools that are not loaded in this session and get their full parameters; then call them with "
                     "run_read_tool / run_write_tool (see the `run` field). Leave group and query empty to list every "
                     "group and tool name.",
    find_group_param_sep=" / ",
    find_query_param="Keywords separated by spaces",
    run_read_description="Run a read tool found with find_tools. name = the tool's name.",
    run_write_description="Run a write tool found with find_tools. The rules are the same as calling it directly: it only "
                          "writes your own data, and you say in one sentence what you are about to write first.",
    run_args_param="Its arguments, the same as calling it directly (leave out user for your own data)",
    run_direct="call it by its own name",
    unknown_group="No such group: {group}; the groups are: {groups}",
    group_sep=" / ",
    find_no_hits="Nothing found: call find_tools without arguments to see every group, or look in one group",
    find_index_hint="Use find_tools(group=...) for the full parameters",
    args_not_object="args must be an object (JSON)",
    name_required="name must be a tool name (from find_tools)",
    no_such_tool="No such tool, or it is not available here: {name} (look it up with find_tools first)",
    use_run_write="{name} is a write tool: use run_write_tool",
    use_run_read="{name} is a read tool: use run_read_tool",
    read_only="This token / session is read-only and cannot write",
    call_directly="Call {name} by its own name (it deletes or undoes something, so the client asks the user first)",
    missing_args="{name} is missing arguments: {missing} (find_tools shows its parameters)",
    missing_sep=", ",
    missing_param="Missing argument {param}",
    unknown_playbook="No such playbook: {name}; the playbooks are: {names}",
    playbook_sep=" / ",
    write_only_self="Write tools only write your own data",
    no_grant="No permission to see {scope} of user {owner}",
    no_write_log_row="No such write log entry: {id} (or it is not yours)",
    already_rolled_back="This entry was already rolled back at {at}",
    cannot_roll_back="This entry has no earlier value to restore automatically; change it back with the matching tool",
    mcp_no_such_tool="No such tool: {name}",
    mcp_read_only="This token is read-only and cannot write",
    mcp_no_such_prompt="No such prompt: {name}",
    mcp_no_such_resource="No such resource: {uri}",
    mcp_not_signed_in="Not signed in",
    mcp_not_started="The MCP server is not running",
    mcp_missing_bearer="Missing Bearer token",
    mcp_invalid_token="The token is invalid, expired or revoked",
)
