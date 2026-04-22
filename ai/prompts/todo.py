"""TODO 驱动对话的三个 prompt 模板。

数据流：
- Planner: 只跑一次（或 lazy replan 触发时）——产出 plan.order / plan.next / plan.reasoning。
- Checker: 每次卖家回复后跑——只打勾和填 result/evidence，不决定下一步。
- Inquiry: 每轮询问前按 todo_state 渲染 "待办 / 已完成" 状态拼给 Reply Agent。
"""
from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# 通用渲染
# ---------------------------------------------------------------------------


def _fmt_history(messages: list[dict], tail: int = 10) -> str:
    return "\n".join(
        f"{'我' if m.get('role') == 'buyer' else '卖家'}: {m.get('content', '')}"
        for m in messages[-tail:]
    ) or "(暂无对话)"


def render_todo_board(todo_state: dict) -> str:
    """把 todo_state.items 渲染为人类可读的状态板。"""
    items = (todo_state or {}).get("items", [])
    if not items:
        return "(todo list 为空)"
    plan = (todo_state or {}).get("plan") or {}
    order_ids: list[str] = plan.get("order") or [i["id"] for i in items]
    id2item = {i["id"]: i for i in items}
    lines: list[str] = []
    for tid in order_ids:
        item = id2item.get(tid)
        if not item:
            continue
        status = item.get("status", "pending")
        title = item.get("title", "")
        if status == "done":
            result = item.get("result") or "(无具体内容)"
            lines.append(f"- [已完成] {tid} 《{title}》：已获取 → {result}")
        elif status == "skipped":
            reason = item.get("result") or "卖家回避/不适用"
            lines.append(f"- [跳过] {tid} 《{title}》：{reason}")
        else:  # pending / failed
            prompt = item.get("user_prompt") or ""
            pri = item.get("priority_hint") or ""
            suffix = f"（优先级:{pri}）" if pri else ""
            lines.append(f"- [待办] {tid} 《{title}》{suffix}：{prompt}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1) PLANNER
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """你是一个二手交易沟通策略规划师。

你的任务是：基于买家提供的 TODO 清单、商品信息和品相报告，决定最佳的提问顺序，
使买家能用尽量少的消息、尽量低的对话压力，拿到最关键的信息。

规划原则：
1) 先易后难：容易回答、不敏感的项（使用时长、是否在保、配件）放前面；
2) 关键项不丢：critical 优先级项即便不在最前，也必须在 order 中靠前位置；
3) 敏感项殿后：涉及真伪、退货、隐瞒、维修史的项放后段，避免一开口就让卖家防备；
4) 有依赖的项：只有前置项已知才问的项放后面。

只能输出 JSON，schema 如下：
{
  "order": ["<todo_id>", ...],   // 包含全部 pending todo id
  "next":  "<todo_id>",          // order 中第一个应立即推进的 id
  "reasoning": "一两句话说明为何这样排"
}
"""


def build_planner_user_message(
    todos: list[dict],
    product_title: str,
    product_description: str,
    assessment_summary: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    parts: list[str] = []
    parts.append(f"商品: {product_title}")
    if product_description:
        parts.append(f"描述: {product_description[:400]}")
    if assessment_summary:
        parts.append(f"品相报告摘要: {assessment_summary[:400]}")
    if custom_instructions:
        parts.append(f"买家额外说明: {custom_instructions[:300]}")
    parts.append(
        "TODO 清单 (JSON):\n" + json.dumps(todos, ensure_ascii=False, indent=2)
    )
    parts.append("请输出规划 JSON。")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 2) CHECKER
# ---------------------------------------------------------------------------

CHECKER_SYSTEM_PROMPT = """你是一个对话分析员，负责核对买家的 TODO 是否已经被卖家的最新回复回答。

判定规则（务必保守）：
- done: 卖家明确给出了答案（可在回复原文中引用一句作为 evidence）；
- skipped: 卖家明确拒绝回答 / 不适用 / 无法给出（例如"我也不知道"）；
- still_pending: 没有回答、回避、答非所问。

注意：
- 只处理 pending 的 todo，不要改动已经 done/skipped 的；
- 不要"脑补"：只有当回复里真的包含对应信息时才 done；
- result 字段是最终要给买家看的结论（如 "用了1年半"、"原装充电器+盒"），不是原文；
- evidence 字段是从卖家回复中截取的关键原文（≤ 40 字）。

只输出 JSON：
{
  "updates": [
    {"id": "<todo_id>", "status": "done|skipped", "result": "...", "evidence": "..."}
  ]
}
若本轮没有任何 todo 被回答，输出 {"updates": []}。
"""


def build_checker_user_message(
    pending_items: list[dict],
    recent_history: list[dict],
    latest_seller_reply: str,
) -> str:
    return (
        f"待核对的 TODO (仅 pending):\n"
        f"{json.dumps(pending_items, ensure_ascii=False, indent=2)}\n\n"
        f"对话历史(最近):\n{_fmt_history(recent_history, tail=8)}\n\n"
        f"卖家最新回复:\n{latest_seller_reply}\n\n"
        f"请输出 updates JSON。"
    )


# ---------------------------------------------------------------------------
# 3) TODO-AWARE INQUIRY (Reply Agent)
# ---------------------------------------------------------------------------

TODO_INQUIRY_SYSTEM_PROMPT = """你是一位友好的二手商品买家，与卖家沟通。

沟通原则：
- 口吻自然、像真实买家，不要机械列问题；
- 每条消息只推进 1~2 个 TODO；
- 优先聚焦「当前应推进」的 TODO，顺带确认最多 1 个其他待办；
- 已完成的 TODO 不要重复问；
- 只输出发送给卖家的消息，不要解释策略、不要列编号。"""


def build_todo_inquiry_prompt(
    todo_state: dict,
    chat_history: list[dict],
    next_todo_id: str | None,
    plan_reasoning: str | None = None,
) -> str:
    items = (todo_state or {}).get("items", [])
    id2item = {i["id"]: i for i in items}
    next_item = id2item.get(next_todo_id) if next_todo_id else None

    next_hint = ""
    if next_item:
        next_hint = (
            f"当前应优先推进: {next_item['id']} 《{next_item.get('title','')}》 "
            f"— {next_item.get('user_prompt','')}"
        )
        if plan_reasoning:
            next_hint += f"\n（规划理由: {plan_reasoning[:120]}）"
    else:
        next_hint = "当前无明确优先项，可挑选一个待办推进。"

    return (
        f"对话历史:\n{_fmt_history(chat_history, tail=10)}\n\n"
        f"TODO 状态板:\n{render_todo_board(todo_state)}\n\n"
        f"{next_hint}\n\n"
        f"请生成下一条买家消息（只输出消息本体）。"
    )


# ---------------------------------------------------------------------------
# JSON 提取容错
# ---------------------------------------------------------------------------


def safe_load_json(raw: str) -> Any:
    """宽容解析：尝试去掉 ```json``` 包裹、裁剪到第一个合法 JSON。"""
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        # 去掉 ```json / ``` 包裹
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:].lstrip("\n")
        if s.endswith("```"):
            s = s[:-3]
    try:
        return json.loads(s)
    except Exception:
        pass
    # 尝试抓第一个 { ... } 或 [ ... ]
    for l, r in (("{", "}"), ("[", "]")):
        i = s.find(l)
        j = s.rfind(r)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(s[i : j + 1])
            except Exception:
                continue
    return None
