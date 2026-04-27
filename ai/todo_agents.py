"""TODO 驱动对话的 Planner / Checker 辅助。

设计要点：
- Planner 只在真正需要时调用 LLM（初次 + lazy replan，上限 2 次）；
- Checker 每次卖家回复后调用一次；
- `choose_next_todo` 是纯规则的本地函数，不消耗 LLM；
- `should_replan` 收敛所有 replan 触发条件，便于单测和复盘。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from loguru import logger

from goofish_agent.ai.llm_client import LLMClient
from goofish_agent.ai.prompts.todo import (
    CHECKER_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    TODO_BUILDER_SYSTEM_PROMPT,
    build_checker_user_message,
    build_planner_user_message,
    build_todo_builder_user_message,
    safe_load_json,
)

# 沟通失败/敏感关键词，checker 命中后会触发 replan（可能要插入追问项）。
SENSITIVE_FLAG_WORDS = (
    "翻新", "进水", "拆机", "维修", "换过屏", "摔过", "二次销售", "碎过", "泡过水"
)

MAX_REPLAN_TIMES = 2  # 含 lazy replan，不包含初次规划


# ---------------------------------------------------------------------------
# 构造 / 规范化
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_todo_item(raw: dict, fallback_id: str) -> dict:
    """把用户的一条 todo 原始输入规范成内部结构。"""
    item = {
        "id": str(raw.get("id") or fallback_id),
        "title": str(raw.get("title") or raw.get("name") or fallback_id).strip(),
        "user_prompt": str(
            raw.get("user_prompt")
            or raw.get("prompt")
            or raw.get("desc")
            or raw.get("description")
            or raw.get("title")
            or ""
        ).strip(),
        "priority_hint": (raw.get("priority_hint") or raw.get("priority") or "").strip().lower() or None,
        "status": "pending",
        "result": None,
        "evidence": None,
        "updated_at": None,
    }
    return item


def fallback_todos_from_text(raw_text: str) -> list[dict]:
    """LLM 不可用时，把自然语言按行/标点粗略拆成可维护 TODO。"""
    import re

    text = (raw_text or "").strip()
    if not text:
        return []
    parts = [
        p.strip(" -—\t\r\n")
        for p in re.split(r"[\n;；。]+", text)
        if p.strip(" -—\t\r\n")
    ]
    if not parts:
        parts = [text]
    todos: list[dict] = []
    for idx, part in enumerate(parts[:8], 1):
        title = part[:24]
        todos.append(
            {
                "title": title,
                "user_prompt": f"向卖家确认：{part}",
                "priority_hint": "normal",
            }
        )
    return todos


async def build_todos_from_description(
    llm: LLMClient,
    raw_description: str,
    keywords: str | None = None,
    custom_instructions: str | None = None,
) -> list[dict]:
    """把买家的自然语言 TODO 描述转成结构化 TODO 列表。"""
    raw_description = (raw_description or "").strip()
    if not raw_description:
        return []

    user_msg = build_todo_builder_user_message(
        raw_description,
        keywords=keywords,
        custom_instructions=custom_instructions,
    )
    try:
        raw = await llm.generate(TODO_BUILDER_SYSTEM_PROMPT, user_msg, temperature=0.2)
    except Exception as e:
        logger.warning(f"[TodoBuilder] LLM 调用失败，使用本地拆分兜底：{e}")
        return fallback_todos_from_text(raw_description)

    parsed = safe_load_json(raw)
    todos = parsed.get("todos") if isinstance(parsed, dict) else parsed
    if not isinstance(todos, list):
        logger.warning(f"[TodoBuilder] 无法解析 TODO 生成结果，使用本地拆分兜底：{raw[:200]}")
        return fallback_todos_from_text(raw_description)

    normalized: list[dict] = []
    seen_titles: set[str] = set()
    for idx, item in enumerate(todos, 1):
        if not isinstance(item, dict):
            continue
        todo = normalize_todo_item(item, fallback_id=f"u{idx}")
        if not todo["title"] or todo["title"] in seen_titles:
            continue
        normalized.append(
            {
                "title": todo["title"],
                "user_prompt": todo["user_prompt"] or f"向卖家确认「{todo['title']}」",
                "priority_hint": todo.get("priority_hint") or "normal",
            }
        )
        seen_titles.add(todo["title"])

    if not normalized:
        return fallback_todos_from_text(raw_description)

    logger.info(
        f"[TodoBuilder] 根据自然语言生成 {len(normalized)} 个 TODO: "
        f"{[i['title'] for i in normalized]}"
    )
    return normalized


def build_initial_todo_state(
    user_todos: Iterable[dict] | None,
    default_checklist: Iterable[str] | None,
    merge_defaults: bool = True,
) -> dict:
    """拼用户 todo + 默认 checklist（用户在前，默认在后，去重基于 title）。"""
    items: list[dict] = []
    seen_titles: set[str] = set()

    # 用户自定义：保持顺序
    for i, raw in enumerate(user_todos or []):
        if not isinstance(raw, dict):
            continue
        item = normalize_todo_item(raw, fallback_id=f"u{i + 1}")
        if not item["title"] or item["title"] in seen_titles:
            continue
        items.append(item)
        seen_titles.add(item["title"])

    # 默认 checklist：合并到尾部
    if merge_defaults and default_checklist:
        for j, title in enumerate(default_checklist):
            t = str(title).strip()
            if not t or t in seen_titles:
                continue
            items.append(
                normalize_todo_item(
                    {
                        "title": t,
                        "user_prompt": f"向卖家确认「{t}」",
                        "priority_hint": "normal",
                    },
                    fallback_id=f"d{j + 1}",
                )
            )
            seen_titles.add(t)

    return {
        "version": 1,
        "items": items,
        "plan": {
            "order": [it["id"] for it in items],  # 未跑 planner 前的保守默认顺序
            "next": items[0]["id"] if items else None,
            "reasoning": None,
            "planned_at": None,
            "replan_count": 0,
        },
        "_stuck_rounds": {},  # 本地状态：todo_id -> 连续问过但仍 pending 的轮次
    }


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


async def plan_todos(
    llm: LLMClient,
    todo_state: dict,
    product_title: str,
    product_description: str,
    assessment_summary: str | None = None,
    custom_instructions: str | None = None,
) -> dict:
    """跑 planner；成功时原地更新 todo_state.plan 并返回新 plan。"""
    items = todo_state.get("items", []) or []
    pending_items = [
        {
            "id": i["id"],
            "title": i["title"],
            "user_prompt": i.get("user_prompt", ""),
            "priority_hint": i.get("priority_hint"),
        }
        for i in items
        if i.get("status", "pending") == "pending"
    ]
    if not pending_items:
        return todo_state["plan"]

    user_msg = build_planner_user_message(
        todos=pending_items,
        product_title=product_title,
        product_description=product_description,
        assessment_summary=assessment_summary,
        custom_instructions=custom_instructions,
    )
    logger.info(
        f"[TodoPlanner] 规划 {len(pending_items)} 个 pending todo | "
        f"已完成 {sum(1 for i in items if i.get('status')=='done')} 项"
    )
    try:
        raw = await llm.generate(PLANNER_SYSTEM_PROMPT, user_msg, temperature=0.3)
    except Exception as e:
        logger.warning(f"[TodoPlanner] LLM 调用失败，保留原 plan：{e}")
        return todo_state["plan"]

    parsed = safe_load_json(raw) or {}
    pending_ids = [i["id"] for i in pending_items]
    # 只保留合法 id，planner 漏掉的 id 补到尾部
    order_raw = parsed.get("order") or []
    order = [tid for tid in order_raw if tid in pending_ids]
    for tid in pending_ids:
        if tid not in order:
            order.append(tid)

    next_id = parsed.get("next") if parsed.get("next") in order else (order[0] if order else None)

    plan = todo_state.setdefault("plan", {})
    plan["order"] = order
    plan["next"] = next_id
    plan["reasoning"] = parsed.get("reasoning")
    plan["planned_at"] = _now_iso()
    logger.info(
        f"[TodoPlanner] 新 plan: next={next_id} | order={order} | "
        f"reason={str(plan['reasoning'])[:120]}"
    )
    return plan


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------


async def check_todos(
    llm: LLMClient,
    todo_state: dict,
    seller_reply: str,
    recent_history: list[dict],
) -> list[dict]:
    """调用 LLM 检查哪些 pending todo 被卖家最新回复回答了；返回 updates 列表。"""
    items = todo_state.get("items", []) or []
    pending = [
        {"id": i["id"], "title": i["title"], "user_prompt": i.get("user_prompt", "")}
        for i in items
        if i.get("status", "pending") == "pending"
    ]
    if not pending:
        return []

    user_msg = build_checker_user_message(pending, recent_history, seller_reply)
    try:
        raw = await llm.generate(CHECKER_SYSTEM_PROMPT, user_msg, temperature=0.1)
    except Exception as e:
        logger.warning(f"[TodoChecker] LLM 调用失败，本轮不打勾：{e}")
        return []

    parsed = safe_load_json(raw) or {}
    updates = parsed.get("updates") or []
    if not isinstance(updates, list):
        logger.warning(f"[TodoChecker] updates 不是数组：{updates!r}")
        return []

    pending_ids = {p["id"] for p in pending}
    valid = []
    for u in updates:
        if not isinstance(u, dict):
            continue
        tid = u.get("id")
        st = u.get("status")
        if tid in pending_ids and st in ("done", "skipped"):
            valid.append(
                {
                    "id": tid,
                    "status": st,
                    "result": (u.get("result") or "").strip() or None,
                    "evidence": (u.get("evidence") or "").strip() or None,
                }
            )
    if valid:
        logger.info(
            f"[TodoChecker] 本轮打勾 {len(valid)} 项："
            + "; ".join(f"{v['id']}={v['status']}" for v in valid)
        )
    return valid


def apply_updates(todo_state: dict, updates: list[dict]) -> set[str]:
    """原地应用 updates，返回本轮刚刚变为 done/skipped 的 id 集合。"""
    if not updates:
        return set()
    id2item = {i["id"]: i for i in todo_state.get("items", [])}
    changed: set[str] = set()
    for u in updates:
        item = id2item.get(u["id"])
        if not item or item.get("status", "pending") != "pending":
            continue
        item["status"] = u["status"]
        if u.get("result"):
            item["result"] = u["result"]
        if u.get("evidence"):
            item["evidence"] = u["evidence"]
        item["updated_at"] = _now_iso()
        changed.add(u["id"])
    return changed


# ---------------------------------------------------------------------------
# 本地规则：选下一步 / 判断是否 replan
# ---------------------------------------------------------------------------


def choose_next_todo(todo_state: dict) -> str | None:
    """顺着 plan.order 找第一个仍 pending 的；都不在则随便挑一个。"""
    items = todo_state.get("items", []) or []
    id2item = {i["id"]: i for i in items}
    plan = todo_state.get("plan") or {}
    for tid in plan.get("order") or []:
        it = id2item.get(tid)
        if it and it.get("status", "pending") == "pending":
            plan["next"] = tid
            return tid
    for it in items:
        if it.get("status", "pending") == "pending":
            plan["next"] = it["id"]
            return it["id"]
    plan["next"] = None
    return None


def bump_stuck_count(todo_state: dict, asked_id: str | None) -> int:
    """一轮询问结束后调用；若该 id 仍 pending 则计数 +1，否则清 0。返回当前计数。"""
    if not asked_id:
        return 0
    id2item = {i["id"]: i for i in todo_state.get("items", [])}
    item = id2item.get(asked_id)
    stuck: dict = todo_state.setdefault("_stuck_rounds", {})
    if item and item.get("status", "pending") == "pending":
        stuck[asked_id] = int(stuck.get(asked_id, 0)) + 1
    else:
        stuck.pop(asked_id, None)
    return int(stuck.get(asked_id, 0))


def should_replan(
    todo_state: dict,
    asked_id: str | None,
    just_changed_ids: set[str],
    seller_reply: str,
) -> tuple[bool, str]:
    """返回 (是否 replan, 原因描述)。在 chatter 中已满额时会被覆盖。"""
    plan = todo_state.get("plan") or {}
    if int(plan.get("replan_count", 0)) >= MAX_REPLAN_TIMES:
        return False, "replan 次数已达上限"

    stuck = int((todo_state.get("_stuck_rounds") or {}).get(asked_id or "", 0))
    if stuck >= 2:
        return True, f"todo {asked_id} 连续 {stuck} 轮仍 pending"

    if len(just_changed_ids) >= 2:
        return True, f"本轮一次打勾 {len(just_changed_ids)} 项，顺序需要重排"

    if seller_reply and any(w in seller_reply for w in SENSITIVE_FLAG_WORDS):
        hit = [w for w in SENSITIVE_FLAG_WORDS if w in seller_reply]
        return True, f"检测到敏感关键词 {hit}，可能需要补充追问项"

    return False, ""
