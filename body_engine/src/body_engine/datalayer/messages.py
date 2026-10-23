"""Fixed texts the framework shows the model: meta tool descriptions, find_tools hints and guardrail errors (issue #116).

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
    # playbooks (datalayer/playbooks.py)
    unknown_playbook: str            # {name}, {names}
    playbook_sep: str                # joins the playbook names in unknown_playbook
