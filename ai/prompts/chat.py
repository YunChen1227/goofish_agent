CHAT_SYSTEM_PROMPT = """你是一位友好的二手商品买家。你正在与卖家沟通，目标是了解商品的真实状况。

沟通原则:
- 语气友好真诚，模拟真实买家
- 不在第一句就砍价
- 问题逐步深入，每次只问1-2个问题
- 根据卖家风格动态调整"""


def build_greeting(title: str) -> str:
    return f"你好，请问这个「{title}」还在吗？"


def build_inquiry_prompt(info_needed: list[str], chat_history: list[dict]) -> str:
    history_text = "\n".join(
        f"{'我' if m['role'] == 'buyer' else '卖家'}: {m['content']}"
        for m in chat_history
    )
    needed = "、".join(info_needed)
    return (
        f"对话历史:\n{history_text}\n\n"
        f"还需要了解: {needed}\n\n"
        f"请生成下一条自然的买家消息，只问1-2个问题。只输出消息内容。"
    )
