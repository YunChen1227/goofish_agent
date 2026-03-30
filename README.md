# Goofish Buyer Agent（闲鱼买家 Agent）

自动化在闲鱼上按你的条件搜索商品、评估、沟通与谈价的代理程序。提供 **命令行** 与 **HTTP API** 两种使用方式。

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

配置通过 **`.env` 文件** 或环境变量读取，变量名前缀为 **`GOOFISH_`**（见 `config/settings.py`）。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GOOFISH_OPENAI_API_KEY` | OpenAI 兼容 API 密钥（多模态/LLM） | 无 |
| `GOOFISH_OPENAI_BASE_URL` | API 基地址 | `https://api.openai.com/v1` |
| `GOOFISH_VLM_MODEL` / `GOOFISH_LLM_MODEL` | 视觉与文本模型名 | `gpt-4o` |
| `GOOFISH_DEEPSEEK_API_KEY` | DeepSeek（如代码中有使用） | 无 |
| `GOOFISH_DATABASE_URL` | 数据库连接串 | `sqlite:///goofish_agent.db` |
| `GOOFISH_BROWSER_HEADLESS` | 是否无头浏览器 | `False`（有界面，便于登录） |
| `GOOFISH_BROWSER_DATA_DIR` | 浏览器用户数据目录 | `browser_data` |
| `GOOFISH_PROXY_SERVER` | 可选 HTTP 代理 | 无 |
| `GOOFISH_MEDIA_DIR` | 媒体缓存目录 | `media_cache` |
| `GOOFISH_ENCRYPTION_KEY` | 可选，用于敏感信息加密 | 无 |
| `GOOFISH_SMTP_*` / `GOOFISH_WEBHOOK_URL` | 邮件或 Webhook 通知（若启用） | 见 `settings.py` |

评分权重、各接口每小时请求上限等也在 `settings.py` 中，可通过同名 `GOOFISH_` 环境变量覆盖。

---

## 如何运行

### 1. 工作目录与模块路径

本包导入形式为 `goofish_agent.xxx`。请在 **`goofish_agent` 文件夹的上一级** 执行命令（例如若路径为 `d:\workspace\goofish_agent`，则在 `d:\workspace` 下执行）。

若遇 `ModuleNotFoundError: goofish_agent`，可设置：

```bash
# Windows PowerShell
set PYTHONPATH=D:\workspace  # 改为你的上一级目录

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

必填参数：

- `--keywords`：搜索关键词  
- `--max-price`：可接受的最高价格（元）  
- `--target-price`：目标/心理价位（元）  

常用可选参数：

- `--condition`：成色要求，对应 `ConditionGrade` 枚举名，默认 `LIKE_NEW`（如 `SEALED`、`LIKE_NEW`、`LIGHTLY_USED` 等）  
- `--location`：地域筛选  
- `--reference-images`：零个或多个参考图路径（可多次或空格分隔，视你 shell 而定）  
- `--config`：JSON 配置文件路径；文件中的 `keywords`、`max_price`、`target_price`、`condition`、`location`、`reference_images` 可覆盖命令行，其余键会合并进任务模型  

示例：

```bash
python -m goofish_agent.main run ^
  --keywords "iPhone 15 Pro" ^
  --max-price 6000 ^
  --target-price 5500 ^
  --condition LIKE_NEW
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

创建成功后任务在后台运行；数据库在首次访问时初始化（`init_db()`）。

---

## 使用流程提示

1. **首次使用**：`GOOFISH_BROWSER_HEADLESS=false` 时可在窗口内完成闲鱼登录（扫码或手动）；程序会依赖浏览器会话访问平台。  
2. **数据文件**：默认 SQLite 库名 `goofish_agent.db`、浏览器配置目录 `browser_data`、媒体缓存 `media_cache` 等会在运行目录下生成，注意备份与隐私。  
3. **详细设计**：业务状态机、模块划分与参数说明见仓库内 `DESIGN.md`。

---

## 许可证与合规

使用本工具时请遵守闲鱼平台用户协议与当地法律法规；自动化行为可能受平台规则限制，请自行评估风险。
