# 数字资产 AI 助手 MVP

这是小组作业的 AI 后端。问卷组提交五维测评，AI 按需查询行情和中台账户快照。前端可调用对话获取投教与模拟建议，也可单独读取持仓风险摘要。

## 本地启动

需要 Python 3.10 或更新版本，运行时只使用标准库。以下命令在项目目录的两个 PowerShell 窗口运行。

```powershell
python -m ai_mvp
```

```powershell
python -X utf8 -m ai_mvp.demo
```

演示脚本提交两份虚构的五维测评和两条模拟报价，再让新手和有基础的用户问同一个行情问题，并分别请求模拟方案。默认以离线模板回答，便于无模型联调。响应中的 `generation` 可以辨认实际走了哪条通道。服务默认在 `127.0.0.1:8000` 监听，`--port` 可改端口。测评默认存入 `.ai_mvp/profiles.sqlite3`，`--db` 可改数据库文件。推送的行情保存在进程内，重启后由上游重新推送。对话历史由前端保管。

## 模型双备份

服务按 **本地 Ollama → 显式启用的 API Key 通道 → 离线模板** 的顺序选择。可以只配其中一条模型通道。每轮都会重新读取最新画像，再把白名单字段作为系统提示的一部分。对话内容和前端传入的历史放在用户消息中。

### 本地 Ollama

先启动 Ollama 桌面应用或在另一个终端运行 `ollama serve`，再使用 `ollama list` 中显示的完整模型名称。本机已验证 `qwen3.5:9b-q4_K_M` 可以推理。默认连接 `http://127.0.0.1:11434`，启动 Ollama 的进程可用 `OLLAMA_NO_CLOUD=1` 禁用云功能。AI 服务在发送提示前检查所选模型是否已安装且为本地模型。

```powershell
python -m ai_mvp --ollama-model qwen3.5:9b-q4_K_M
```

也可用 `AI_MVP_OLLAMA_MODEL` 配置模型名称。`AI_MVP_OLLAMA_URL` 只支持 HTTP 回环地址，`AI_MVP_OLLAMA_TIMEOUT_SECONDS` 默认 90 秒。演示时用 `python -X utf8 -m ai_mvp.demo --require-local-model` 验证推理结果。[Ollama Chat API](https://docs.ollama.com/api/chat) · [Ollama 本地模式说明](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)

### API Key 通道

默认远程服务为 OpenAI 的 `https://api.openai.com/v1`，模型为 `gpt-6-luna`。OpenAI API 与 ChatGPT 套餐分开计费，该模型的标准文本价格为每百万输入 token $0.10、每百万输出 token $0.50。一次约 1,000–5,000 输入 token、最多 320 输出 token 的普通短请求约 $0.00026–$0.00066。模型费用取决于实际 token 数。[GPT-6 Luna 定价](https://developers.openai.com/api/docs/models/gpt-6-luna)

拿到有调用权限的 OpenAI Key 后，用 PowerShell 隐藏输入并启动服务。密钥只交给服务端进程。以下命令启用本地优先、远程备用两条通道。

```powershell
$apiSecret = Read-Host 'OpenAI API key' -AsSecureString
try {
    $env:AI_MVP_REMOTE_API_KEY = [Net.NetworkCredential]::new('', $apiSecret).Password
    python -m ai_mvp --ollama-model qwen3.5:9b-q4_K_M --enable-remote
} finally {
    Remove-Item Env:AI_MVP_REMOTE_API_KEY -ErrorAction SilentlyContinue
    $apiSecret.Dispose()
}
```

要单独验证 API 通道，省略 `--ollama-model` 并确保当前终端没有设置 `AI_MVP_OLLAMA_MODEL`。服务启动后，在另一窗口发起一条普通对话。`generation` 为 `remote_model` 表示本次回复来自远程模型。如果 AI 服务配置了 `AI_MVP_API_TOKEN`，请求还需要附上 AI 服务的 Bearer 凭证。

```powershell
$body = @{ user_id = 'demo-api'; message = 'Explain market liquidity in one sentence.' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/v1/chat' -ContentType 'application/json' -Body $body |
    Select-Object generation, reply
```

可以用 `AI_MVP_REMOTE_ENABLED=1` 替代 `--enable-remote`。换用其他服务商时，将 `AI_MVP_REMOTE_BASE_URL` 和 `AI_MVP_REMOTE_MODEL` 设为该服务商的 HTTPS 服务地址和模型 ID，服务需要兼容 `/v1/chat/completions` 和 `max_tokens`。`AI_MVP_REMOTE_TIMEOUT_SECONDS` 默认 30 秒。每次对话的远程通道至多尝试一次。

远程通道收到普通聊天的用户消息、前端所传历史、派生画像和检索到的行情。触发账户查询或历史中识别出持仓、挂单等词时，只走本地模型或离线模板。前端应将账户详情卡与普通聊天历史分开保存和发送。`generation` 的可能值为 `local_model`、`remote_model`、`template`、`template_model_unavailable`、`template_fact_guard`、`template_assessment_guard`、`rule_based`。[兼容接口参考](https://developers.openai.com/api/reference/resources/chat)

## 小组联调契约

可把 [接口联调.md](接口联调.md) 发给各组。里面包含请求方向、每个字段的类型和示例，以及空数据和接口故障的处理约定。

以下接口使用 UTF-8 JSON。`GET /health` 返回服务和通道配置状态。设置 `AI_MVP_API_TOKEN` 后，其他接口要求 `Authorization: Bearer <token>`。跨机器联调可用 `python -m ai_mvp --host <本机内网IPv4> --port 8000`，此时必须配置服务凭证。填入的地址必须是 AI 运行机器的局域网地址。接真实中台同样要求服务凭证。业务后端持有服务凭证，从登录会话确认 `user_id`，再代浏览器调用。

### AI 提供的接口

- `PUT /v1/profiles/{user_id}` 和 `GET /v1/profiles/{user_id}` 分别用于写入和读取用户测评。
- `POST /v1/chat` 返回投教或一般解释。传 `include_simulation: true` 可请求结构化模拟建议，传 `include_account: true` 可在对话中带上只读账户快照。
- `GET /v1/risk/{user_id}?quote_currency=USDT` 返回以指定币种计价的持仓风险摘要。计价币种默认 `USDT`。
- `GET /v1/market/quotes?q=BTC%2FUSDT&limit=5` 查询 AI 当前可取得的行情。
- `POST /v1/market/quotes` 接收价格推送，适用于上游推送或本地演示。

### AI 需要的上游接口

- 问卷组在完成评分后，调用 AI 的 `PUT /v1/profiles/{user_id}`，提交派生测评。双方使用同一个用户 ID。问卷组提供提交结果即可。
- 行情组开放只读 `GET /quotes?q=<查询>&limit=<条数>`，按下方结构返回命中的现货交易对和最新报价。配置 `AI_MVP_MARKET_URL` 和可选的 `AI_MVP_MARKET_TOKEN` 后，AI 主动调用该接口。
- 中台组开放只读 `GET /accounts/{user_id}/snapshot`，按下方结构返回账户快照。配置 `AI_MVP_TRADING_URL` 和可选的 `AI_MVP_TRADING_TOKEN` 后，AI 主动调用该接口。

每组还需给出服务基础 URL、鉴权方式和一组可复用的测试数据。交易对统一使用 `BTC/USDT` 的形式，时间统一使用含时区的 ISO 8601 时间，价格及数量建议传十进制字符串。

### 问卷组

`PUT /v1/profiles/{user_id}` 写入或替换当前画像，`GET` 同一路径读出。`user_id` 支持最多 64 个 ASCII 字母、数字、下划线或连字符。问卷端完成计分并提交派生测评。下面的字段名是本原型的对接约定。

```json
{
  "questionnaire_version": "1.0.0",
  "source": "questionnaire",
  "assessment": {
    "dimensions": {"FC": 62, "RA": 65, "KE": 75, "BD": 60, "OC": 55},
    "rai": 63,
    "risk_level": "C3",
    "product_cap": "R3",
    "allocation_range": {"min_pct": 5, "max_pct": 15},
    "flags": ["short_horizon"],
    "prohibitions": []
  }
}
```

- `FC`、`RA`、`KE`、`BD`、`OC` 分别表示财务承受力、风险偏好、知识与经验、行为纪律、目标与约束。五项均为 0–100 的归一化分数。
- `rai` 是问卷端计算好的 0–100 整数。AI 服务验证财务承受力上限、C1–C5 的分段以及 C 档与 R 档的映射，保留问卷端算出的 RAI。原始作答不进入数据库或模型上下文。
- `allocation_range` 是可选百分比区间。`flags` 和 `prohibitions` 是可选代码数组，每组最多 16 个代码。自由文本和未约定字段会被丢弃。
- 本原型以 `KE < 45` 进入 `education`，其余进入 `advisory`。这是助手的分流规则。上游如果另有独立的财富档，可在外层 `dimensions.wealth_band` 提供 `low`、`medium` 或 `high`。
- `GET` 原样返回规范化后的 `assessment`，并带上 `risk_ceiling`、`route`、`reasons`、`missing_fields` 和 `updated_at`。数据库沿用 `profiles` 表的 JSON 列，已有画像可以直接读取。

旧版调用仍可提交仅含 `dimensions` 的请求。旧字段 `risk_willingness`、`risk_capacity`、`wealth_band` 和 `knowledge_level` 继续使用 `low`、`medium`、`high`，并沿用原分流和风险上限规则。

### 行情组

AI 主动请求 `GET {AI_MVP_MARKET_URL}/quotes?q=BTC%2FUSDT&limit=5`，默认单次请求超时 2 秒。`q` 支持完整交易对、币种代码或名称，`limit` 为 2–20。完整交易对仅返回完全相同的交易对。代码或名称对应多个交易对时，请在数量允许的范围内至少返回两条，并附用于匹配的 `name` 或 `aliases`。无匹配时返回 HTTP 200 和 `{"quotes":[]}`。

以下是行情查询响应，也是可选推送接口 `POST /v1/market/quotes` 接受的请求格式。推送单批最多 500 条。`as_of` 填行情采集时刻。

```json
{
  "quotes": [{
    "symbol": "BTC/USDT",
    "price": "68000.50",
    "as_of": "2026-11-09T10:00:00Z",
    "source": "demo",
    "name": "Bitcoin",
    "aliases": ["比特币", "大饼"]
  }]
}
```

配置主动查询后，上游故障时才回退到进程内的推送行情。回退结果带 `upstream_status: unavailable`，没找到可用记录时状态为 `unavailable`。报价时间比服务时钟快超过 15 秒时拒收。新价格推送可省略 `name` 和 `aliases` 以保留原有检索信息。同一交易对的旧推送会被忽略。`source` 为 `demo` 时，对话标为模拟报价。多交易对命中时提示确认计价币种。每条报价带 `freshness` 和 `age_seconds`，只有唯一精确命中且新鲜的报价进入模型上下文。`AI_MVP_QUOTE_MAX_AGE_SECONDS` 默认 120 秒。联调时前端可显式传 `market_queries`，确保首次请求也能按指定交易对查询。

### 前端对话

`POST /v1/chat`。交易对可从自然语言识别，也可以由前端在 `market_queries` 中显式指定。`history` 可传最近六条 `user` 或 `assistant` 消息。`include_account` 可显式请求读取账户。“看看我的持仓”等账户查询也会触发读取。

```json
{
  "user_id": "demo-learner",
  "message": "比特币现在多少钱？波动是什么意思？",
  "market_queries": ["BTC/USDT"],
  "include_account": false,
  "include_simulation": false,
  "history": []
}
```

响应包含 `reply`、`generation`、`mode`、`risk_ceiling`、`profile_reasons`、`profile_updated_at`、`trace_id`、`market` 和 `account`。行情及账户数值以结构化字段为准，`reply` 同时包含模型或模板生成的解释，以及后端拼接的事实。行情或账户请求中，若模型解释含数字，后端用模板替换解释并保留原始事实。其余生成文本通过提示约束，用于课堂演示。

有五维测评时，`risk_ceiling` 返回 C 档，`product_cap` 返回 R 档。前端设置 `include_simulation: true` 可确定性请求模拟方案。聊天中的常见建议意图也可触发这条路径。后端按 KE 路由和产品上限筛选假设情景，每个情景返回 `product_risk` 和 `references`。有上游禁止项时暂缓全部模拟推荐，等待问卷组提供禁项与场景的对应关系。普通对话使用提示和文本规则拦截常见交易指令及超出上限的 R 档。前端使用结构化 `simulation` 展示经过后端筛选的模拟方案。

### 交易中台

设置 `AI_MVP_TRADING_URL` 和可选的 `AI_MVP_TRADING_TOKEN` 后，助手可读取 `GET {AI_MVP_TRADING_URL}/accounts/{user_id}/snapshot`，默认 2 秒超时。中台应返回同一时点的 `positions` 和 `open_orders`；没有数据时返回空数组。现货持仓数量以基础资产计。若只返回了部分数据，设置 `truncated: true`。以下是最小响应示例。

```json
{
  "as_of": "2026-11-09T10:00:00Z",
  "positions": [{"symbol": "BTC", "quantity": "0.25"}],
  "open_orders": [{"symbol": "ETH/USDT", "side": "buy", "quantity": "0.10", "limit_price": "3000", "status": "open"}]
}
```

### 持仓风险摘要

`GET /v1/risk/{user_id}?quote_currency=USDT` 先读取账户快照，再按持仓使用精确交易对和新鲜报价估算敞口。响应包含 `state`、`reasons`、`account_as_of`、逐笔估值 `positions` 和 `summary`。`state` 为 `ok` 时，摘要提供净值 `net_value`、总敞口 `gross_exposure`，以及敞口非零时的最大持仓集中度 `largest_asset`。`partial` 时只展示已覆盖持仓的 `observed_*` 指标。中台不可用或未配置时，`summary` 为 `null`。估值按现货数量与参考报价计算，挂单只计数。每次最多查询 10 个不同交易对，超过上限的持仓标为未估值。

## 测试

```powershell
python -B -m unittest discover -s tests -v
```

覆盖画像更新、服务路径、行情检索与上游故障、账户只读接口、持仓风险摘要和模型降级。测试中的上游服务和模型响应均为模拟数据。
