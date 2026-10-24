"""Watch2Whoop's texts for the datalayer framework (datalayer/messages.py, issue #116): AI-facing, so Chinese.
These are the texts assistant/toolsets.py, ai/playbooks, assistant/tools.run_tool, assistant/writelog.py, mcp_server.py
and ai/mcp_http.py had inline before the move; tests/fixtures/datalayer pins them."""
from ..datalayer.messages import Messages

MESSAGES = Messages(
    find_description="找这次没加载的工具，返回完整参数说明；再用 run_read_tool / run_write_tool 调用（看 run 字段）。"
                     "group、query 都不填 = 列出所有组和工具名。",
    find_group_param_sep=" / ",
    find_query_param="关键词，空格分开，如「化验 趋势」",
    run_read_description="调用一个只读工具（find_tools 查到的）。name = 工具名。",
    run_write_description="调用一个写工具（find_tools 查到的），规矩和直接调用一样：只写给自己，先一句话说要写什么。",
    run_args_param="它的参数，和直接调用一样（user 不填 = 自己）",
    run_direct="直接调用",
    unknown_group="没有这个组：{group}；可用的组：{groups}",
    group_sep=" / ",
    find_no_hits="没找到：不填参数调 find_tools 看所有组，或按 group 查",
    find_index_hint="用 find_tools(group=…) 拿完整参数说明",
    args_not_object="args 要是一个对象（JSON）",
    name_required="name 要填一个工具名（find_tools 查到的）",
    no_such_tool="没有这个工具，或这里不能用：{name}（先用 find_tools 查）",
    use_run_write="{name} 是写工具，要用 run_write_tool",
    use_run_read="{name} 是只读工具，要用 run_read_tool",
    read_only="这个 token / 这次登录是只读的，不能写",
    call_directly="{name} 要直接调用（删除 / 撤销类，客户端会先请用户确认）",
    missing_args="{name} 缺少参数：{missing}（参数说明用 find_tools 查）",
    missing_sep="、",
    unknown_playbook="没有这个 playbook：{name}；有：{names}",
    playbook_sep=" / ",
    write_only_self="写操作只能写给自己",
    no_grant="没有查看用户 {owner} 的 {scope} 的授权",
    no_write_log_row="没有这条操作日志：{id}（或不是你的）",
    already_rolled_back="这条已经在 {at} 回滚过了",
    cannot_roll_back="这条没有可自动回滚的前值；用对应的工具手动改回",
    mcp_no_such_tool="没有这个工具：{name}",
    mcp_read_only="这个 token 是只读的，不能写",
    mcp_no_such_prompt="没有这个 prompt：{name}",
    mcp_not_signed_in="没有登录",
    mcp_not_started="MCP 没有启动",
    mcp_missing_bearer="缺少 Bearer token（用今天页「AI 设置」里生成的 AI token，或用 OAuth 连接）",
    mcp_invalid_token="token 无效、已过期、已吊销，或不是 AI 专用 token",
)
