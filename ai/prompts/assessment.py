ASSESSMENT_SYSTEM_PROMPT = """你是一位专业的二手商品品相鉴定师。请仔细观察以下商品图片/视频，结合卖家描述，给出客观的品相评估。

重点检查:
1. 外观磨损程度（划痕、掉漆、凹陷、变色）
2. 结构完整性（是否有裂缝、变形、缺件）
3. 屏幕状态（仅限电子产品：是否有坏点、烧屏、碎裂）
4. 配件齐全度（根据描述判断）
5. 图片与描述是否一致（是否有美化/隐瞒嫌疑）

请以 JSON 格式输出评估结果，包含以下字段:
- condition_grade: 品相等级 (SEALED/UNBOXED/LIKE_NEW/LIGHTLY_USED/WELL_USED/FAIR/POOR)
- condition_score: 品相评分 (1-10)
- defects: 瑕疵列表 [{location, severity, description}]
- description_match: 描述一致性评分 (1-10)
- risk_flags: 风险标记列表
- accessories_confirmed: 已确认配件列表
- accessories_missing: 缺失配件列表
- summary: 评估总结"""

REFERENCE_IMAGE_ADDON = """
6. 参考图片对照：请将商品图片与买家提供的参考图片进行对比:
   - 商品型号/款式是否一致
   - 颜色/外观是否匹配
   - 规格/配置是否对应
   - 与参考图片存在哪些明显差异
   额外输出 reference_match 字段: {score: 0-1, matched_aspects: [], differences: [], conclusion: str}"""

IMAGE_MATCH_PROMPT = """请对比以下两组图片的视觉相似度。
第一组是商品实物图片，第二组是买家提供的参考图片。
请仅输出一个 0 到 1 之间的浮点数表示匹配度，1 表示完全一致，0 表示完全不同。
只输出数字，不要其他内容。"""

DAMAGE_CRITERIA_ADDON = """
额外要求（用户提供的「常见损伤」判定依据，用于辅助品相判断）:
- 用户以文字补充了在意的常见损伤/瑕疵模式。评估时请结合该说明，在 defects 中具体标注是否与这些模式相关。
- 若用户提供了「常见损伤参考图」，请将商品实拍与这些参考图对照，判断是否存在**相同或类似类型**的损伤（如划痕位置与形态、掉漆范围、形变、屏幕点线等），并在 summary 中简要说明。"""


def build_assessment_prompt(
    description: str,
    has_reference: bool = False,
    *,
    has_damage_criteria: bool = False,
) -> str:
    system = ASSESSMENT_SYSTEM_PROMPT
    if has_damage_criteria:
        system += DAMAGE_CRITERIA_ADDON
    if has_reference:
        system += REFERENCE_IMAGE_ADDON
    return system


def build_assessment_user_message(
    description: str,
    damage_pattern_description: str | None = None,
) -> str:
    text = f"商品描述:\n{description}\n"
    if damage_pattern_description and damage_pattern_description.strip():
        text += (
            "\n用户补充的常见物品损伤/瑕疵模式（请结合实拍重点核查）:\n"
            f"{damage_pattern_description.strip()}\n"
        )
    return text + "\n请评估以上商品的品相。"
