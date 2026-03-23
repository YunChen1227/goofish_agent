from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID, uuid4


def main() -> None:
    parser = argparse.ArgumentParser(description="闲鱼买家 Agent")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="运行购买任务")
    run_p.add_argument("--keywords", required=True, help="搜索关键词")
    run_p.add_argument("--max-price", type=float, required=True)
    run_p.add_argument("--target-price", type=float, required=True)
    run_p.add_argument("--condition", default="LIKE_NEW")
    run_p.add_argument("--location", default=None)
    run_p.add_argument("--reference-images", nargs="*", default=[])
    run_p.add_argument("--config", help="JSON配置文件路径")

    sub.add_parser("server", help="启动 API 服务")

    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(run_task(args))
    elif args.command == "server":
        run_server()
    else:
        parser.print_help()


async def run_task(args: argparse.Namespace) -> None:
    from goofish_agent.config.logging import setup_logging
    from goofish_agent.storage.database import get_session, init_db

    setup_logging()
    init_db()

    config: dict = {}
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    from goofish_agent.core.task_manager import TaskManager
    from goofish_agent.models.enums import ConditionGrade
    from goofish_agent.models.task import Task

    known_keys = {
        "keywords", "max_price", "target_price", "condition",
        "location", "reference_images",
    }

    with get_session() as session:
        task = Task(
            id=uuid4(),
            user_id=uuid4(),
            keywords=config.get("keywords", args.keywords),
            max_price=config.get("max_price", args.max_price),
            target_price=config.get("target_price", args.target_price),
            condition_requirement=ConditionGrade[
                config.get("condition", args.condition)
            ],
            location=config.get("location", args.location),
            reference_images=config.get(
                "reference_images", args.reference_images or []
            ),
            **{k: v for k, v in config.items() if k not in known_keys},
        )
        session.add(task)
        session.commit()
        task_id: UUID = task.id

    mgr = TaskManager()
    await mgr.initialize()
    try:
        await mgr.run_task(task_id)
    finally:
        await mgr.shutdown()


def run_server() -> None:
    import uvicorn

    from goofish_agent.config.logging import setup_logging

    setup_logging()
    uvicorn.run(
        "goofish_agent.api.app:app", host="0.0.0.0", port=8000, reload=True
    )


if __name__ == "__main__":
    main()
