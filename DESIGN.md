# 闲鱼买家 Agent

## 1. 项目概述

本项目是一个面向 **闲鱼（Goofish）及多平台二手入口**（淘宝/京东/拼多多等，通过统一 `PlatformClient` + 各平台 `PlatformConfig` 驱动同一套 Playwright 客户端）的自动化买家代理。用户提交购买需求后，Agent 自动完成「搜索 → 品相鉴定 → 收藏 → 卖家沟通 → 价格谈判 → 结果通知」全链路流程，最大限度减少用户在二手淘货中的时间与精力消耗。

**目标用户**: 有明确购买意向但没有时间逐一筛选、比价、聊天的二手买家。

**核心价值**:
- 自动搜索并筛选海量商品，节省人工浏览时间
- 支持用户提供参考图片，利用多模态 AI 将商品图片与参考图片进行视觉对照，精准筛选出用户真正想要的商品
- 利用多模态 AI 对商品图片/视频进行品相鉴定，降低"买到坑货"的风险
- 自动与卖家沟通、谈价，获取更优成交价格
- 全程无需用户盯盘，最终仅在找到合适商品时通知用户决策

---

## 2. 核心功能

| # | 功能 | 描述 |
|---|------|------|
| F1 | **商品搜索与筛选** | 多平台搜索；列表页经 **LLM 判定标题与用户搜索意图相关性**，必要时 **KeywordOptimizer** 重写查询；详情阶段价格/排除词/信用过滤；可选 **VLM 参考图匹配** 与初筛打分 |
| F2 | **品相智能鉴定** | 利用多模态视觉模型分析商品的图片与视频，评估外观磨损、功能完好度、是否与描述相符，输出品相评分与缺陷报告 |
| F3 | **收藏与候选管理** | 将通过品相鉴定的商品自动加入闲鱼收藏夹，同时在本地维护一份候选商品列表，记录评估结果 |
| F4 | **卖家自动沟通** | 对候选商品发起与卖家的聊天对话，进一步确认品相细节（如隐藏瑕疵、配件齐全度、保修情况等） |
| F5 | **价格智能谈判** | 基于市场行情分析、商品品相评分、用户心理价位，自动与卖家进行多轮价格谈判 |
| F6 | **结果通知** | 在找到符合要求且谈妥价格的商品后，通知用户进行最终确认与下单；若未找到合适商品，同样反馈搜索总结报告 |

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                      用户层 (User Layer)                 │
│            ┌──────────────┐  ┌───────────────────┐       │
│            │ Web Dashboard│  │ 消息通知 (回调)    │       │
│            └──────┬───────┘  └────────┬──────────┘       │
│                   └───────────────────┘                  │
└───────────────────────┼──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│                  编排层 (Orchestrator)                     │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              TaskManager (任务调度)                    │  │
│  │  - 管理任务生命周期                                    │  │
│  │  - 驱动状态机流转                                     │  │
│  │  - 协调各模块执行顺序                                  │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
│  │搜索模块   │ │评估模块   │ │沟通模块   │ │谈判模块      │  │
│  │(Searcher)│ │(Assessor)│ │(Chatter) │ │(Negotiator) │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬──────┘  │
│       └─────────────┴────────────┴──────────────┘         │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│               平台交互层 (Platform Adapter)                │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  GoofishClient（goofish_platform/client.py）           │  │
│  │  - 实现 PlatformClient；多平台共用 PlatformConfig       │  │
│  │  - 登录与会话、搜索/详情、限流与反爬暂停               │  │
│  │  - 收藏 / 聊天等（部分占位或简化）                     │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌───────────────┐  ┌──────────────────────────────────┐  │
│  │ BrowserEngine  │  │ AntiDetect (反检测 / 行为模拟)    │  │
│  │ (Playwright)   │  │ - 随机延迟 / 鼠标轨迹            │  │
│  └───────────────┘  │ - 指纹管理 / Cookie 轮换          │  │
│                      └──────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│                    AI 能力层 (AI Layer)                    │
│                                                           │
│  ┌──────────────────┐  ┌───────────────────────────────┐  │
│  │ 视觉评估模型       │  │ 文本模型 (LLM)                 │  │
│  │ (Multimodal VLM) │  │ - 搜索标题相关性判定           │  │
│  │ - 品相评分        │  │ - KeywordOptimizer 关键词 Agent│  │
│  │ - 缺陷识别        │  │ - 卖家沟通 / 谈判话术          │  │
│  │ - 参考图片对照    │  │                                │  │
│  └──────────────────┘  └───────────────────────────────┘  │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              市场分析模型 (Market Analyzer)             │  │
│  │  - 多平台价格采集 (闲鱼/淘宝/京东/拼多多等)            │  │
│  │  - 闲鱼平台内卖家间比价                                │  │
│  │  - 同类商品价格分析与合理区间推算                       │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│                   持久化层 (Storage)                       │
│                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ SQLite / PG   │  │ 文件存储      │  │ 配置文件        │  │
│  │ - 任务记录    │  │ - 商品图片    │  │ - 用户偏好      │  │
│  │ - 商品数据    │  │ - 视频缓存    │  │ - API Key       │  │
│  │ - 聊天记录    │  │ - 评估报告    │  │ - 浏览器配置    │  │
│  └──────────────┘  └──────────────┘  └────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 4. 用户配置（任务输入）

用户创建一个购买任务时，需要提供以下配置：

### 4.1 必填参数

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `keywords` | String | 搜索关键词 | `"iPhone 15 Pro Max 256G"` |
| `max_price` | Float | 可接受的最高价格（元） | `6000` |
| `target_price` | Float | 期望成交的心理价位（元） | `5200` |
| `condition_requirement` | Enum | 最低可接受品相等级 | `LIKE_NEW` (见品相等级定义) |

### 4.2 可选参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `min_price` | Float | `0` | 最低价格（过低可能是骗子） |
| `location` | String | `null` | 偏好地域，如 `"同城"` / `"江浙沪"` |
| `exclude_keywords` | List[String] | `[]` | 排除关键词，如 `["碎屏", "拆机"]` |
| `max_candidates` | Int | `20` | 最多查看的商品数量 |
| `max_negotiate_count` | Int | `5` | 最多同时与几个卖家谈判 |
| `negotiate_rounds_limit` | Int | `10` | 单个卖家最大谈判轮次 |
| `seller_min_credit` | Int | `null` | 卖家最低信用等级 |
| `prefer_verified` | Bool | `false` | 是否优先验货/官方验商品 |
| `reference_images` | List[String] | `[]` | 用户提供的参考图片路径/URL 列表，Agent 会将商品图片与参考图片进行视觉对照，筛选出外观最匹配的商品 |
| `image_match_threshold` | Float | `0.6` | 图片匹配度阈值 (0-1)，低于该值的商品将被过滤；值越高筛选越严格 |
| `custom_instructions` | Text | `null` | 用户自定义指令，如 `"必须带原装充电器"` |
| `notification_channel` | Enum | `IN_APP` | 通知渠道：`IN_APP` / `EMAIL` / `WEBHOOK` |

### 4.3 品相等级定义

Agent 统一使用以下品相等级体系对商品进行评估：

| 等级 | 代码 | 数值 | 描述 |
|------|------|------|------|
| 全新未拆封 | `SEALED` | 10 | 原封未拆，完整包装 |
| 仅拆封 | `UNBOXED` | 9 | 已拆封但从未使用 |
| 几乎全新 | `LIKE_NEW` | 8 | 极轻微使用痕迹，不影响外观 |
| 轻微使用 | `LIGHTLY_USED` | 7 | 有少量正常使用痕迹 |
| 明显使用 | `WELL_USED` | 6 | 有明显使用痕迹但功能完好 |
| 有瑕疵 | `FAIR` | 5 | 有可见损伤但核心功能正常 |
| 较差 | `POOR` | 4 | 明显损伤，部分功能受影响 |

### 4.4 入口与任务表字段补充

- **入口**：`python -m goofish_agent.main` 启动 Web 服务（默认 `0.0.0.0:8000`）。浏览器打开 `http://localhost:8000/` 即可填写购买任务并查看 6 阶段流程进度；底层复用 `POST /api/tasks/`。任务处于 `paused` / `error` 时可 `POST /api/tasks/{id}/resume` 或点击「续跑」断点续跑（见 §5.0、§11.2）。
- **Task.platform**：对应表单 / API 中的 `platform` 字段，决定 `PlatformConfig`（搜索/详情 URL 与列表页 CSS 选择器）。
- **数据库**：默认 `sqlite:///buyer_agent.db`（`BUYER_AGENT_DATABASE_URL` 可覆盖）。
- **模型与密钥**：`BUYER_AGENT_LLM_*` / `BUYER_AGENT_VLM_*`（OpenAI 兼容端点，默认 DashScope compatible-mode）。

---

## 5. Agent 工作流

### 5.0 编排与状态 (`core/task_manager.py` + `core/state_machine.py`)

- **`TaskManager.initialize()`**：`create_platform_client(platform)` → `VLMClient` / `LLMClient` / `MarketAnalyzer` / `MediaStore` → `await client.start()`。
- **`run_task(task_id)`**：`get_session` 取 `Task` → `StateMachine.transition(RUNNING)` → `_execute_phases`。若任务从 **`PAUSED` / `ERROR` 恢复**（`current_phase` 非空且原状态为暂停或错误），视为**断点续跑**：不重置 `current_phase`，后续按阶段跳过逻辑执行。
- **浏览器被用户关闭**：`goofish_platform/client.py` 监听 `Page` / `BrowserContext` 的 `close` 事件（非程序主动 `close()` 时）置位；`GoofishClient` 将 Playwright 的「目标已关闭」类异常统一映射为 `BrowserClosedByUserError`。`TaskManager.run_task` 捕获后把任务置为 **`ERROR`** 并向上抛出，任务终止。
- **`resume_task(task_id)`**：仅允许 `PAUSED` / `ERROR` → 调用 `run_task`。**不会**把 `current_phase` 清空（与早期「恢复后从头跑」的简化实现不同）。
- **HTTP API**：`POST /api/tasks/{id}/resume` 触发续跑；页面「续跑」按钮调用同一接口。
- **`_execute_phases` 六阶段**（每进入一阶段用 **`_set_phase(task, phase)`** 写入 `task.current_phase` 并 `commit`）:
  1. **SEARCHING**：若 `_should_skip_phase`（`current_phase` 已严格晚于 SEARCHING）则从数据库加载 `ProductCandidate` 列表，否则执行 `Searcher.execute`。
  2. 无候选 → `FAILED` + `notify_failure({"filtered": 0})`
  3. **ASSESSING**：若已跳过则加载 `AssessmentReport`；否则 `Assessor.execute`（**并发**鉴定，见 §5.3 / §6.5）。无合格候选 → `FAILED` + `assessment_rejected`
  4. **FAVORITING**：若已跳过则按 `initial_score` 重算 top-N；否则 `Favoriter.execute` → `notify_progress`
  5. **CHATTING**：若已跳过则从 DB 加载 `SellerConversation`；否则 `Chatter.execute` → 无 `ChatStatus.READY` → `FAILED` + `chat_failed`
  6. **NEGOTIATING**：若已跳过则加载 `NegotiationRecord`；否则 `Negotiator.execute`
  7. **NOTIFYING**：有 `NegotiationStatus.AGREED` → `notify_deal` + `COMPLETED`；否则 `notify_failure` + `FAILED`
- **`_should_skip_phase(task, phase)`**：比较 `PHASE_ORDER` 中 `task.current_phase` 与 `phase` 的下标；若当前阶段**已经严格晚于**本阶段，则本阶段整段跳过并从 DB 取数（阶段级断点）。
- **`StateMachine`**：`VALID_TRANSITIONS` 约束 `TaskStatus`；`PHASE_ORDER` 为 SEARCHING → … → NOTIFYING；`advance_phase` 仍存在于 `state_machine.py`，**编排侧**以 `_set_phase` + 跳过逻辑为主。

### 5.1 整体流程

```
用户提交购买任务
       │
       ▼
 ┌─────────────┐    未找到商品    ┌──────────────┐
 │ Phase 1:    ├───────────────►│ 通知用户:     │
 │ 搜索与初筛   │                │ 无匹配商品    │
 └──────┬──────┘                └──────────────┘
        │ 找到候选商品
        ▼
 ┌─────────────┐    全部不合格    ┌──────────────┐
 │ Phase 2:    ├───────────────►│ 通知用户:     │
 │ 品相鉴定     │                │ 无合格商品    │
 └──────┬──────┘                └──────────────┘
        │ 有合格商品
        ▼
 ┌─────────────┐
 │ Phase 3:    │
 │ 收藏与排序   │
 └──────┬──────┘
        │ 候选排名列表
        ▼
 ┌─────────────┐   卖家回复不佳    ┌──────────────┐
 │ Phase 4:    ├───────────────►│ 降级处理:     │
 │ 卖家沟通     │                │ 跳过该卖家    │
 └──────┬──────┘                └──────────────┘
        │ 确认品相无误
        ▼
 ┌─────────────┐   全部谈判失败    ┌──────────────┐
 │ Phase 5:    ├───────────────►│ 通知用户:     │
 │ 价格谈判     │                │ 未达成交易    │
 └──────┬──────┘                └──────────────┘
        │ 达成协议
        ▼
 ┌─────────────┐
 │ Phase 6:    │
 │ 通知用户     │
 │ 可以登录平台进行交易  │
 └─────────────┘
```

### 5.2 Phase 1: 搜索与初筛（与 `modules/searcher.py` 一致）

**输入**: `Task`（含 `platform`、`keywords`、价格区间、`exclude_keywords`、`reference_images` 等）  
**输出**: 持久化后的 `List[ProductCandidate]`（写入数据库）

**整体子流程**（`Searcher.execute` → `_smart_search` → 详情拉取 → 可选图匹配 → 打分排序）:

```
Smart Search 循环（最多 1 + 3 轮平台搜索）
  │
  +--> PlatformClient.search(query, filters)
  |    filters = { min_price, max_price, location }
  |    （列表页以平台为准；硬过滤主要在详情阶段）
  |
  +--> SearchParser.parse
  |    · 若仅为「猜你喜欢」推荐流 → 视为 0 条真实结果
  |    · 超时 / 风控 / 登录页 → 诊断日志 + 空列表
  |
  +--> 若有结果：LLM 标题相关性判定（_check_relevance_llm）
  │     · 取最多20 条标题 + 用户原始 task.keywords
  │     · 模型输出 JSON：每条 id 是否 relevant + 理由
  │     · 计算「不相关率」；若 < 80% → 本阶段搜索成功，跳出循环
  │
  +--> 若空结果或不相关率 >= 80%：KeywordOptimizer（ai/keyword_optimizer.py）
  │     · 结构化 JSON：意图分析 / 词拆解 / 多策略 / coverage_check.recommendation
  │     · 日志打印完整推理链；解析失败有多层兜底，不向平台提交整段 JSON
  │     · 新关键词与当前不同时：随机等待 5–10s 再搜（降低反爬）
  │
  └─► 达3 次优化仍不理想 → 以最后一轮 briefs 继续后续（可能为空）

详情与规则过滤（最多尝试 10 个商品详情，遍历 briefs[:max_candidates]）
  │
  +--> get_product_detail -> ProductDetail
  +--> 价格 ∈ [min_price, max_price]
  +--> 标题+描述不含 exclude_keywords
  └─► seller_min_credit（若配置）

参考图（若 task.reference_images 非空）
  │
  └─► VLM match_images（最多 3 张商品图 vs 参考图）→ image_match_score
      · ≥ image_match_threshold 保留，否则淘汰

初筛打分 initial_score（Searcher._score_and_sort，与 settings 权重设计不同，代码侧为简化公式）
  │
  · 无参考图：0.4×价格分 + 0.3×信用分 + 0.3
  · 有参考图：0.3×价格分 + 0.2×信用分 + 0.3×image_match + 0.2
  · 价格分：相对 target_price ~ max_price 线性归一化

每通过筛选并构造好的 `ProductCandidate` **立即** `session.add` + `commit`（非仅在 Phase 1 末尾一次性提交），以便进程崩溃或任务进入 `ERROR` 后仍能保留已入库商品。

**断点缓存（搜索列表 + 商品页 URL）**  
`_smart_search` 成功后、开始拉详情前，将本轮 **brief 列表**（含 `product_id`、`title`、`product_url` 等）写入 `task.result_summary["search_state"]`（见 §7.1）。续跑时若存在该缓存，则**跳过**平台再次搜索与 LLM 标题相关性判定，直接按缓存 brief 继续详情与过滤；同时按 `task_id` 查询已有 `platform_product_id`，**跳过**已入库商品，避免重复详情请求。

**初筛规则（代码实际执行）**:
- 搜索页：不做简单子串匹配；**相关性由 LLM 对标题批量判定**。
- 详情页：价格区间、`exclude_keywords`、`seller_min_credit`。
- 参考图：`image_match_threshold` 过滤 +参与 `initial_score`。
- `prefer_verified`：模型字段存在，Searcher 内未单独分支（可后续接入）。

### 5.3 Phase 2: 品相鉴定

**输入**: 候选商品列表  
**输出**: 品相评估报告 `List[AssessmentReport]`

**并发与断点（与 `modules/assessor.py` 一致）**  
- 启动前查询数据库中已存在的 `AssessmentReport`（按 `candidate_id`），**已有报告的商品不再调用 VLM**，仅合并进返回列表。  
- 待鉴定列表使用 **`asyncio.Semaphore`** 限制并发（默认 `MAX_CONCURRENCY = 3`），多商品 **并行** 调用 `VLMClient.assess_product`。  
- 每条报告生成后在与 `Session` 绑定的 **`asyncio.Lock`** 内 `add` + `commit`，保证单会话写库串行、且单条进度可持久化。  
- `ImageAcquisitionHook` 若使用共享 Playwright `Page`，通过 **`asyncio.Lock`** 串行化「需页面」的截图分支，避免多协程同时驱动同一 `Page`。  
- 若抛出 `BrowserClosedByUserError`（或映射后的页面关闭异常），`gather` 会取消其余协程并将错误上抛，由 `TaskManager` 将任务置为 `ERROR`。

**执行步骤**:

1. 下载/截取每个商品的所有图片和视频关键帧
2. 将图片/视频帧与商品描述文本一起提交给多模态视觉模型；若用户提供了 `reference_images`，同时传入参考图片，供模型在评估时对照判断商品是否与用户期望的外观/型号一致
3. 模型输出结构化评估结果:
   - `condition_grade`: 品相等级 (参考 §4.3)
   - `condition_score`: 品相评分 (1-10)
   - `defects`: 发现的瑕疵列表，每项含位置、严重程度、描述
   - `description_match`: 图片与文字描述的一致性评分 (1-10)
   - `risk_flags`: 风险标记（如疑似盗图、图片模糊无法判断、描述避重就轻等）
   - `reference_match`: （仅在用户提供参考图片时输出）与参考图片的匹配评估，含匹配度评分和差异说明（如颜色不同、型号不同、配件差异等）
   - `summary`: 评估总结（自然语言）
4. 根据 `condition_requirement` 过滤品相不达标的商品
5. 品相评分低于用户要求等级对应数值的商品标记为 `REJECTED`

**视觉评估 Prompt 策略**:

```
System: 你是一位专业的二手商品品相鉴定师。请仔细观察以下商品图片/视频，
结合卖家描述，给出客观的品相评估。

重点检查:
1. 外观磨损程度（划痕、掉漆、凹陷、变色）
2. 结构完整性（是否有裂缝、变形、缺件）
3. 屏幕状态（仅限电子产品：是否有坏点、烧屏、碎裂）
4. 配件齐全度（根据描述判断）
5. 图片与描述是否一致（是否有美化/隐瞒嫌疑）

{若用户提供了参考图片，追加以下内容:}
6. 参考图片对照：请将商品图片与买家提供的参考图片进行对比，从以下维度评估匹配度:
   - 商品型号/款式是否一致
   - 颜色/外观是否匹配
   - 规格/配置是否对应
   - 与参考图片存在哪些明显差异
   输出 reference_match 字段，包含匹配度评分 (0-1) 和差异说明

请以 JSON 格式输出评估结果...
```

### 5.4 Phase 3: 收藏与排序

**输入**: 通过品相鉴定的商品列表
**输出**: 排序后的候选列表 + 收藏操作完成

**执行步骤**:

1. 将所有合格商品加入闲鱼收藏夹（方便用户后续查看）
2. 对合格商品进行综合评分排序:

```
综合得分 = w1 × 品相得分 + w2 × 价格得分 + w3 × 卖家信用分 + w4 × 描述一致性分 [+ w5 × 图片匹配度]

其中:
- 品相得分: condition_score / 10 (归一化)
- 价格得分: 1 - (price - target_price) / (max_price - target_price)，越接近心理价位越高
- 卖家信用分: seller_credit / max_credit (归一化)
- 描述一致性分: description_match / 10 (归一化)
- 图片匹配度: reference_match.score (仅在用户提供参考图片时参与计算)

默认权重 (未提供参考图片): w1=0.35, w2=0.30, w3=0.20, w4=0.15
默认权重 (提供参考图片时): w1=0.25, w2=0.25, w3=0.15, w4=0.10, w5=0.25
```

3. 取排名前 `max_negotiate_count` 个商品进入沟通阶段
4. 向用户发送阶段性报告：已找到 N 个合格商品，即将开始与卖家沟通

### 5.5 Phase 4: 卖家沟通

**输入**: 排名靠前的候选商品
**输出**: 沟通结果报告 `List[ChatReport]`

**执行步骤**:

1. 对每个候选商品，通过闲鱼私信功能向卖家发起对话
2. 开场消息策略: 表达购买意向，礼貌询问商品状态
3. 核心沟通目标:
   - 确认商品实际使用时长与频率
   - 确认品相图片中无法判断的细节（如背面、底部）
   - 确认配件是否齐全（充电器、包装盒、说明书等）
   - 确认是否支持验货 / 先验后买
   - 了解出售原因（辅助判断商品可信度）
   - 若用户有 `custom_instructions`，纳入沟通内容
4. 对卖家回复进行分析:
   - 回复态度评分 (积极/冷淡/敷衍)
   - 信息可信度评估 (是否前后矛盾、是否回避关键问题)
   - 商品真实品相修正 (根据卖家补充信息更新评估)
5. 处理异常场景:
   - 卖家长时间未回复 → 设置超时 (默认 6h)，超时后降级排名
   - 卖家回复暴露严重问题 → 标记为 `REJECTED`，跳过此商品
   - 卖家态度恶劣 → 礼貌结束对话，跳过此商品

**对话生成策略**:

- 语气保持友好、真诚，模拟真实买家
- 不在第一句就砍价，先建立沟通信任
- 问题逐步深入，避免一次性发送大量问题
- 根据卖家回复风格动态调整沟通方式
- 每次发送消息前添加随机延迟 (模拟真人打字速度)

### 5.6 Phase 5: 价格谈判

**输入**: 沟通结果良好的商品列表
**输出**: 谈判结果 `List[NegotiationResult]`

**谈判策略**:

Agent 采用分层递进的谈判策略，核心原则是 **"先不直接出价，而是引导卖家让步"**。

**Round 1 - 试探阶段**:
- 表达购买意向，询问"价格还能聊吗"
- 不主动出价，观察卖家是否有让利空间
- 若卖家表示"一口价不议价"，评估当前价格是否在 `max_price` 内可接受

**Round 2 - 引导阶段**:
- 引用品相评估中的小瑕疵作为议价依据
- 引用多平台价格数据作为参考（全新价锚点、二手折价率）
- 引用闲鱼平台内其他卖家的同品相低价作为议价筹码
- 给出模糊的价格暗示（"xx左右的话可以直接拍"）

**Round 3+ - 出价阶段**:
- 第一次正式出价 = `target_price` × 0.9（留出博弈空间）
- 每轮加价幅度递减（模拟真实买家心理）
- 加价策略: `offer_n = offer_(n-1) + (max_price - target_price) × decay_factor^n`
- `decay_factor` 默认 0.5

**终止条件**:
- 达成一致价格 → 状态 `AGREED`，进入通知阶段
- 达到 `negotiate_rounds_limit` 上限 → 状态 `STALEMATE`
- 卖家最终报价超过 `max_price` → 状态 `FAILED`
- 卖家明确拒绝继续谈 → 状态 `REJECTED`
- 所有卖家均谈判失败 → 通知用户总结报告

**市场价格参考**:

Agent 在进入谈判前，通过多渠道采集价格数据，构建全面的市场行情画像：

*1) 多平台价格采集*:
- **闲鱼平台**: 搜索同关键词的已售/在售商品价格，侧重二手成交行情
- **淘宝/天猫**: 获取全新商品官方售价及促销价，作为"全新价格锚点"
- **京东**: 获取自营/第三方售价，尤其关注历史最低价
- **拼多多**: 获取最低渠道价，作为价格下限参考
- **其他渠道** (可扩展): 转转、得物等垂直二手平台

*2) 闲鱼平台内卖家间比价*:
- 汇总当前任务中所有候选卖家的报价，生成价格分布图（最低价、中位价、最高价）
- 标注各卖家报价与品相评分的对应关系（性价比排序）
- 识别报价异常的卖家（明显高于/低于同品相均价）

*3) 综合行情分析*:
- 计算同品相二手商品的市场中位价、均价、最低价
- 计算二手折价率: 二手均价 / 全新最低价
- 评估当前卖家报价在市场中的分位数（偏高/合理/偏低）
- 用于生成谈判中的"市场行情"论据，支持引用具体平台和价格数据

### 5.7 Phase 6: 结果通知

**成功场景**: 找到至少一个达成价格协议的商品
- 通知内容:
  - 商品标题、图片、链接
  - 品相评估摘要
  - 谈定价格 vs 原始标价（省了多少）
  - 卖家沟通记录摘要
  - 建议操作: "请尽快确认并下单"
- 同时发送其他候选商品的简要信息作为备选

**失败场景**: 未找到符合要求的商品
- 通知内容:
  - 搜索范围与筛选条件回顾
  - 各阶段淘汰原因统计（初筛淘汰 N 个、品相不合格 N 个、卖家沟通失败 N 个、谈判未达成 N 个）
  - 建议: 放宽条件 / 提高预算 / 更换关键词
  - 可选: 设置"持续监控"，当新商品上架匹配条件时自动重新执行

---

## 6. 核心模块设计

### 6.1 平台抽象与客户端 (`platform/` + `goofish_platform/`)

**抽象接口** (`platform/base.py`):

- `PlatformConfig`: `name`, `display_name`, `base_url`, `search_url_template`, `item_url_template`, `product_link_selector`, `locale`, `timezone_id`, 登录相关文案与弹层选择器等。
- 内置配置：`GOOFISH_CONFIG`、`TAOBAO_CONFIG`、`JD_CONFIG`、`PDD_CONFIG`（键为 `goofish` / `taobao` / `jd` / `pdd`）。
- `PlatformClient`（抽象）方法：
  - `start()` / `close()`
  - `search(query, filters?) -> list[ProductBrief]`
  - `get_product_detail(product_id) -> ProductDetail | None`
  - `get_product_media(product_id) -> list[str]`（当前实现为再拉详情取图）
  - `add_to_favorites` / `send_message` / `get_messages` / `get_seller_info`

**工厂** (`platform/__init__.py`): `create_platform_client(platform: PlatformType)` — 闲鱼用默认配置，其余平台复用同一 `GoofishClient` 类但注入对应 `PlatformConfig`（通用 Playwright 抓取）。

**具体实现** (`goofish_platform/client.py` — `GoofishClient`):

- `BrowserEngine`：持久化 Chromium 上下文、`browser_data_dir`、可选代理。
- `AuthManager`：Cookie 加载/保存、`ensure_logged_in`（轮询登录过程中可注入 `user_closed_check`，避免用户关窗后仍无限等待）。
- `AntiDetect`：`search`/`detail` 后 `random_browse_pause()`（约 3~15s）；发消息前有额外延迟。
- `RateLimiterRegistry`：按 `config/settings.py` 对 search/detail/favorite/message 等限流。
- **生命周期与用户关窗**：`start()` 后为当前 `Page` / `BrowserContext` 注册 `close` 监听；非程序主动关闭时置 `_user_closed_browser`。后续 `_ensure_page()` 会抛出 `BrowserClosedByUserError`（定义见 `goofish_platform/exceptions.py`）。`search` / `get_product_detail` 等异步路径经 `_run_with_page_guard`，将 Playwright「Target closed」类异常统一包装为同一错误类型，便于上层终止任务。
- **`close()`**：置 `_intentional_close`，避免误报「用户关闭」。

**运行时 DTO（非 ORM）**:

| 类型 | 定义位置 | 主要字段 |
|------|----------|----------|
| `ProductBrief` | `goofish_platform/parsers/search_parser.py` | `product_id`, `title`, `price`, `image_url`, `seller_name`, `location`, `product_url` |
| `ProductDetail` | `goofish_platform/parsers/detail_parser.py` | `product_id`, `title`, `description`, `price`, `images`, `video_url`, `seller_id`, `seller_name`, `seller_credit`, `location`, `product_url` |

### 6.2 搜索页解析 (`SearchParser`)

- 等待列表页商品链接（选择器来自 `PlatformConfig.product_link_selector`）。
- **空结果 / 风控**：`_diagnose_page` 根据正文匹配反爬、登录、无结果文案；超时打诊断日志。
- **闲鱼「猜你喜欢」**：若页面实为推荐流而非真实搜索结果，返回空列表，避免把推荐商品当检索结果。
- 解析链接去重、`ProductBrief` 列表。

### 6.3 关键词 Agent (`KeywordOptimizer`)

- 文件：`ai/keyword_optimizer.py`。
- 输入：用户**原始** `keywords`、当前搜索结果标题样本（最多约 15 条）、历史 `OptimizationResult` 列表。
- 输出：`OptimizationResult`（`intent_analysis`、`keyword_decomposition`、`search_strategies`、`coverage_check`、`optimized_keywords`）；完整推理链写入日志。
- JSON 解析：剥离 thinking 块、markdown 围栏、括号配对提取、弯引号归一化、`json.loads` 失败时正则抽 `recommendation`，**禁止**把整段原始响应当作搜索词。

### 6.4 商品搜索模块 (`Searcher`)

- 文件：`modules/searcher.py`。
- 依赖：`PlatformClient`、`VLMClient`、`LLMClient`、`KeywordOptimizer`、`Session`。
- **常量**：`MAX_KEYWORD_OPTIMIZE_RETRIES = 3`；`MISMATCH_THRESHOLD = 0.8`（不相关率 ≥ 80% 触发换词）；详情最多处理 **10** 个商品（`MAX_DETAIL_ATTEMPTS`）。
- **`_smart_search`**：循环搜索 → `SearchParser` 结果 → **LLM 批量判定标题与 `task.keywords` 意图是否相关** → 必要时调用 `KeywordOptimizer` → 换词后 **5~10s** 随机等待再搜。
- **`execute`**：若 `task.result_summary` 中已有 **`search_state`**（见下），则跳过 `_smart_search` 与相关性判定，直接使用缓存的 brief 列表；否则在 `_smart_search` 成功后、拉详情前**持久化** `search_state`。初筛通过后拉详情、`_passes_filters`、可选 VLM 图匹配、`_score_and_sort`；**每个**通过筛选的 `ProductCandidate` 单独 `commit`；参考图匹配阶段对已写入 `image_match_score` 的记录跳过重算。
- **`result_summary["search_state"]`**（JSON）：`keywords_used`、序列化后的 `briefs`（含 `product_url` 等）、`saved_at`。用于断点续跑时不必重复搜索与 LLM 预筛选。

**初筛打分**（`Searcher._score_and_sort`，与 `Settings` 里 w_condition 等设计权重并存，Phase1 使用下列简化式）:

- 无参考图：`initial_score = 0.4 * price_s + 0.3 * credit_s + 0.3`
- 有参考图：`initial_score = 0.3 * price_s + 0.2 * credit_s + 0.3 * image_match_score + 0.2`
- `price_s`：在 `target_price` ~ `max_price` 间归一化；`credit_s = min(seller_credit/1000, 1)`。

**图片对照**（`reference_images` 非空）: `VLMClient.match_images` 最多 3 张商品图 vs 参考图，低于 `image_match_threshold` 淘汰。

### 6.5 商品评估模块 (Assessor)

**职责**: 利用多模态 AI 模型对商品品相进行智能评估。

**实现要点**（与 `modules/assessor.py` 一致）**  
- 先查库中已存在的 `AssessmentReport`，**未评估的候选**才进入 VLM；支持**断点续跑**（任务在鉴定阶段中断后，已完成的报告不重复）。  
- 多候选通过 **`asyncio.gather` + `Semaphore`** 并行鉴定；每条报告写入后在锁内 `commit`。  
- 若配置了 `ImageAcquisitionHook` + Playwright `Page`，对 `acquire_for_candidate` 使用**页面锁**，避免并发操作同一 `Page`。

**评估流程**（概念上仍为单商品管线；实际可多条并行）:
```
获取商品图片/视频
       │
       ▼
┌──────────────┐
│ 图片预处理    │ ─── 去重、排序、质量检测
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 视频关键帧    │ ─── 如有视频，提取关键帧（均匀采样 + 场景变化检测）
│ 提取          │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ VLM 品相评估  │ ─── 图片+描述文本 → 多模态模型 → 结构化评估结果
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ 风险检测      │ ─── 盗图检测、描述矛盾检测、异常低价检测
└──────┬───────┘
       │
       ▼
  评估报告输出
```

**评估报告结构** (`AssessmentReport`):

```
{
  "product_id": "xxx",
  "condition_grade": "LIKE_NEW",
  "condition_score": 8.2,
  "defects": [
    {
      "location": "背面右下角",
      "severity": "minor",
      "description": "轻微划痕，约1cm长，不影响使用"
    }
  ],
  "description_match": 8.5,
  "risk_flags": [],
  "accessories_confirmed": ["充电器", "数据线"],
  "accessories_missing": ["原装包装盒"],
  "reference_match": {
    "score": 0.92,
    "matched_aspects": ["型号一致", "颜色一致", "存储规格一致"],
    "differences": ["商品为银色而非参考图中的深空灰（不影响功能）"],
    "conclusion": "商品与参考图片高度匹配，仅颜色略有差异"
  },
  "summary": "整体品相良好，仅背面有轻微划痕。图片与描述基本一致，与买家参考图片高度匹配，未发现明显风险。"
}
```

### 6.6 卖家沟通模块 (Chatter)

**职责**: 管理与卖家的自动对话，收集关键信息，为谈判做准备。

**对话状态机**:

```
INIT ──(发送开场白)──> GREETING
  │
GREETING ──(卖家回复)──> INQUIRY
  │
INQUIRY ──(信息收集完成)──> READY_TO_NEGOTIATE
  │       ──(发现严重问题)──> ABANDONED
  │       ──(卖家超时未回复)──> TIMEOUT
  │
READY_TO_NEGOTIATE ──(转交谈判模块)──> NEGOTIATING
  │
NEGOTIATING ──(达成协议)──> COMPLETED
            ──(谈判失败)──> FAILED
```

**信息收集清单** (根据品类动态调整):

| 信息维度 | 示例问题 | 优先级 |
|----------|----------|--------|
| 使用时长 | "请问这个用了多久了？" | 高 |
| 出售原因 | "方便问一下为什么出吗？" | 中 |
| 隐藏瑕疵 | "除了图片上的，还有其他使用痕迹吗？" | 高 |
| 配件情况 | "配件都齐全吗？有原装充电器/包装盒吗？" | 高 |
| 保修状态 | "还在保修期内吗？" | 中 |
| 交易方式 | "可以走平台担保交易吗？" | 高 |
| 发货时效 | "付款后大概多久能发货？" | 低 |

**消息发送规则**:
- 每条消息间隔至少 15-60 秒（随机）
- 一次最多连续发送 2 条消息，之后等待卖家回复
- 卖家回复后等待 5-15 秒再回复（模拟阅读时间）
- 单次对话总消息不超过 20 条（不含谈判阶段）

### 6.7 价格谈判模块 (Negotiator)

**职责**: 基于市场分析和用户预算，执行智能价格谈判。

**谈判上下文构建**:
```
{
  "product_info": { ... },              // 商品基本信息
  "assessment": { ... },                // 品相评估结果
  "market_analysis": {
    "goofish": {                        // 闲鱼平台行情
      "median_price": 5500,
      "avg_price": 5650,
      "lowest_price": 4800,
      "sample_count": 23
    },
    "cross_platform": {                 // 多平台价格参考
      "taobao_new_price": 7999,         // 淘宝全新价
      "jd_new_price": 7899,             // 京东全新价
      "jd_history_low": 6999,           // 京东历史最低
      "pdd_price": 6899,               // 拼多多价格
      "new_price_anchor": 6899,         // 全新最低价锚点
      "secondhand_discount_rate": 0.72  // 二手折价率
    },
    "seller_comparison": {              // 闲鱼卖家间比价
      "current_seller_rank": 3,         // 当前卖家报价排名 (在候选中)
      "total_candidates": 5,
      "lowest_candidate": {"seller": "xx", "price": 5100, "condition_score": 7.8},
      "highest_candidate": {"seller": "yy", "price": 5900, "condition_score": 8.5},
      "price_percentile": 0.65          // 当前卖家报价在候选中的分位数
    }
  },
  "user_config": {                      // 用户预算配置
    "target_price": 5200,
    "max_price": 6000
  },
  "chat_context": { ... },             // 前序沟通记录
  "seller_attitude": "friendly"         // 卖家态度评估
}
```

**出价决策树**:

```
卖家当前报价 P_seller, 我方目标 P_target, 上限 P_max

if P_seller <= P_target:
    → 直接接受，达成交易 ✓

elif P_seller <= P_max:
    if 还有谈判轮次:
        → 尝试砍价至 P_target 附近
        → 报价 = max(P_target, P_seller × 0.85 + P_target × 0.15)
    else:
        → 最终决策: 接受 P_seller 或放弃
        → 取决于 P_seller 与 P_target 的距离和商品稀缺度

else P_seller > P_max:
    if 卖家已明确拒绝降价:
        → 礼貌结束，标记 FAILED
    else:
        → 继续谈判，目标引导至 P_max 以下
```

**谈判话术模板** (由 LLM 基于模板动态生成):

| 场景 | 策略 | 话术方向 |
|------|------|----------|
| 开局试探 | 不直接出价 | "价格还能聊聊吗？" |
| 引用瑕疵 | 合理依据 | "我看图片上 XX 位置有点 XX，这个能便宜些吗？" |
| 引用行情 | 闲鱼市场对比 | "我看闲鱼上同款 XX 成色的大概在 XX 左右..." |
| 引用全新价 | 多平台对比 | "这个全新 XX 才 XX，二手的话 XX 左右比较合理吧" |
| 引用其他卖家 | 卖家间比价 | "我看到另一个同成色的才挂 XX，您这边能再优惠下吗？" |
| 表达诚意 | 促成交易 | "如果 XX 的话我可以直接拍，秒付款" |
| 最终出价 | 限时压力 | "最多能接受 XX，可以的话现在就拍" |
| 礼貌离场 | 留有余地 | "这个价格有点超预算了，我再看看，谢谢老板" |

### 6.8 通知模块 (Notifier)

**职责**: 在关键节点向用户推送进度与结果通知。

**通知类型**:

| 类型 | 触发条件 | 内容 |
|------|----------|------|
| `TASK_STARTED` | 任务开始执行 | 搜索参数确认 |
| `SEARCH_COMPLETED` | Phase 1 完成 | 找到 N 个候选商品 |
| `ASSESSMENT_COMPLETED` | Phase 2 完成 | N 个商品通过品相检查，已加入收藏 |
| `NEGOTIATION_PROGRESS` | 任一卖家有实质性回复 | 当前谈判进展摘要 |
| `DEAL_FOUND` | 达成价格协议 | 商品详情 + 谈定价格 + 操作指引 |
| `TASK_FAILED` | 全流程未找到合适商品 | 淘汰统计 + 建议 |
| `TASK_ERROR` | 系统异常 | 错误描述 + 恢复方案 |

**通知渠道**:
- `IN_APP`: 应用内消息队列（默认）
- `EMAIL`: 邮件推送
- `WEBHOOK`: HTTP 回调（用于集成第三方工具，如微信机器人、钉钉等）

---

## 7. 数据模型

### 7.1 核心实体

#### Task (购买任务) — `models/task.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `user_id` | UUID | 所属用户 |
| `platform` | `PlatformType` | `goofish` / `taobao` / `jd` / `pdd` / `custom` |
| `keywords` | String | 搜索关键词 |
| `min_price` | Float | 最低价格（默认 0） |
| `max_price` | Float | 最高价格 |
| `target_price` | Float | 心理价位 |
| `condition_requirement` | `ConditionGrade` | 最低品相要求（数据库存整型枚举值） |
| `location` | String? | 地域偏好 |
| `exclude_keywords` | List[String] | 排除关键词（JSON） |
| `max_candidates` | Int | 列表页最多跟进条目（默认 20） |
| `max_negotiate_count` | Int | 进入谈判的卖家数上限（默认 5） |
| `negotiate_rounds_limit` | Int | 单卖家谈判轮次上限（默认 10） |
| `seller_min_credit` | Int? | 卖家最低信用 |
| `prefer_verified` | Bool | 是否偏好验货/严选（字段存在，Searcher 内未单独分支） |
| `reference_images` | List[String] | 参考图路径或 URL（JSON） |
| `image_match_threshold` | Float | 图匹配阈值（默认 0.6） |
| `custom_instructions` | Text? | 用户自定义指令 |
| `notification_channel` | `NotificationChannel` | `in_app` / `email` / `webhook` |
| `status` | `TaskStatus` | 任务状态 |
| `current_phase` | `TaskPhase?` | 当前阶段（断点续跑时保留，不随 `resume` 清空） |
| `result_summary` | JSON? | 结果摘要；其中 **`search_state`** 由 Searcher 写入，缓存已通过 LLM 相关性判定的 **搜索 brief 列表**（含 `product_url` 等），供任务中断后跳过重复搜索 |
| `created_at` | DateTime | 创建时间 |
| `updated_at` | DateTime | 更新时间 |

#### ProductCandidate (候选商品) — `models/candidate.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `task_id` | UUID | 所属任务 |
| `platform_product_id` | String | 平台商品 ID |
| `title` | String | 商品标题 |
| `description` | Text | 商品描述 |
| `price` | Float | 标价 |
| `seller_id` | String | 卖家 ID |
| `seller_name` | String | 卖家昵称 |
| `seller_credit` | Int? | 卖家信用等级 |
| `images` | List[String] | 图片 URL 列表 |
| `video_url` | String? | 视频 URL |
| `product_url` | String | 商品链接 |
| `initial_score` | Float | 初筛综合得分 |
| `image_match_score` | Float? | 与参考图片的匹配度 (0-1)，仅在用户提供参考图片时有值 |
| `status` | Enum | 候选状态 |
| `created_at` | DateTime | 入选时间 |

#### AssessmentReport (品相评估报告) — `models/assessment.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `candidate_id` | UUID | 关联候选商品 |
| `condition_grade` | `ConditionGrade` | 品相等级（整型枚举） |
| `condition_score` | Float | 品相评分 (1-10) |
| `defects` | JSON | 瑕疵列表 |
| `description_match` | Float | 描述一致性评分 (1-10) |
| `risk_flags` | List[String] | 风险标记 |
| `accessories_confirmed` | List[String] | 已确认配件 |
| `accessories_missing` | List[String] | 缺失配件 |
| `reference_match` | JSON? | 参考图片匹配评估（含匹配度评分、匹配维度、差异说明），仅在用户提供参考图片时有值 |
| `summary` | Text | 评估总结 |
| `model_used` | String | 使用的评估模型 |
| `created_at` | DateTime | 评估时间 |

#### SellerConversation (卖家对话) — `models/conversation.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `candidate_id` | UUID | 关联候选商品 |
| `seller_id` | String | 卖家 ID |
| `platform_conversation_id` | String? | 平台会话 ID |
| `messages` | List[dict] | 消息记录（JSON） |
| `chat_status` | `ChatStatus` | 对话状态 |
| `seller_attitude` | String? | 卖家态度（自由文本，非枚举） |
| `info_collected` | dict | 已收集信息（JSON） |
| `started_at` | DateTime | 开始时间 |
| `last_message_at` | DateTime? | 最后消息时间 |

#### NegotiationRecord (谈判记录) — `models/negotiation.py`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID | 主键 |
| `conversation_id` | UUID | 关联对话 |
| `initial_price` | Float | 卖家初始价格 |
| `target_price` | Float | 我方目标价 |
| `current_offer` | Float? | 我方当前出价 |
| `seller_counter` | Float? | 卖家当前还价 |
| `round_count` | Int | 已进行轮次 |
| `agreed_price` | Float? | 最终协议价格 |
| `market_reference` | JSON | 市场参考价格数据 |
| `status` | Enum | 谈判状态 |
| `history` | JSON | 谈判过程记录 (每轮出价/还价/话术) |

### 7.2 枚举类型 — `models/enums.py`

```
PlatformType:
  GOOFISH        - 闲鱼
  TAOBAO         - 淘宝二手
  JD             - 京东二手
  PDD            - 拼多多二手
  CUSTOM         - 自定义（需自行传入 PlatformConfig）

TaskStatus:
  PENDING        - 等待执行
  RUNNING        - 执行中
  PAUSED         - 用户暂停
  COMPLETED      - 已完成（找到商品）
  FAILED         - 已完成（未找到商品）
  CANCELLED      - 用户取消
  ERROR          - 系统错误

TaskPhase:
  SEARCHING      - 搜索与初筛阶段
  ASSESSING      - 品相鉴定阶段
  FAVORITING     - 收藏与排序阶段
  CHATTING       - 卖家沟通阶段
  NEGOTIATING    - 价格谈判阶段
  NOTIFYING      - 结果通知阶段

CandidateStatus:
  ACTIVE         - 仍在候选池
  FAVORITED      - 已收藏
  CHATTING       - 沟通中
  NEGOTIATING    - 谈判中
  AGREED         - 已达成协议
  REJECTED       - 已淘汰（品相/沟通/谈判不通过）
  TIMEOUT        - 卖家超时

ChatStatus:
  INIT           - 初始化
  GREETING       - 已发送开场白
  INQUIRY        - 信息收集中
  READY          - 准备谈判
  NEGOTIATING    - 谈判中
  COMPLETED      - 对话完成
  ABANDONED      - 放弃
  TIMEOUT        - 超时

NegotiationStatus:
  IN_PROGRESS    - 谈判进行中
  AGREED         - 达成协议
  STALEMATE      - 僵持（达到轮次上限）
  FAILED         - 谈判失败
  REJECTED       - 卖家拒绝

ConditionGrade:
  SEALED         - 全新未拆封
  UNBOXED        - 仅拆封
  LIKE_NEW       - 几乎全新
  LIGHTLY_USED   - 轻微使用
  WELL_USED      - 明显使用
  FAIR           - 有瑕疵
  POOR           - 较差
```

---

## 8. 任务状态机

```
                    用户创建任务
                        │
                        ▼
                    ┌────────┐
                    │PENDING │
                    └───┬────┘
                        │ 系统开始执行
                        ▼
                    ┌────────┐  ◄──── 用户恢复
              ┌────►│RUNNING │──────────────────────┐
              │     └───┬────┘                      │
              │         │                           │
              │    ┌────┴───────┬──────────┐        │
              │    │            │          │        │
              │    ▼            ▼          ▼        ▼
              │ ┌───────┐ ┌────────┐ ┌─────────┐ ┌──────┐
              │ │PAUSED │ │COMPLETED│ │ FAILED  │ │ERROR │
              │ └───┬───┘ └────────┘ └─────────┘ └──┬───┘
              │     │  用户恢复                      │ 可重试
              │     └──────────────┘                 │
              └─────────────────────────────────────┘

RUNNING 内部阶段流转:
  SEARCHING → ASSESSING → FAVORITING → CHATTING → NEGOTIATING → NOTIFYING
```

**状态流转规则**:

| 当前状态 | 事件 | 目标状态 | 说明 |
|----------|------|----------|------|
| `PENDING` | 系统调度执行 | `RUNNING` | 进入搜索阶段 |
| `RUNNING` | 用户暂停 | `PAUSED` | 保存当前进度，暂停所有操作 |
| `PAUSED` | 用户恢复（`POST /api/tasks/{id}/resume` 或页面「续跑」） | `RUNNING` | 从 `current_phase` 及已持久化数据断点继续（见 §11.2） |
| `RUNNING` | 找到并达成交易 | `COMPLETED` | 通知用户后结束 |
| `RUNNING` | 全流程无合适商品 | `FAILED` | 通知用户后结束 |
| `RUNNING` | 系统异常 | `ERROR` | 记录错误，等待重试或人工介入 |
| `ERROR` | 用户续跑（同上） | `RUNNING` | 从 `current_phase` 继续；典型原因：用户关闭浏览器导致 `BrowserClosedByUserError` |
| `*` | 用户取消 | `CANCELLED` | 终态，不可恢复 |

---

## 9. 技术栈

| 技术 | 用途 | 选型理由 |
|------|------|----------|
| **Python 3.11+** | 主语言 | AI 生态成熟，异步支持好 |
| **Playwright** | 浏览器自动化 | 比 Selenium 更快更稳定，原生异步支持 |
| **OpenAI GPT-4o / Gemini 2.5** | 多模态视觉评估 | 图片/视频理解能力强 |
| **OpenAI GPT-4o / DeepSeek** | 对话生成与谈判 | 中文对话能力优秀，成本可控 |
| **SQLite (dev) / PostgreSQL (prod)** | 数据存储 | 轻量开发，生产可扩展 |
| **SQLModel** | ORM | 结合 SQLAlchemy 与 Pydantic |
| **FastAPI** | Web API (可选) | 任务管理、状态查询接口 |
| **Celery / APScheduler** | 任务调度 | 异步任务执行与定时调度 |
| **Redis** | 消息队列 / 缓存 | Celery broker + 会话缓存 |
| **Pydantic** | 数据校验 | 配置、API Schema 校验 |
| **Loguru** | 日志 | 结构化日志，便于调试 |

---

## 10. 反检测与风控

闲鱼平台有完善的反爬虫和反自动化机制，Agent 必须在以下方面做好风控：

### 10.1 浏览器指纹管理

- 使用 `playwright-stealth` 插件隐藏自动化特征
- 随机化 `User-Agent`、屏幕分辨率、语言、时区等指纹参数
- 使用持久化浏览器上下文 (persistent context)，保持 Cookie 和 localStorage 一致性
- 避免使用无头模式 (headless)，优先使用有头模式

### 10.2 行为模拟

| 行为 | 模拟策略 |
|------|----------|
| 鼠标移动 | 贝塞尔曲线轨迹，非直线移动 |
| 点击 | 随机偏移 (±5px)，添加按压时长 |
| 打字 | 逐字输入，随机间隔 50-200ms |
| 滚动 | 平滑滚动，随机暂停 |
| 页面浏览 | 随机停留 3-15 秒 |
| 操作间隔 | 操作之间添加 1-5 秒随机延迟 |

### 10.3 频率控制

| 操作 | 频率限制 | 说明 |
|------|----------|------|
| 搜索 | ≤ 10 次/小时 | 避免触发搜索频率限制 |
| 详情页浏览 | ≤ 30 次/小时 | 模拟正常浏览速度 |
| 收藏 | ≤ 20 次/小时 | 避免批量收藏异常 |
| 发起聊天 | ≤ 5 个新对话/小时 | 聊天频率最敏感 |
| 发送消息 | ≤ 30 条/小时 | 含所有对话的消息总量 |

### 10.4 异常处理

- 检测到验证码 → 暂停任务，通知用户手动处理或接入打码服务
- 检测到账号风控 → 立即停止所有操作，通知用户
- 检测到 IP 被限制 → 切换代理 IP（需用户配置代理池）
- 页面结构变化 → 记录异常，触发告警，等待适配更新

---

## 11. 错误处理与容错

### 11.1 重试策略

| 错误类型 | 重试次数 | 间隔 | 策略 |
|----------|----------|------|------|
| 网络超时 | 3 次 | 指数退避 (5s, 15s, 45s) | 自动重试 |
| 页面加载失败 | 3 次 | 固定 10s | 刷新页面后重试 |
| 登录态失效 | 1 次 | - | 自动重新登录 |
| 验证码拦截 | 0 次 | - | 暂停并通知用户 |
| LLM API 错误 | 3 次 | 指数退避 | 自动重试，可切换备用模型 |
| 消息发送失败 | 2 次 | 30s | 重试后仍失败则跳过 |

### 11.2 断点恢复

**阶段级**  
- `Task.current_phase` 在每进入一阶段时更新（`_set_phase`），`resume` **不清空**该字段。  
- `_execute_phases` 若发现 `current_phase` 已严格晚于某一阶段，则**跳过**该阶段整段逻辑，改为从数据库加载该阶段产出（候选、报告、对话、谈判记录等）。

**Phase 1（Searcher）**  
- `result_summary["search_state"]`：在 `_smart_search` 成功后写入完整 brief 列表（含商品页 URL），续跑时跳过搜索 + LLM 相关性判定。  
- 每个通过筛选的 `ProductCandidate` **单独 commit**；续跑时用已有 `platform_product_id` 集合跳过已处理商品。  
- 参考图匹配：已写入 `image_match_score` 的记录不重复调用 VLM `match_images`。

**Phase 2（Assessor）**  
- 已存在 `AssessmentReport` 的 `candidate_id` 跳过 VLM；未完成部分以并发协程继续，每条报告提交后落库。

**API / UI**  
- `POST /api/tasks/{id}/resume`；静态页任务详情区提供「续跑」按钮。

**局限（当前实现）**  
- Phase 4 / 5 若半道中断，续跑时采用「整阶段跳过则从 DB 加载」策略；未完全做到对话/谈判轮次内的细粒度断点（可后续扩展）。

### 11.3 降级策略

| 场景 | 降级方案 |
|------|----------|
| 主 VLM 不可用 | 切换到备用视觉模型 |
| 主 LLM 不可用 | 切换到备用对话模型 |
| 浏览器崩溃 | 重新启动浏览器实例，恢复会话 |
| 代理 IP 耗尽 | 降低请求频率，直连执行 |
| 视频无法加载 | 仅基于图片评估，降低评估置信度 |

---

## 12. 安全考虑

- **凭证管理**: 用户闲鱼账号的登录凭证（Cookie）加密存储，不明文记录密码
- **API Key 保护**: LLM API Key 通过环境变量或加密配置文件管理，不硬编码
- **隐私保护**: 卖家个人信息仅在任务生命周期内使用，任务结束后可选清除
- **操作审计**: 所有平台操作记录详细日志，支持事后审计
- **权限隔离**: 不同用户的任务数据严格隔离
- **代理安全**: 若使用代理 IP，确保代理服务商可信，避免中间人攻击
- **风控自律**: 严格遵守频率限制，避免对平台造成不正当负担

---

## 13. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **平台交互方式** | 统一使用 Playwright 浏览器自动化 | 闲鱼无公开 API，浏览器自动化功能覆盖最全面，且便于处理动态渲染页面；统一方案降低维护复杂度 |
| **品相评估方式** | 多模态 VLM 而非传统 CV | 二手商品品相判断需要语义理解（如"正常使用痕迹"），纯 CV 难以胜任 |
| **对话与谈判分离** | 两个独立模块 | 信息收集和价格谈判的目标、策略、话术风格不同，分离可降低复杂度 |
| **任务编排方式** | 顺序阶段 + 阶段内并行 | 各 Phase 有严格依赖关系（先搜索才能评估）；**品相鉴定**阶段对多商品 VLM 调用并发执行（有并发上限与写库锁） |
| **断点续跑** | `current_phase` + DB 实体 + `search_state` | 用户暂停、浏览器被关（`ERROR`）后可续跑；搜索列表与已入库候选、已生成鉴定报告均不重复浪费 |
| **数据库选型** | SQLite (dev) / PostgreSQL (prod) | 开发期零依赖快速迭代，生产环境可平滑切换 |
| **并发卖家沟通** | 串行轮询而非真并行 | 避免同一时间段内发起过多对话触发风控 |
| **谈判策略** | LLM 动态生成而非规则引擎 | 谈判场景多变，硬编码规则难以覆盖所有情况，LLM 可灵活应对 |
| **通知机制** | 多渠道支持 | 不同用户偏好不同，关键结果应能及时触达 |
| **不自动下单** | Agent 只谈不买 | 涉及资金安全，最终购买决策必须由用户本人确认 |
| **搜索列表相关性** | LLM 批量判定标题 vs用户关键词意图 | 子串匹配无法处理行话、异名同物；与 KeywordOptimizer 配合减少无效详情请求 |
| **无结果页** | SearchParser 识别「猜你喜欢」等兜底 | 避免把推荐流当真实搜索结果，减少后续污染 |
| **关键词优化** | 独立 KeywordOptimizer + 结构化 JSON | 可记录推理链；解析多层兜底避免把整段模型输出当搜索词 |
| **用户入口** | Web 页面替代 CLI | 非技术用户可直接使用，且可直观观察 6 阶段流水线（搜索 → 鉴定 → 收藏 → 沟通 → 谈判 → 通知）进展；底层复用现有 HTTP API |

---

## 14. 项目目录结构（与仓库一致）

```
goofish_agent/
├── config/
│   ├── settings.py              # BUYER_AGENT_* 环境变量、LLM/VLM/浏览器/限流等
│   └── logging.py               # Loguru 配置
│
├── core/
│   ├── task_manager.py          # 六阶段编排、模块装配
│   ├── state_machine.py         # TaskStatus 转移、TaskPhase 顺序
│   └── scheduler.py             # 定时调度（若使用）
│
├── platform/
│   ├── base.py                  # PlatformConfig、PlatformClient 抽象、各平台 URL模板
│   └── __init__.py              # create_platform_client
│
├── goofish_platform/            # Playwright 实现（多平台共用 GoofishClient + Config）
│   ├── client.py                # GoofishClient：search / detail / 限流 / 反爬暂停
│   ├── exceptions.py            # BrowserClosedByUserError、TargetClosed 类错误判别
│   ├── browser.py               # BrowserEngine 持久化上下文
│   ├── auth.py                  # AuthManager、登录与 Cookie
│   ├── anti_detect.py           # 随机延迟、人类化操作辅助
│   └── parsers/
│       ├── search_parser.py     # ProductBrief、猜你喜欢/风控诊断
│       ├── detail_parser.py     # ProductDetail
│       └── chat_parser.py       # 聊天解析
│
├── modules/
│   ├── searcher.py              # Smart search、LLM 相关性、KeywordOptimizer、初筛打分
│   ├── assessor.py
│   ├── favoriter.py
│   ├── chatter.py
│   ├── negotiator.py
│   └── notifier.py
│
├── ai/
│   ├── llm_client.py            # OpenAI 兼容异步客户端
│   ├── vlm_client.py          # 品相鉴定、图匹配
│   ├── keyword_optimizer.py   # 关键词 Agent、JSON 解析兜底
│   ├── market_analyzer.py
│   └── prompts/
│       ├── assessment.py
│       ├── chat.py
│       └── negotiation.py
│
├── models/                      # SQLModel 表
├── schemas/                     # Pydantic API Schema
├── api/                         # FastAPI应用与 routes
├── static/                      # Web 前端（index.html 单页，流程管理展示）
├── storage/                     # database.py、media_store.py
├── utils/                       # retry、rate_limiter、crypto
├── main.py                      # Web 服务入口（uvicorn 启动 FastAPI + 静态页面）
├── requirements.txt
├── README.md
└── DESIGN.md
```

---

## 15. 后续扩展方向

- **持续监控模式**: 任务完成后可设置"守候"，当新商品上架匹配条件时自动触发新一轮流程
- **多平台支持**: 抽象 PlatformClient 接口，扩展支持转转、拍拍等其他二手平台
- **历史价格追踪**: 记录商品价格变动趋势，辅助判断出价时机
- **买家社区情报**: 接入商品评测、避坑指南等外部信息源，提升评估准确度
- **多任务并行**: 支持用户同时运行多个购买任务（不同商品），共享浏览器实例
- **智能复盘**: 对已完成任务进行分析，优化谈判策略参数
