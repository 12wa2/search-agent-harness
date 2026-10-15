# 贡献指南（Contributing）

感谢你有兴趣改进 search-agent-harness！这个仓库的定位是「可读、可测、可解释」的
Agent Harness，欢迎 bug 修复、文档改进，以及新后端 / 新 runner 的接入示例。

## 环境准备

```bash
git clone https://github.com/12wa2/search-agent-harness.git
cd search-agent-harness
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # 核心 + 测试 / 静态检查
```

可选 extras（按需）：

```bash
pip install -e ".[openai]"       # 真实 LLM（任何 OpenAI 兼容端点）
pip install -e ".[api]"          # FastAPI HTTP 服务（search-agent serve）
pip install -e ".[langgraph]"    # LangGraphRunner（同一批阶段编译成 StateGraph）
pip install -e ".[redis]"        # 会话记忆 / 结果分页
```

## 日常开发命令

```bash
pytest -q                        # 全部测试（离线、确定性、不需要 API key）
ruff check .                     # 静态检查（E / F / I / B）
ruff format .                    # 统一格式（line-length 100）
search-agent demo                # 离线端到端演示
search-agent eval --markdown     # 单趟基线 vs 全循环：效果 / 成本对比
search-agent demo --json         # 原始 JSON 响应（含 agentTrace 与质量指标）
```

## 提交与 PR 规范

- 小步提交，一个 PR 只做一件事；保持 `main` 全绿（CI = ruff + pytest + demo 冒烟）。
- 提交信息用祈使句 + 前缀：`feat:` / `fix:` / `docs:` / `test:` / `refactor:`，
  例如 `feat: add explainable rerank fallback`。
- 新行为必须带测试；**降级矩阵的每条分支**与**预算边界**是重点覆盖对象。
- 改动公共行为时同步更新 `README.md` / `docs/ARCHITECTURE.md` / `docs/DECISIONS.md`
  （设计取舍写成 ADR 条目，编号顺延）。

## 常见扩展点

| 想做什么 | 改哪里 |
|---|---|
| 接自己的检索后端 | 实现 `SearchTool` 两个方法（见 `examples/custom_backend.py`） |
| 换 LLM / 模型 | 实现 `LlmClient.complete_json` 或配置 `SEARCH_AGENT_*` 环境变量 |
| 加 / 改阶段 | `stages/<阶段>.py` 单文件 + 对应测试 |
| 换执行后端 | 实现 `HarnessRunner.run` 协议（参考 `graph.py` 的 `LangGraphRunner`） |
| HTTP 部署 | `server.py` + `search-agent serve`（`.[api]` extra） |

细节设计见 `docs/ARCHITECTURE.md`；裁剪取舍的理由见 `docs/DECISIONS.md`。

## 录制演示 GIF

仓库包含 [vhs](https://github.com/charmbracelet/vhs) 脚本 `docs/demo.tape`：

```bash
vhs docs/demo.tape               # 生成 docs/demo.gif（需先安装 vhs）
```

## 行为准则

保持友善、就事论事；讨论设计取舍时优先给可验证的证据（测试 / 数据 / 复现步骤）。
