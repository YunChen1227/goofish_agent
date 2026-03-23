NEGOTIATION_SYSTEM_PROMPT = """你是一位精明但友好的买家，正在与卖家进行价格谈判。

谈判原则:
- 先不直接出价，引导卖家让步
- 合理引用商品瑕疵、市场行情作为议价依据
- 可引用其他平台全新价格和闲鱼同款低价
- 语气保持友好，表达诚意
- 只输出消息内容，不要解释策略"""


def build_negotiation_prompt(
    round_num: int,
    seller_price: float,
    target_price: float,
    max_price: float,
    defects: list[str],
    market_data: dict,
    chat_history: list[dict],
) -> str:
    history_text = "\n".join(
        f"{'我' if m['role'] == 'buyer' else '卖家'}: {m['content']}"
        for m in chat_history[-10:]
    )

    market_info: list[str] = []
    if gf := market_data.get("goofish"):
        market_info.append(f"闲鱼同款均价{gf.get('avg_price')}，最低{gf.get('lowest_price')}")
    if cp := market_data.get("cross_platform"):
        if anchor := cp.get("new_price_anchor"):
            market_info.append(f"全新最低价{anchor}")
    if sc := market_data.get("seller_comparison"):
        if lc := sc.get("lowest_candidate"):
            market_info.append(f"其他卖家同品相最低{lc.get('price')}")

    defect_text = "、".join(defects[:3]) if defects else "无明显瑕疵"
    market_text = "；".join(market_info) if market_info else "暂无市场数据"

    return (
        f"对话历史:\n{history_text}\n\n"
        f"当前轮次: 第{round_num}轮\n"
        f"卖家报价: {seller_price}\n我的目标价: {target_price}\n我的上限: {max_price}\n"
        f"商品瑕疵: {defect_text}\n市场行情: {market_text}\n\n"
        f"请生成下一条谈判消息。只输出消息内容。"
    )
