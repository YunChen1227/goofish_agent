# Goofish Agent（二手平台买家 Agent）

自动化在**可配置平台**上按你的条件搜索商品、评估、沟通与谈价的代理程序。支持 **闲鱼（`goofish`）**、淘宝二手、京东二手、拼多多等（见下方「支持的平台」）；**每次任务须显式指定 `platform`，不设隐式默认**。提供 **命令行** 与 **HTTP API** 两种使用方式。

---

## 支持的平台

通过任务或 CLI **显式传入** `platform`（值为小写字符串，与 `PlatformType` 枚举一致），**无默认值**：

| 值 | 说明 |
|----|------|
| `goofish` | 闲鱼（Goofish） |
| `taobao` | 淘宝二手（URL/选择器为预设模板，实际页面可能需单独适配） |
| `jd` | 京东二手（同上） |
| `pdd` | 拼多多二手（同上） |
| `custom` | 自定义：需在代码中传入 `PlatformConfig` 并扩展工厂（见 `platform/base.py`、`platform/__init__.py`） |

非 `goofish` 平台目前复用同一套 Playwright 客户端骨架与可配置的搜索/详情 URL 模板；**解析器与登录逻辑是否完全匹配目标站点**，需按实际页面自行验证或扩展。

---

## 环境要求

- **Python**：建议 3.10+
- **依赖**：见仓库根目录 `requirements.txt`
- **Playwright**：使用 Chromium 自动化浏览器（需单独安装浏览器二进制）

---

## 安装

1. 将本仓库放在某个目录下，使 Python 能导入包名 `goofish_agent`（即：项目根目录的**上一级**需在 `PYTHONPATH` 中，或你在该上一级执行下面的命令）。

2. 安装 Python 依赖：

   ```bash
   pip install -r requirements.txt
   ```

3. 安装 Playwright 浏览器（至少需要 Chromium）：

   ```bash
   playwright install chromium
   ```

---

## 配置（环境变量）

配置通过 **`.env` 文件** 或环境变量读取。字段定义见 `config/settings.py`，**环境变量前缀为 `BUYER_AGENT_`**，名称由字段名转成大写加下划线（Pydantic Settings 惯例）。

| 变量 | 对应配置项 | 说明 | 默认值（摘录） |
|------|------------|------|----------------|
| `BUYER_AGENT_LLM_API_KEY` | `llm_api_key` | LLM API 密钥 | 见 `settings.py` |
| `BUYER_AGENT_LLM_BASE_URL` | `llm_base_url` | LLM 兼容 OpenAI 的基地址 | DashScope 兼容模式示例 |
| `BUYER_AGENT_LLM_MODEL` | `llm_model` | 文本模型名 | `qwen3.5-plus` |
| `BUYER_AGENT_VLM_API_KEY` | `vlm_api_key` | 视觉模型 API 密钥 | 可与 LLM 相同 |
| `BUYER_AGENT_VLM_BASE_URL` | `vlm_base_url` | VLM 基地址 | 同上 |
| `BUYER_AGENT_VLM_MODEL` | `vlm_model` | 视觉模型名 | `qwen3.5-plus` |
| `BUYER_AGENT_DATABASE_URL` | `database_url` | 数据库连接串 | `sqlite:///buyer_agent.db` |
| `BUYER_AGENT_BROWSER_HEADLESS` | `browser_headless` | 是否无头浏览器 | `False`（有界面，便于登录） |
| `BUYER_AGENT_BROWSER_DATA_DIR` | `browser_data_dir` | 浏览器用户数据目录 | `browser_data` |
| `BUYER_AGENT_PROXY_SERVER` | `proxy_server` | 可选 HTTP 代理 | 无 |
| `BUYER_AGENT_MEDIA_DIR` | `media_dir` | 媒体缓存目录 | `media_cache` |
| `BUYER_AGENT_ENCRYPTION_KEY` | `encryption_key` | 可选，用于敏感信息加密 | 无 |
| `BUYER_AGENT_SMTP_*` / `BUYER_AGENT_WEBHOOK_URL` | 通知相关 | 邮件或 Webhook（若启用） | 见 `settings.py` |

评分权重（`w_condition`、`w_price` 等）、各接口每小时请求上限（`search_per_hour` 等）同样可通过 `BUYER_AGENT_` + 大写字段名覆盖。

日志默认写入运行目录下 **`logs/buyer_agent.log`**（见 `config/logging.py`）。

---

## 如何运行

### 1. 工作目录与模块路径

本包导入形式为 `goofish_agent.xxx`。请在 **`goofish_agent` 文件夹的上一级** 执行命令（例如若路径为 `d:\workspace\goofish_agent`，则在 `d:\workspace` 下执行）。

若遇 `ModuleNotFoundError: goofish_agent`，可设置：

```bash
# Windows PowerShell
$env:PYTHONPATH = "D:\workspace"   # 改为你的上一级目录

# Windows CMD（路径含空格时：`set "PYTHONPATH=D:\your path"`）
set PYTHONPATH=D:\workspace

# Linux / macOS
export PYTHONPATH=/path/to/workspace
```

---

### 2. 命令行（CLI）

在项目上一级目录执行：

```bash
python -m goofish_agent.main --help
```

#### 子命令：`run`（执行一次购买任务）

**平台（必须显式指定其一）：**

- 命令行：`--platform`，取值为 `goofish`（闲鱼）、`taobao`、`jd`、`pdd`、`custom`。
- 或使用 **`--config` 指向的 JSON** 里的 **`"platform"`** 字段。
- 若两者都未提供有效平台，程序会报错退出（退出码 2）。

**`goofish` 即闲鱼**：参数名在代码中为 `goofish`，对应站点为闲鱼。

必填参数：

- `--platform` **或** JSON 中的 `platform`（见上）
- `--keywords`：搜索关键词
- `--max-price`：可接受的最高价格（元）
- `--target-price`：目标/心理价位（元）

常用可选参数：

- `--condition`：成色要求，对应 `ConditionGrade` 枚举名，默认 `LIKE_NEW`（如 `SEALED`、`LIKE_NEW`、`LIGHTLY_USED` 等）
- `--location`：地域筛选
- `--reference-images`：零个或多个参考图路径（可多次或空格分隔，视你 shell 而定）
- `--config`：JSON 配置文件路径；文件中的 `platform`、`keywords`、`max_price`、`target_price`、`condition`、`location`、`reference_images` 可覆盖或与命令行合并，其余键会合并进任务模型（**若仅用配置文件指定平台，则 JSON 内必须含 `platform`**）

示例（闲鱼，须写 `--platform`）：

```bash
python -m goofish_agent.main run ^
  --platform goofish ^
  --keywords "iPhone 15 Pro" ^
  --max-price 6000 ^
  --target-price 5500 ^
  --condition LIKE_NEW
```

示例（京东）：

```bash
python -m goofish_agent.main run --platform jd --keywords "iPhone 15" --max-price 5000 --target-price 4500
```

（Linux/macOS 把 `^` 换成 `\` 续行或写成一行。）

#### 子命令：`server`（启动 API）

默认通过 Uvicorn 加载 `goofish_agent.api.app:app`，监听 **`0.0.0.0:8000`**，并开启 **reload**。

```bash
python -m goofish_agent.main server
```

启动后：

- 健康检查：`GET http://localhost:8000/health`
- OpenAPI 文档：浏览器打开 `http://localhost:8000/docs`（FastAPI 自动生成）

---

### 3. HTTP API

路由前缀（见 `api/app.py`）：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 服务健康状态 |
| `POST` | `/api/tasks/` | 创建任务（请求体为 JSON，见下表） |
| `GET` | `/api/tasks/` | 列出所有任务 |
| `GET` | `/api/tasks/{task_id}` | 查询单个任务 |
| `POST` | `/api/tasks/{task_id}/pause` | 暂停任务 |
| `POST` | `/api/tasks/{task_id}/cancel` | 取消任务 |
| `GET` | `/api/results/{task_id}/candidates` | 某任务的候选商品 |
| `GET` | `/api/results/{task_id}/negotiations` | 某任务的谈判记录摘要 |

创建任务 `POST /api/tasks/` 的请求体字段与默认值（见 `schemas/task.py`）：

| 字段 | 说明 |
|------|------|
| `platform` | **必填**，与 CLI 的 `--platform` 取值一致（如 `"goofish"` 表示闲鱼） |
| `keywords` | 必填，搜索关键词 |
| `max_price` / `target_price` | 必填 |
| `min_price` | 默认 `0` |
| `condition_requirement` | 默认 `"LIKE_NEW"`（与 `ConditionGrade` 枚举名一致） |
| `location` | 可选 |
| `exclude_keywords` | 排除词列表 |
| `max_candidates` / `max_negotiate_count` / `negotiate_rounds_limit` | 数量与轮次上限 |
| `seller_min_credit` / `prefer_verified` | 卖家信用与认证偏好 |
| `reference_images` / `image_match_threshold` | 参考图与图像匹配阈值 |
| `custom_instructions` | 自定义说明 |
| `notification_channel` | `"IN_APP"`、`"EMAIL"` 或 `"WEBHOOK"`（与 `NotificationChannel` 一致） |

创建成功后任务在后台运行；数据库在首次访问时初始化（`init_db()`）。不同 `platform` 的任务会使用对应平台的 `TaskManager` 实例执行。

---

## 架构速览（多平台）

- **`platform/base.py`**：`PlatformClient` 抽象接口、`PlatformConfig`（站点 URL、搜索/商品链接模板、语言/时区等）。
- **`platform/__init__.py`**：`create_platform_client(platform)` 工厂。
- **`goofish_platform/`**：当前具体实现（如 `GoofishClient`、解析器、鉴权）；可按平台复制或子类化扩展。

扩展新站点时：增加 `PlatformType` 与 `PLATFORM_CONFIGS` 中的配置，或实现新的 `PlatformClient` 并在工厂中注册。

---

## 使用流程提示

1. **首次使用**：`BUYER_AGENT_BROWSER_HEADLESS=false`（默认）时可在窗口内完成**当前任务所选平台**的登录；程序会按平台保存独立 cookie 文件（如 `goofish_cookies.json`）。  
2. **数据文件**：默认 SQLite 为 **`buyer_agent.db`**、浏览器目录 **`browser_data`**、媒体缓存 **`media_cache`** 等会在运行目录下生成，注意备份与隐私。  
3. **详细设计**：业务状态机、模块划分与参数说明见仓库内 `DESIGN.md`。

---

## 许可证与合规

使用本工具时请遵守**所使用平台**的用户协议与当地法律法规；自动化行为可能受平台规则限制，请自行评估风险。
