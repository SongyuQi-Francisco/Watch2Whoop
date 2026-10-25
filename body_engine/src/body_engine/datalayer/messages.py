"""Fixed texts the framework shows the model: meta tool descriptions, find_tools hints, guardrail errors and the MCP
transports' errors (issue #116).

Every field is required, so an application that forgets one fails when it builds its Messages, never by silently showing
the model another language (docs/datalayer-design.md sections 3.8 and 7). Placeholders in braces are filled with
str.format. English defaults for other projects come in a later phase; Watch2Whoop passes its Chinese texts
(ai/datalayer_texts.py)."""
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
    # MCP over HTTP (datalayer/mcp/http.py)
    mcp_not_signed_in: str
    mcp_not_started: str
    mcp_missing_bearer: str
    mcp_invalid_token: str
