# Project Progress

> Canonical snapshot as of 2026-04-03.

这份文件是当前项目最详细的工作台账，用于防止后续上下文压缩时丢失关键事实。

## 协作约定

从这一轮开始，`PROJECT_PROGRESS.md` 需要持续承担下面这几种作用：

1. 记录“准备做什么”
2. 记录“当前做到哪了”
3. 记录“还有什么没做完”
4. 记录“做过哪些测试”
5. 记录“已知边界 / 错误 / 解决方法”
6. 在上下文压缩后，作为恢复现场的第一参考文件

也就是说，后续每一轮较大改动都应该优先补到这里，而不是只依赖聊天上下文。

## 当前工作状态

### 当前主目标

- 保持现有真实相图和 LAMMPS 主链路可用
- 继续扩充真实可计算的 TDB 体系
- 扩大 thermo RAG 覆盖面，但不让 RAG 接管执行
- 继续把 memory 做成更稳的会话层，而不是额外 agent

### 当前已完成

- 前后端分离结构稳定
- 4-agent 架构稳定
- memory 已升级到 `Memory v2`
- thermo registry 已从 `3` 个体系扩到 `10` 个体系
- thermo RAG v1 已接入
- LAMMPS 本地执行与 OVITO 后处理已打通

### 当前正在做

- 继续筛选更多安全可用的 `TDB`
- 当前重点候选：
  - `Al-Cu`
  - 以及其他可从多元公开 TDB 中安全抽出的二元子空间
- 目标不是盲目加数量，而是：
  - 能真实计算
  - 能过 accuracy gate
  - 能通过端到端验证

### 当前待做

- 再扩 `1-2` 个稳定二元系
- 继续扩充 thermo RAG 文档
- 评估是否生成真正的 `thermo_rag_documents.jsonl` 构建脚本
- 对新增体系补一轮 live API 验证并记录 run_id

### 当前明确不做

- 不让 thermo RAG 直接决定执行数据库
- 不把 memory 单独做成 agent
- 不为了扩库而引入明显不稳或 accuracy 过不了的 TDB

## 2026-04-03 Memory v2 + Thermo Registry 扩容

### 本轮新增

- `memory` 模块升级为 `Memory v2`，但仍然**不是**独立 agent：
  - `MemorySnapshot` 新增
    - `session_title`
    - `last_user_message`
    - `message_count`
    - `asset_count`
    - `summary_version`
  - `MemoryStore` 的摘要现在会同时压缩：
    - 最近会话内容
    - recognition 摘要
    - last run 摘要
  - 目标是让会话级 follow-up 更稳，同时保留 `memory = state/persistence/summary` 这条主线
- Thermodynamic registry 从 `3` 个体系扩到 `5` 个体系：
  - `Al-Zn`
  - `Al-Mg`
  - `Al-Ni`
  - `Pb-Sn`
  - `Al-Fe`
- 新增 TDB 文件：
  - `backend/configs/thermo_databases/pbsn.tdb`
  - `backend/configs/thermo_databases/Al-Fe_sundman2009.tdb`
- thermo RAG 同步扩容：
  - registry card 检索现在可召回 `Pb-Sn` 和 `Al-Fe`
  - `backend/configs/thermo_rag_documents.example.jsonl` 已补充新体系的 `system_card / phase_card / provenance_card / tdb_chunk`
- `verify_phase_diagram_cases.py` 已支持新增体系：
  - `Pb-Sn`
  - `Al-Fe`

### 本轮准备做什么

- 提升会话级 memory 的信息密度和可恢复性
- 在不破坏现有真实计算链路的前提下扩充 thermo registry
- 把新体系同步接入 thermo RAG 文档和检索

### 本轮做到哪了

- `memory` 模块已经完成 `Memory v2`
- 新增 `Pb-Sn` 和 `Al-Fe`
- thermo RAG 文档已经补到新体系
- 回归测试和端到端验证已经完成

### 本轮还没做完什么

- 还没有进一步把 registry 扩到 `7+` 个稳定体系
- 还没有把 thermo RAG builder 自动化脚本写出来

### 本轮验证

- 后端单元测试：
  - `backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `40` 个测试全部通过
- 新增 memory 测试：
  - `backend/tests/test_memory_store.py`
- 新体系准确率锚点验证：
  - `Pb-Sn`
    - `passes = true`
    - endpoint estimates: `602.5 K / 502.5 K`
  - `Al-Fe`
    - `passes = true`
    - endpoint estimates: `927.5 K / 1812.5 K`
- 新体系本地端到端验证（`TestClient + scripted LLM + 真实 pycalphad + TDB`）：
  - `Pb-Sn`
    - `success = true`
    - `route = phase_diagram.generate`
    - `termination_reason = review_passed`
    - `accuracy_passed = true`
  - `Al-Fe`
    - `success = true`
    - `route = phase_diagram.generate`
    - `termination_reason = review_passed`
    - `accuracy_passed = true`
- thermo RAG 检索验证：
  - 查询 `我想计算铅锡二元相图并查看共晶附近的液相线`
    - `matched = true`
    - `selected_system_name = Pb-Sn`
    - `selection_strategy = rag_auto_select`
  - 查询 `请帮我找一个铝铁二元相图数据库，并关注FCC_A1和金属间化合物相区`
    - `matched = true`
    - `selected_system_name = Al-Fe`
    - `selection_strategy = rag_auto_select`

### 本轮已知边界

- 新增体系验证主要基于：
  - `TestClient`
  - `scripted LLM`
  - 真实 `pycalphad + TDB`
- 这说明代码链路是通的，但不等于外部 LLM 配额一定稳定
- 当前外部 LLM 仍可能受供应商 `429 quota/throttling` 影响

## 2026-04-03 第二批 TDB 扩库（已落地两项）

### 本轮准备做什么

- 继续从本地 `pycalphad` 自带数据库和公开可复用来源中筛选更多稳定二元系
- 在不破坏当前真实执行主链路的前提下，把“可交差的相图体系数量”继续往上扩
- 先做保守扩库：只收真正通过 accuracy gate 的体系

### 本轮做到哪了

- 新增 `component_selection` 支持：
  - `backend/app/thermo/registry.py`
  - 允许一个多元 `.tdb` 文件为多个二元子空间服务
- 新增非标准液相名支持：
  - `backend/app/thermo/accuracy.py`
  - 允许在 `accuracy_reference` 里声明 `liquid_phase_name` / `liquid_phase_names`
- 新增 TDB 文件：
  - `backend/configs/thermo_databases/COST507-modified.tdb`
  - `backend/configs/thermo_databases/cumg.tdb`
  - `backend/configs/thermo_databases/FeNi_deep_branching.tdb`
  - `backend/configs/thermo_databases/nbre_liu.tdb`
- 正式接入的新体系：
  - `Cu-Ni`
  - `Nb-Re`
- thermo registry 已从 `5` 个体系扩到 `7` 个体系：
  - `Al-Zn`
  - `Al-Mg`
  - `Al-Ni`
  - `Pb-Sn`
  - `Al-Fe`
  - `Cu-Ni`
  - `Nb-Re`
- thermo RAG 示例文档同步扩充，新增：
  - `Cu-Ni` 的 `system_card / phase_card / provenance_card / tdb_chunk`
  - `Nb-Re` 的 `system_card / phase_card / provenance_card / tdb_chunk`

### 本轮验证结果

- `Cu-Ni`
  - 数据库：`COST507-modified.tdb`
  - 形式：从多元数据库抽出的二元子空间
  - 稳定相：`FCC_A1`, `LIQUID`
  - endpoint estimate：
    - `Cu side ~ 1357.5 K`
    - `Ni side ~ 1727.5 K`
  - 结论：`passes = true`
- `Nb-Re`
  - 数据库：`nbre_liu.tdb`
  - 关键点：液相名不是标准 `LIQUID`，而是 `LIQUID_RENB`
  - 稳定相：`BCC_RENB`, `CHI_RENB`, `HCP_RENB`, `LIQUID_RENB`, `SIGMARENB`
  - endpoint estimate：
    - `Nb side ~ 2747.5 K`
    - `Re side ~ 3437.5 K`
  - 结论：`passes = true`
- `Cu-Mg`
  - 数据库：`cumg.tdb`
  - 左端点通过，但右端点 Mg 熔点估计偏差过大
  - 结论：`暂不接入主链路`
- 第二批回归后端测试：
  - `backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `42` 个测试全部通过

### 本轮还没做完什么

- `Al-Cu` 仍在候选中，但还没有得到可接受的 accuracy 结果
- 新增体系的 live `/api/agent/chat` 真实 API run_id 还没有全部记录完成

### 本轮已知边界 / 错误 / 解决方法

- `nbre_liu.tdb` 使用了非标准液相名 `LIQUID_RENB`
  - 已通过 `accuracy.py` 的自定义液相名支持解决
- `COST507-modified.tdb` 是多元数据库
  - 已通过 `registry.py` 的 `component_selection` 支持解决二元子空间执行问题
- `Cu-Mg` 当前 accuracy gate 不稳
  - 结论是不接主链路，不硬塞进 registry

### 如果上下文被压缩，下一步从这里继续

优先继续：

1. 对新加的 `Cu-Ni / Nb-Re / Cr-Fe / Fe-Nb / Cr-Nb` 跑一轮 live `/api/agent/chat` 验证
2. 继续筛 `Al-Cu` 或其它安全候选
3. 如果再有通过项，再：
   - 更新 `thermo_registry.json`
   - 更新 `thermo_rag_documents.example.jsonl`
   - 重新跑后端全量测试

## 2026-04-03 第三批 TDB 扩库（CrFeNb 三个二元子空间）

### 本轮准备做什么

- 继续扩大“能真实计算的相图体系数”
- 优先筛选一个相集合小、液相命名标准、适合二元子空间抽取的公开数据库
- 目标是一次性再加 `2-3` 个稳定体系，而不是盲目找零散小库

### 本轮做到哪了

- 从本地 `pycalphad` 自带测试数据库中选中：
  - `CrFeNb_Jacob2016.tdb`
- 已将该文件复制到项目路径：
  - `backend/configs/thermo_databases/CrFeNb_Jacob2016.tdb`
- 从该三元数据库中验证并接入了 3 个二元子空间：
  - `Cr-Fe`
  - `Fe-Nb`
  - `Cr-Nb`
- thermo registry 已正式扩到 `10` 个体系
- thermo RAG 示例文档已同步补入上述 3 个体系
- `verify_phase_diagram_cases.py` 已加入：
  - `Cr-Fe`
  - `Fe-Nb`
  - `Cr-Nb`
- 接口契约测试已更新为：
  - registry count `>= 10`

### 本轮验证结果

- `Cr-Fe`
  - 稳定相：`BCC_A2`, `FCC_A1`, `LIQUID`, `SIGMA`
  - endpoint estimate：
    - `Cr side ~ 2177.5 K`
    - `Fe side ~ 1807.5 K`
  - 结论：`passes = true`
- `Fe-Nb`
  - 稳定相：`BCC_A2`, `FCC_A1`, `LAVES_C14`, `LIQUID`, `MU_PHASE`
  - endpoint estimate：
    - `Fe side ~ 1802.5 K`
    - `Nb side ~ 2737.5 K`
  - 结论：`passes = true`
- `Cr-Nb`
  - 稳定相：`BCC_A2`, `LAVES_C15`, `LIQUID`
  - endpoint estimate：
    - `Cr side ~ 2177.5 K`
    - `Nb side ~ 2747.5 K`
  - 结论：`passes = true`
- 后端全量测试：
  - `backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `42` 个测试全部通过
- live registry 检查：
  - `GET /api/thermo/registry`
  - `count = 10`
  - 说明运行中的后端已经吃到最新 registry

### 本轮已知边界 / 错误 / 解决方法

- 运行中的后端最初还是旧进程，live registry 还只显示 `5` 个体系
  - 解决：重启后端进程，再验证 `GET /api/thermo/registry`
- `CrFeNb_Jacob2016.tdb` 最初只在 `.venv` 的 test-databases 目录
  - 解决：复制到项目自己的 `backend/configs/thermo_databases/`

### 本轮还没做完什么

- 还没有给 `Cr-Fe / Fe-Nb / Cr-Nb` 跑 live `/api/agent/chat` 并记录最终 run_id
- `Al-Cu` 仍然未定
- thermo RAG 仍然是 registry-backed retrieval enhancement，还没有接入真正 embedding 索引

## 2026-04-03 第二批 TDB 扩库筛选（进行中）

### 本轮准备做什么

- 继续从本地 `pycalphad` 自带数据库和公开可复用来源中筛选更多稳定二元系
- 目标是把“可真实计算的相图范围”进一步扩大，方便项目交付

### 当前筛选策略

- 先看数据库是否真的是二元或可抽出稳定二元系
- 再看相名是否适合当前主链路
- 再做 accuracy gate 试算
- 最后才决定是否接入 registry

### 当前已筛到的候选

- `cumg.tdb`
  - 组件：`CU, MG`
  - 相：`CU2MG, CUMG2, FCC_A1, HCP_A3, LIQUID`
  - 结论：
    - 候选质量较好
    - 适合优先做下一轮 accuracy 验证
- `FeNi_deep_branching.tdb`
  - 组件：`FE, NI`
  - 相：`FCC_A1, ORD_FCC`
  - 结论：
    - 体系较简单
    - 需要确认是否适合当前液相 endpoint gate
- `nbre_liu.tdb`
  - 组件：`NB, RE`
  - 相：`BCC_RENB, CHI_RENB, FCC_RENB, HCP_RENB, LIQUID_RENB, SIGMARENB`
  - 结论：
    - 有真实液相，但相命名不是标准 `LIQUID`
    - 如果要接，需要先确认当前 accuracy 逻辑是否允许非标准液相名
- `cfe_broshe.tdb`
  - 组件：`C, FE`
  - 相：`BCC_A2, CEMENTITE_D011, DIAMOND_A4, FCC_A1, GRAPHITE, HCP_A3, LIQUID, M7C3_D101`
  - 结论：
    - 工程上有价值
    - 但体系和当前简单二元金属逻辑差异较大
    - 需要谨慎，不建议直接作为下一批第一优先级
- `femn.tdb`
  - 组件：`FE, MN`
  - 相：当前读到的稳定相信息过少，仅看到 `LIQUID`
  - 结论：
    - 暂不推荐直接接入

### 当前做到哪了

- 已完成候选数据库的 metadata 初筛
- 已完成候选体系第一轮人工排序
- 下一步将优先验证：
  - `Cu-Mg`
  - `Fe-Ni`
  - 再视情况决定是否处理 `Nb-Re`

### 这一轮还没做完

- 还没对第二批候选跑完整 accuracy gate
- 还没把通过验证的候选写回 registry
- 还没把新增候选同步写进 thermo RAG 文档

### 如果上下文被压缩，下一步从这里继续

优先继续：

1. 对 `cumg.tdb` 跑 accuracy 验证
2. 对 `FeNi_deep_branching.tdb` 评估是否适合当前 gate
3. 如果两者至少有一个稳定，通过后再：
   - 更新 `thermo_registry.json`
   - 补 `thermo_rag_documents.example.jsonl`
   - 跑后端全量测试
   - 跑 `TestClient + scripted LLM + real pycalphad/TDB` 端到端验证

## 2026-04-01 Thermo RAG v1

### 本轮新增

- 新增相图 `thermo RAG v1`，但没有替换现有 registry：
  - exact/alias 命中仍然优先
  - exact miss 才进入 RAG 检索
  - RAG 当前检索对象是结构化 thermo card，不是直接检索 `.tdb` 原文
  - 只有达到阈值时，才允许 `rag_auto_select`
  - 真执行仍然是：`registry card -> database_file -> pycalphad + TDB`
- 新增文件：
  - `backend/app/thermo/rag_models.py`
  - `backend/app/thermo/rag_index.py`
  - `backend/app/thermo/rag_retriever.py`
  - `backend/app/thermo/rag_service.py`
- 新增接口：
  - `POST /api/thermo/rag/search`
- 新增可交接的数据资产说明：
  - `docs/THERMO_RAG_SCHEMA.md`
  - `backend/configs/thermo_rag_documents.example.jsonl`
- 新增配置：
  - `PHASE_DIAGRAM_THERMO_RAG_ENABLED`
  - `PHASE_DIAGRAM_THERMO_RAG_TOP_K`
  - `PHASE_DIAGRAM_THERMO_RAG_MIN_SCORE`
  - `PHASE_DIAGRAM_THERMO_RAG_AUTO_SELECT_THRESHOLD`
  - `PHASE_DIAGRAM_THERMO_RAG_AUTO_SELECT_MARGIN`
  - `PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_BACKEND`
  - `PHASE_DIAGRAM_THERMO_RAG_EMBEDDING_MODEL`

### 当前设计原则

- 旧的 thermo registry 链路保留不动
- RAG 只做召回增强，不直接控制执行
- `.tdb` 文件仍然由 registry 决定和落盘
- RAG 效果不好时，可以随时关闭，不会影响当前真实计算路径

### 当前推荐的 embedding 模型

当前 v1 先使用 lexical + structured retrieval，避免为了引入 embedding 依赖而破坏稳定性。

如果后续升级到向量检索，我当前建议优先评估：

1. `BAAI/bge-m3`
2. `Qwen/Qwen3-Embedding-0.6B` 或更高规格版本

当前默认推荐写在配置里的是：

- `BAAI/bge-m3`

原因：

- 中英文混合查询更稳
- 更适合 thermo card 这种“结构化 metadata + 摘要文本”的检索对象
- 可以和未来的 hybrid retrieval 继续兼容

### 本轮验证

- 后端单元测试：
  - `backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `37` 个测试全部通过
- 新接口验证：
  - `POST /api/thermo/rag/search`
  - 对查询 `我想计算铝锌二元相图并查看液相线`
  - 已正确返回 `Al-Zn` 为第一候选
- service fallback 验证：
  - `lookup_registered_database("未知体系", query_text="我想计算一张铝锌二元相图，重点看看液相线和FCC_A1区域。")`
  - 已能通过 RAG 自动选中 `Al-Zn`
- 诚实失败验证：
  - 模糊且低置信查询仍会返回 `exact_then_rag` 的未命中结果，不会乱选数据库

## 2026-04-01 结果可信度与环境诊断

### 本轮新增

- 后端新增统一系统诊断接口：
  - `GET /api/system/diagnostics`
- 后端结果 summary 新增统一 `result_profile`
  - 相图侧：
    - `category = Calculated`
    - `source_label = pycalphad + TDB`
    - 带 `trust_level / confidence / trust_statement / warnings / evidence`
  - LAMMPS 侧：
    - `category = LAMMPS Simulated` 或 `LAMMPS Fallback`
    - 同样带 `trust_level / confidence / warnings / evidence`
- 前端结果面板已接入这套结果可信度信息
- 前端系统设置页已接入真实环境诊断信息：
  - LLM
  - Python Runtime
  - LAMMPS Runtime
  - OVITO
  - Thermodynamic Registry
  - Storage

### 本轮验证

- 后端单元测试：
  - `backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `31` 个测试全部通过
- 前端构建：
  - `frontend/npm run build`
  - 通过
- live 接口检查：
  - `GET /api/health` 正常
  - `GET /api/system/diagnostics` 已用本地 Python 请求验证通过

### 这轮的目的

- 不再只让用户看到“有没有结果”，而是明确告诉用户：
  - 这是什么类型的结果
  - 结果来自哪里
  - 可信度处在哪个级别
  - 当前有哪些假设和风险
- 同时把“为什么这台机器能跑 / 不能跑”做成一眼可见的诊断页，减少 Windows 或多机器部署时的排障成本

## 当前结论

这个仓库当前已经稳定在下面这个形态：

- 前后端分离：`frontend/ + backend/`
- 后端是真实 `4 agent`
- 真实相图链路：`pycalphad + TDB`
- 真实 LAMMPS 链路：`LAMMPS + OVITO`
- `memory` 只负责状态与持久化，不是独立 agent
- `lammps/` 目录保留为冻结参考实现，不参与当前主线运行

## 当前有效主目录

只应关注：

- `frontend/`
- `backend/`
- `docs/ARCHITECTURE.md`
- `README.md`
- `PROJECT_PROGRESS.md`

## 当前 4-agent 结构

当前有效文件：

- `backend/app/agents/supervisor.py`
- `backend/app/agents/recognition.py`
- `backend/app/agents/compute.py`
- `backend/app/agents/chat.py`
- `backend/app/graph.py`
- `backend/app/memory.py`
- `backend/app/state.py`

LangGraph 主节点：

- `load_memory`
- `SupervisorAgent`
- `RecognitionAgent`
- `ComputeAgent`
- `ChatAgent`
- `summarize_context`
- `save_memory`
- `respond`

## 当前计算域

### PhaseDiagramRuntime

入口：

- `backend/app/runtimes/phase_diagram.py`

约束：

- 必须先查 thermodynamic registry
- 未命中 TDB 直接诚实失败
- LLM 只生成薄 wrapper
- 本地 Python 执行 wrapper
- 真计算必须由 `pycalphad + TDB` 完成
- 末端必须通过 `review + accuracy gate`

### LammpsRuntime

入口：

- `backend/app/runtimes/lammps.py`

约束：

- 必须先做结构化请求解析
- 必须过 registry 和 validation
- 必须本地生成 `in.lammps`
- 必须本地执行 LAMMPS
- 后处理必须能产出图、轨迹和可视化媒体
- 末端必须经过 LLM review

## 当前 registry

### Thermodynamic registry

当前已注册体系：

1. `Al-Zn`
2. `Al-Mg`
3. `Al-Ni`

关键文件：

- `backend/configs/thermo_registry.json`
- `backend/configs/thermo_databases/alzn_mey.tdb`
- `backend/configs/thermo_databases/Al-Mg_Zhong.tdb`
- `backend/configs/thermo_databases/alni_dupin_2001.tdb`

### LAMMPS registry

当前 LAMMPS runtime 已有独立 registry / validator / template / runner / postprocess：

- `backend/app/lammps/registry.py`
- `backend/app/lammps/validator.py`
- `backend/app/lammps/template.py`
- `backend/app/lammps/runner.py`
- `backend/app/lammps/postprocess.py`

## 本轮真实验证

### 后端单元测试

- 命令：
  - `backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
- 结果：
  - `29` 个测试全部通过

### 相图真实计算

本轮重新验证通过：

- `Al-Zn`
  - `run_id = 95c16ae27e99`
- `Al-Mg`
  - `run_id = 1026d2c9c3af`
- `Al-Ni`
  - `run_id = 4d71f1772a0f`

共同特征：

- `route = phase_diagram.generate`
- `generation_source = llm_codegen_calculated_wrapper`
- `review_passed = true`
- `accuracy_passed = true`

### LAMMPS 真实执行

本轮重新验证通过：

- `Cu heating`
  - `run_id = 273aa67d98db`
- `Al heating`
  - `run_id = ef53e237dfcb`

共同特征：

- `route = lammps.generate`
- `run_mode = real`
- `review_passed = true`
- 已生成：
  - `thermo.csv`
  - `plot.png`
  - trajectory
  - `report.md`
  - `diffusion_trajectory.png`
  - `diffusion_trajectory_3d.gif`
  - `ovito.mp4`

## 文档收口结果

当前只保留：

- `README.md`
- `PROJECT_PROGRESS.md`
- `docs/ARCHITECTURE.md`

已删除的冗余/过时文档：

- `MAINTENANCE.md`
- `backend/README.md`
- `docs/INTERVIEW_PLAYBOOK.md`

## 当前边界

- 相图真实计算目前只覆盖 registry 中已有 TDB 的体系
- 识别链路仍是 MVP
- 前端可运行，但后续 UI 迭代可以独立进行
- 本轮发现旧浏览器 smoke 脚本仍依赖 route/status chip 的旧 UI 假设，后续如继续做前端自动化，需要按当前小状态栏行为更新脚本

## 2026-03-31 全流程回归

### 本轮修复

- 修复了相图请求解析器对 `300K-2000K` 这类双端都带单位温区的识别，文件：
  - `backend/app/thermo/service.py`
- 修复了 OVITO Python module 后处理在主 API 进程内不稳定的问题，改成子进程渲染，文件：
  - `backend/app/lammps/postprocess.py`
- 调整了 OVITO backend 检测逻辑，优先 `ovitos/ovito CLI`，否则退回 `python module`，不再误把桌面 App 当脚本后端，文件：
  - `backend/app/lammps/config.py`
- 为新版前端补回页面级 smoke 所需的轻量测试钩子，文件：
  - `frontend/src/app/AgentWorkbench.tsx`
  - `frontend/src/features/chat/AgentConversationPanel.tsx`
  - `frontend/src/shared/styles.css`
  - `backend/examples/frontend_smoke.mjs`
  - `backend/examples/frontend_snapshot.mjs`

### 本轮后端验证

- 后端单元测试：
  - `backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `29` 个测试全部通过
- 相图真实计算：
  - `Al-Zn`
    - `run_id = 20f4c16d2e7c`
    - `route = phase_diagram.generate`
    - `generation_source = llm_codegen_calculated_wrapper`
    - `accuracy_passed = true`
  - `Al-Mg`
    - `run_id = 84a9c8325ad8`
    - `route = phase_diagram.generate`
    - `generation_source = llm_codegen_calculated_wrapper`
    - `accuracy_passed = true`
  - `Al-Ni`
    - `run_id = 3c1dbf577101`
    - `route = phase_diagram.generate`
    - `generation_source = llm_codegen_calculated_wrapper`
    - `accuracy_passed = true`
- LAMMPS 真实执行：
  - `Cu heating`
    - `run_id = 703d711fcab9`
    - `run_mode = real`
    - 已生成 `trajectory + plot.png + report.md + diffusion_trajectory.png + diffusion_trajectory_3d.gif + ovito.mp4`
  - `Al heating`
    - `run_id = e20f80817786`
    - `run_mode = real`
    - 已生成 `trajectory + plot.png + report.md + diffusion_trajectory.png + diffusion_trajectory_3d.gif + ovito.mp4`

### 本轮前端页面级验证

- 前端构建：
  - `frontend/npm run build`
  - 通过
- 相图页面级 smoke：
  - 生成 `Al-Zn` 后继续追问“你刚刚生成了什么代码？”
  - `run_id = 87509d8e4cf4`
  - `iframeLength = 85313`
  - artifact 在 follow-up 后仍保留
  - 页面级确认能返回 `build_calculated_phase_diagram_report`
- LAMMPS 页面级恢复验证：
  - 从历史 run 加载 `e20f80817786`
  - 页面级确认：
    - `statusChips = ["ready", "lammps.generate", "completed"]`
    - `imageCards = 3`
    - `videoCards = 1`
  - 后端访问日志确认前端真实拉取了：
    - `plot.png`
    - `diffusion_trajectory.png`
    - `diffusion_trajectory_3d.gif`
    - `ovito.mp4`

## 2026-03-31 前端结果窗口与流式体验收口

### 本轮目标

- 放宽相图结果页的前端展示宽度，不再把 HTML 结果压成窄条
- 修正 OVITO 视频在前端不完整展示的问题
- 让 LAMMPS 结果从“全部堆叠”改成“单结果主预览 + 右侧导航”
- 保留单独的小状态栏和进度条，不把 agent 中间步骤塞进聊天记录

### 本轮前端改动

- 相图结果卡收口为宽窗口布局：
  - `frontend/src/features/result/ArtifactResultPanel.tsx`
  - phase artifact 最大宽度提升到 `1820px`
  - iframe 高度提升到 `820 / 900 / 980px`
  - 左侧主图，右侧关键信息栏
- LAMMPS 结果卡改为单结果预览：
  - `frontend/src/features/result/ArtifactResultPanel.tsx`
  - 只显示当前选中的一个结果
  - 右侧通过 `Result Navigator` 切换 `mp4 / plot / trajectory image / gif / report`
  - 视频和图片统一改成 `object-contain`
- markdown 报告读取补全：
  - `frontend/src/features/result/ArtifactResultPanel.tsx`
  - 通过 `getArtifactText(...)` 拉取 `report.md`
- 流式过程中的 LAMMPS artifact 继续在聊天流里就地更新，不重复插卡：
  - `frontend/src/features/chat/useAgentChat.ts`
- artifact 卡在运行中会收到 `isLoading` 状态：
  - `frontend/src/features/chat/AgentConversationPanel.tsx`

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 相图页面级 smoke：
  - prompt：`请生成一张 Al-Zn 二元相图，温度范围 300K-1000K，突出液相线以及 FCC_A1 和 HCP_A3 两个主要固相区。`
  - follow-up：`你刚刚生成了什么代码？`
  - 结果：
    - `iframeLength = 85313`
    - `iframeClientHeight = 980`
    - `iframeClientWidth = 860`
    - `artifactBubbleWidth = 1244`
    - follow-up 后 artifact 仍保留
- LAMMPS 历史 run 页面级验证：
  - `run_id = 703d711fcab9`
  - 结果：
    - `statusChips = ["ready", "lammps.generate", "completed"]`
    - `videoCards = 1`
    - `imageCards = 0`
    - `video.readyState = 4`
    - `video.networkState = 1`
    - `video.clientWidth = 824`
    - `video.clientHeight = 520`
  - 说明当前页面已经是“单视频主预览 + 导航切换”，不是把所有媒体堆在一起

## 2026-03-31 相图固定图窗补充

### 本轮目标

- 相图生成结果改成左侧固定图窗
- 右侧信息栏改成独立纵向滚动

### 本轮前端改动

- `frontend/src/features/result/ArtifactResultPanel.tsx`
  - phase metrics 从左侧主图区移动到右侧信息栏
  - 右侧 `aside` 增加独立滚动：`xl:max-h-[980px] xl:overflow-y-auto`
  - 左侧保留固定高度相图窗口，不再跟随右侧说明一起拉长

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 页面级载入验证：
  - 加载历史相图 run `8d965581`
  - 页面文案已更新为：
    - `左侧保持固定相图窗口，右侧信息栏可以单独上下滚动`
  - `iframeLength = 85313`

## 2026-04-01 相图窗口与进度条修正

### 本轮问题

- 运行中进度条会因为“已知步骤数”过少而直接跳到 `100%`
- 相图结果在前端和 iframe 内部各有一层侧栏，导致 iframe 宽度被压缩后触发内部响应式折叠，`Overview` 又被挤回图下方

### 本轮改动

- `frontend/src/features/chat/useAgentChat.ts`
  - 运行中不再展示伪百分比
  - 只有真正完成时才返回 `100%`
- `frontend/src/features/chat/AgentConversationPanel.tsx`
  - 状态说明从 `已完成 x/y 步` 改成更诚实的阶段描述
  - 运行中显示：
    - `已记录 N 个阶段，等待后端继续推进`
- `frontend/src/features/result/ArtifactResultPanel.tsx`
  - phase artifact 改成单一大窗口
  - 移除前端外层的第二层右侧信息栏
  - 相图信息统一交给 iframe 内的结果页自己布局
  - iframe 高度改为视口自适应的固定窗口
- `backend/app/thermo/engine.py`
  - phase report 内部响应式折叠阈值从 `1024px` 下调到 `860px`
  - 保持桌面嵌入时优先使用左右双栏：左图右信息

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 后端健康检查：
  - `GET http://127.0.0.1:8000/api/health`
  - 正常
- 新生成相图 run：
  - `run_id = 55dda75b4062`
  - 真实 `POST /api/agent/chat` 成功
  - 新 `result.html` 已确认包含：
    - `.workspace`
    - `.sidebar`
    - `Overview`
    - 不再包含旧版 `hero-copy / figure-footer`
- 页面级前端快照：
  - 新 run 已显示 `固定相图窗口`
  - `iframeLength = 86147`
  - `statusChips = ["ready", "phase_diagram.generate", "completed"]`

## 2026-04-01 侧栏视觉与删除对话

### 本轮目标

- 重新设计侧栏 agent logo
- 提高左栏 `新建研究课题` 按钮可读性
- 增加删除对话选项，并让删除动作真正落到后端 run 管理

### 本轮改动

- `frontend/src/app/AgentWorkbench.tsx`
  - 用新的 `AgentMark` SVG 标识替换旧烧瓶图标
  - `新建研究课题` 改成高对比渐变按钮
  - 最近研究流增加 hover 删除按钮
- `frontend/src/services/api.ts`
  - 新增 `deleteRunRequest(...)`
- `backend/app/core/artifacts.py`
  - 新增 `delete_run(run_id)`
- `backend/app/api.py`
  - 新增 `DELETE /api/runs/{run_id}`
- `backend/app/graph.py`
  - 所有 run（包括普通聊天）统一写入 summary，避免“可查看但不可删除”的不一致状态

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 后端测试：
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `29` 个测试通过
- 删除链路：
  - 使用本地 `TestClient` 验证 `POST /api/agent/chat -> DELETE /api/runs/{run_id} -> GET 404`
  - 结果：
    - `run_id = 7490359ee87a`
    - `get_before_status = 200`
    - `delete_status = 200`
    - `get_after_status = 404`

## 2026-04-01 Logo 收口

### 本轮目标

- 参考用户给出的原子轨道风格图片，继续收口左侧栏 agent logo
- 保持代码内联 SVG 方案，避免引入额外位图资源和适配成本

### 本轮改动

- `frontend/src/app/AgentWorkbench.tsx`
  - 将 `AgentMark` 重绘为更接近参考图的原子计算风格
  - 保留深色圆盘底座
  - 新增金属质感轨道环、中心三颗原子核、顶部晶格节点和侧向能量火花
  - 去掉此前偏卡通/偏扁平的弱参考版本

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过

## 2026-04-01 会话边界与交接文档收口

### 本轮问题

- 左侧 `最近研究流` 仍然按单次 run 展示，容易显示成 agent 的最终回复而不是用户请求
- `run_id` 和 `conversation_id` 之前耦合得太紧，不像 GPT Web 那样按会话隔离
- 运行中进度条仍可能出现误导性的 `100%`
- 聊天区发送后不会自动跟随滚动到底部
- 需要把这些问题同步整理进交接文档，避免后续再踩

### 本轮改动

- `backend/app/memory.py`
  - 给 `conversation_id` 加入安全归一化，避免直接拼文件名
  - 新增 `delete(conversation_id)`，支持删除整段会话的 memory snapshot
- `backend/app/core/artifacts.py`
  - 新增 `delete_conversation(conversation_id)`，可删除同一会话下的所有 run
- `backend/app/api.py`
  - 新增 `DELETE /api/conversations/{conversation_id}`
- `backend/app/graph.py`
  - 所有 response 的 `summary` 里统一补 `request_message`
  - 让前端可以用真实用户请求作为会话标题
- `frontend/src/features/chat/useAgentChat.ts`
  - 新增独立 `conversationId`
  - `resetConversation()` 改成创建新的会话 id
  - 发送请求时不再把 `runId` 当会话 id
  - 前端不再用本地 `conversation_history` 覆盖后端 memory snapshot
  - 运行中进度条改成非定量模式，不再返回 `100%`
- `frontend/src/features/chat/AgentConversationPanel.tsx`
  - 新增自动滚动到底部
  - 进度状态改为 `处理中`
  - 脉冲条宽度按真实阶段数量缓慢推进，不再假装精确百分比
- `frontend/src/app/AgentWorkbench.tsx`
  - 左栏改成按 `conversation_id` 聚合
  - 会话标题优先显示用户请求
  - 删除按钮改为删除整段会话，而不是只删单次 run
- `README.md`
  - 增加“会话与记忆边界”
  - 增加“常见问题与解决”

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 后端测试：
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `29` 个测试通过
- 后端健康检查：
  - `GET http://127.0.0.1:8000/api/health`
  - 正常
- 会话删除接口：
  - 使用本地 `TestClient` 验证 `DELETE /api/conversations/{conversation_id}`
  - 结果：
    - `deleted_runs = 2`
    - `deleted_memory = true`
    - run 目录和 memory 文件都已删除

## 2026-04-01 相图右栏重排

### 本轮问题

- 相图右侧信息栏仍然像传统 `dt/dd` 表格，在侧栏宽度较窄时容易出现“内容被裁掉”的观感
- 顶部 badge 区也会因为长文本而显得拥挤

### 本轮改动

- `backend/app/thermo/engine.py`
  - 相图结果 HTML 的右栏改成单列参数卡布局
  - `Conditions / Calculation Context / Method & Notes` 全部改成 `fact-grid + fact card`
  - `Selected Phases` 改成 phase chip
  - `References` 改成独立链接卡
  - 顶部 badge 改成更稳定的两列网格
  - 调整主区比例为更适合嵌入窗口的 `左图 + 右栏` 宽度分配

### 本轮验证

- 后端已重启到最新代码
- 新相图 run：
  - `run_id = aeb202fb9335`
  - `route = phase_diagram.generate`
  - `success = true`
- 新 `result.html` 已确认包含：
  - `fact-grid`
  - `phase-chip`
  - `reference-links`
  - `grid-template-columns: minmax(0, 1.9fr) minmax(300px, 356px)`

## 2026-04-01 左栏底部固定区

### 本轮问题

- 左侧栏底部的 `算力队列负载` 会跟着会话列表一起滚动
- `系统偏好设置` 和运行状态不够像固定底栏

### 本轮改动

- `frontend/src/app/AgentWorkbench.tsx`
  - 左栏滚动区域只保留 `最近研究流`
  - 将 `算力队列负载` 移到左栏底部固定区
  - 将 `系统偏好设置` 与其一起收口到固定底部

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过

## 2026-04-01 仓库安全瘦身

### 本轮目标

- 在不改变任何功能的前提下，清理主项目里已经没有必要长期保留的缓存、历史产物和废弃兼容代码
- 保留运行依赖和冻结参考目录 `lammps/`

### 本轮清理

- 删除前端构建和缓存：
  - `frontend/dist/`
  - `frontend/node_modules/.vite/`
  - `frontend/node_modules/.vite-temp/`
- 删除历史运行产物：
  - `backend/outputs/runs/*`
  - `backend/outputs/memory/*`
  - `backend/outputs/calculated_examples/*`
  - `backend/outputs/latest_result.html`
- 删除无用文件：
  - `.DS_Store`
  - `__pycache__/`
  - `.vscode/`
  - 根目录旧压缩包 `归档.zip`
  - 已废弃空壳 `backend/package.json`
- 删除旧兼容字段和代码：
  - `backend/app/config.py`
    - 移除 `latest_result_file_name`
  - `backend/app/core/artifacts.py`
    - 移除 `latest_result` 兼容逻辑

### 保留说明

- 明确保留：
  - `backend/.venv/`
  - `frontend/node_modules/`
  - `backend/configs/thermo_databases/`
  - 冻结参考目录 `lammps/`
- `backend/outputs/` 现在只保留最小骨架：
  - `.gitkeep`
  - `runs/`
  - `memory/`
  - `calculated_examples/`

### 本轮问题与修正

- 在清理 `latest_result` 兼容代码时，误删了 `write_trace()` 仍在使用的 `write_text_file` 引入
- 现象：
  - 后端测试里 `route` 回退成 `supervisor.dispatch`
  - 部分 case 变成 `graph_execution_failed`
- 修正：
  - 已在 `backend/app/core/artifacts.py` 恢复 `write_text_file` 引入

### 本轮验证

- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 后端测试：
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `29` 个测试通过
- 后端健康检查：
  - `GET http://127.0.0.1:8000/api/health`
  - 正常
- Registry 接口：
  - `GET http://127.0.0.1:8000/api/thermo/registry`
  - 正常返回 `count = 3`

## 2026-04-01 全面回归验证

### 本轮目标

- 在仓库瘦身后重新做一轮完整验证，确认主链路没有被清理动作破坏
- 覆盖：
  - 后端单元测试
  - 前端构建
  - 相图真实计算
  - LAMMPS 真实本地执行
  - 浏览器层基础 smoke

### 本轮验证结果

- 后端单元测试：
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `29` 个测试通过
- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 后端健康检查：
  - `GET http://127.0.0.1:8000/api/health`
  - 正常
- 相图 live 验证：
  - `cd backend && ./.venv/bin/python examples/verify_phase_diagram_cases.py --all`
  - 通过
  - `Al-Zn`：
    - `run_id = 20c146af385f`
    - `accuracy_passed = true`
  - `Al-Mg`：
    - `run_id = 4544a78433ad`
    - `accuracy_passed = true`
  - `Al-Ni`：
    - `run_id = 763eabe8ff6c`
    - `accuracy_passed = true`
- LAMMPS live 验证：
  - `Cu heating`
    - `run_id = 6793e3fc9c27`
    - `run_mode = real`
    - `generated_ovito = true`
  - `Al heating`
    - `run_id = 8b3cb18c09e0`
    - `run_mode = real`
    - `generated_ovito = true`
- 产物落盘核对：
  - 相图 run 都存在：
    - `generated_code_attempt_1.py`
    - `result.html`
    - `summary.json`
  - LAMMPS run 都存在：
    - `plot.png`
    - `report.md`
    - `diffusion_trajectory_3d.gif`
    - `ovito.mp4`

### 浏览器层补充说明

- 普通对话的前端 smoke 已通过：
  - `statusChips = ["ready","conversation.answer","completed"]`
- 但这台机器当前用 Node 直连 Chrome DevTools 时，存在 `fetch('http://127.0.0.1:9222/json/list')` 间歇性失败
- 因此本轮浏览器自动化没有把“相图 iframe + LAMMPS 多媒体卡片”作为唯一判断标准，而是改用：
  - live API 成功返回
  - 结果产物真实落盘
  - 前端构建通过

### 本轮发现的稳定性边界

- 当前本地 `uvicorn` 在重任务之后，对并发 `curl` 请求的可用性有短时抖动
- 现象：
  - 同一轮里 `api/health` 可通
  - 但并发或紧邻的 artifact `curl` 偶发连接失败
- 这不影响本轮已经完成的 live 相图和 live LAMMPS 结果，但后面如果要继续提升稳定性，建议优先排查：
  - 本地 server 启动方式
  - 长任务执行时的阻塞/并发模型

## 2026-04-01 动态 Prompt 推荐接入

### 本轮目标

- 把输入框右侧 `✨` 按钮从“固定模板插入”升级成“基于上下文的 LLM 动态推荐”
- 保持现有聊天、follow-up 和 `last_run_context` 语义不变
- 不自动发送，只负责给用户推荐下一条更有价值的提问

### 本轮改动

- 后端新增接口：
  - `POST /api/agent/prompt-suggestion`
- 新增模型：
  - `PromptSuggestionRequest`
  - `PromptSuggestionResponse`
- `ChatAgent` 新增：
  - 基于最近会话、`last_run_context`、识别结果和 summary 生成一条简短中文推荐 prompt
- 前端按钮逻辑调整：
  - 不再把上一条完整返回内容复制到输入框
  - 改为调用后端接口拿推荐结果
  - 不自动发送，只填入输入框
  - 推荐生成中显示轻量 loading 态

### 本轮验证

- 后端单元测试：
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `30` 个测试通过
- 前端构建：
  - `cd frontend && npm run build`
  - 通过
- 新增接口测试：
  - `test_prompt_suggestion_endpoint_returns_contextual_llm_prompt`
  - 已覆盖

### 当前已知边界

- 这台机器上如果本地 `uvicorn` 不是常驻运行，直接 `curl` 新接口会连不上端口；这不影响单测和前端构建结果
- 动态推荐依赖真实 LLM，未配置时会诚实失败，不再回退到假模板

## 2026-04-01 Agent Prompt 与编排收口

### 本轮目标

- 在不破坏现有功能的前提下，收紧 4-agent 编排里最容易漂移的部分
- 避免一次 compute 请求在 memory 中留下两条 assistant 结论
- 收紧 `ChatAgent` 的能力边界，减少“泛化成通用联网助手”的倾向
- 让 `mixed.request` 链路更真实地消化 `RecognitionAgent` 的结构化结果

### 本轮改动

- `ComputeAgent`
  - 不再把 runtime 的 `final_message` 直接追加为对话消息
  - 只保留用户消息，最终对话回复统一由 `ChatAgent` 写回 memory
- `ChatAgent`
  - 系统 prompt 新增明确边界：
    - 不虚构联网/数据库/插件能力
    - 不泛化成通用互联网助手
    - 不随意暴露模型提供方或隐藏部署信息
- `SupervisorAgent`
  - 保留 heuristic baseline，但显式要求 LLM 独立判断，只把 heuristic 当 fallback reference
- `PhaseDiagramRuntime`
  - `mixed.request` 时会把 recognition 的：
    - `system`
    - `diagram_type`
    - `y_axis` 温区
    - `phases / labels / critical_points / raw_summary`
    注入生成请求
  - recognition 补充信息会最终落入 `DiagramRequest.notes`
- `AgentAppGraph`
  - 对 compute 响应新增：
    - `metadata.runtime_final_message`
    - `metadata.chat_final_message`
  - 方便区分 runtime 原始结论和 ChatAgent 用户可读结论

### 本轮验证

- 后端测试：
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `34` 个测试通过
- live 接口检查：
  - `GET /api/health` 正常
  - `GET /api/thermo/registry` 正常
- 新增/增强覆盖：
  - `ChatAgent` prompt 边界测试
  - `ComputeAgent` 不再双写 assistant 消息
  - `PhaseDiagramRuntime` 真实吸收 recognition 温区和结构化摘要
  - API 返回 `runtime_final_message/chat_final_message`

### 结果

- 当前这套已经是“真 4-agent 编排”，不是假 workflow
- 但现在的边界更清楚：
  - runtime 负责计算链路
  - chat 负责用户可读回复
  - recognition 对 mixed path 不再只是装饰

## 2026-04-03 第四批 TDB 扩库（新增 6 个可计算二元系）

### 本轮准备做什么

- 在前端冻结不变的前提下，继续扩充后端可真实计算的相图体系数量
- 目标不是“只把库文件收集进仓库”，而是：
  - 能被 `pycalphad` 正常解析
  - 能通过当前 accuracy gate
  - 能进入 thermo registry
  - 能同步扩到 thermo RAG 文档
- 优先从当前环境已经存在的公开数据库中，筛选能稳定跑通的二元子空间

### 本轮做到哪了

- 重新阅读了：
  - `README.md`
  - `PROJECT_PROGRESS.md`
  - `backend/configs/thermo_registry.json`
- 确认本轮开始前，registry 已经有 `10` 个体系：
  - `Al-Zn`
  - `Al-Mg`
  - `Al-Ni`
  - `Pb-Sn`
  - `Al-Fe`
  - `Cu-Ni`
  - `Nb-Re`
  - `Cr-Fe`
  - `Fe-Nb`
  - `Cr-Nb`
- 扫描并复用了本地可用的公开数据库来源：
  - `pycalphad` 测试数据库目录
  - 当前项目 `backend/configs/thermo_databases`
- 新增复制入库的数据库文件：
  - `backend/configs/thermo_databases/crtiv_ghosh.tdb`
  - `backend/configs/thermo_databases/mc_fecocrnbti.tdb`
- 正式新增并接入 `6` 个可真实计算的二元体系：
  - `Cr-Ti`
  - `Cr-V`
  - `Ti-V`
  - `Fe-Co`
  - `Co-Cr`
  - `Nb-Ti`
- thermo registry 已从 `10` 个体系扩到 `16` 个体系
- thermo RAG 示例文档同步扩充到 `59` 条
- `backend/examples/verify_phase_diagram_cases.py` 已支持这些新体系
- `backend/tests/test_backend_app_contract.py` 已补充新体系断言

### 本轮筛选过程与结论

#### 成功纳入主链路的数据库 / 子空间

- `crtiv_ghosh.tdb`
  - 抽取并验证通过：
    - `Cr-Ti`
    - `Cr-V`
    - `Ti-V`
- `mc_fecocrnbti.tdb`
  - 抽取并验证通过：
    - `Fe-Co`
    - `Co-Cr`
    - `Nb-Ti`

#### 评估后未纳入主链路的候选

- `Al-Cr`（来自 `alcrni.tdb`）
  - 当前稳定相集合只出现：
    - `B2`
    - `L12_FCC`
    - `LIQUID`
  - 缺少预期的 `FCC_A1` / `BCC_A2`
  - 结论：暂不接主链路
- `Cr-Ni`（来自 `alcrni.tdb`）
  - 相集合与预期不稳
  - 结论：暂不接主链路
- `Co-Ni`（来自 `alcocrni.tdb`）
  - 当前结果里 `FCC_A1` 支撑不足
  - 结论：暂不接主链路
- `Al-Co`（来自 `alcocrni.tdb`）
  - 当前结果里 `FCC_A1` 支撑不足
  - 结论：暂不接主链路
- `Al-Cu-Y.tdb`
  - `pycalphad` 解析失败
  - 失败位置在 `PARAMETER G(AL2Y,AL,...)`
  - 结论：不纳入当前链路

### 本轮验证结果

#### 单体系准确率 / 稳定相验证

- `Cr-Ti`
  - stable phases:
    - `BCC_A2`
    - `HCP_A3`
    - `LAVES_C14`
    - `LAVES_C15`
    - `LAVES_C36`
    - `LIQUID`
  - endpoint estimate:
    - `Cr side ~ 2177.5 K`
    - `Ti side ~ 1937.5 K`
  - 结论：`passes = true`

- `Cr-V`
  - stable phases:
    - `BCC_A2`
    - `LIQUID`
  - endpoint estimate:
    - `Cr side ~ 2177.5 K`
    - `V side ~ 2182.5 K`
  - 结论：`passes = true`

- `Ti-V`
  - stable phases:
    - `BCC_A2`
    - `HCP_A3`
    - `LIQUID`
  - endpoint estimate:
    - `Ti side ~ 1937.5 K`
    - `V side ~ 2182.5 K`
  - 结论：`passes = true`

- `Fe-Co`
  - stable phases:
    - `BCC_A2`
    - `FCC_A1`
    - `HCP_A3`
    - `LIQUID`
  - endpoint estimate:
    - `Fe side ~ 1807.5 K`
    - `Co side ~ 1767.5 K`
  - 结论：`passes = true`

- `Co-Cr`
  - stable phases:
    - `BCC_A2`
    - `FCC_A1`
    - `HCP_A3`
    - `LIQUID`
    - `SIGMA`
  - endpoint estimate:
    - `Co side ~ 1767.5 K`
    - `Cr side ~ 2177.5 K`
  - 结论：`passes = true`

- `Nb-Ti`
  - stable phases:
    - `BCC_A2`
    - `HCP_A3`
    - `LIQUID`
  - endpoint estimate:
    - `Nb side ~ 2747.5 K`
    - `Ti side ~ 1942.5 K`
  - 结论：`passes = true`

#### 后端回归

- 后端全量单元测试：
  - `cd backend && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  - `42` 个测试全部通过

#### 数据文件与检索层验证

- registry 数量校验：
  - `registry_count = 16`
- thermo RAG 示例文档数量校验：
  - `rag_doc_count = 59`
- 新增文档类型覆盖正常：
  - `system_card`
  - `phase_card`
  - `provenance_card`
  - `tdb_chunk`

#### live 后端验证（最新进程）

- live registry:
  - `GET /api/thermo/registry`
  - `count = 16`
- live `/api/agent/chat`：
  - `Cr-Ti`
    - `run_id = 7fab83d983b5`
    - `route = phase_diagram.generate`
    - `generation_source = llm_codegen_calculated_wrapper`
    - `termination_reason = review_passed`
    - `accuracy_passed = true`
  - `Fe-Co`
    - `run_id = a08cf7d8af46`
    - `route = phase_diagram.generate`
    - `generation_source = llm_codegen_calculated_wrapper`
    - `termination_reason = review_passed`
    - `accuracy_passed = true`

### 本轮还没做完什么

- 还没有把这 `6` 个新增体系全部跑一轮 live `/api/agent/chat` 并全部记录 run_id
- 当前已完成其中 `2` 个 live 验证：`Cr-Ti`、`Fe-Co`
- 还没有继续向 `20+` 个稳定体系推进
- 还没有把 thermo RAG builder 自动化成正式脚本

### 本轮已知边界 / 错误 / 解决方法

- `Al-Cu-Y.tdb` 当前无法被现有 `pycalphad` 正常解析
  - 解决策略：不强接主链路，保持 registry 干净
- 某些多元数据库抽出的二元子空间虽然能跑，但相集合不稳
  - 解决策略：必须先过 accuracy gate，再入 registry
- 当前 exec 会话很多，桌面环境会持续提示 unified exec process 数量过高
  - 解决策略：本轮不再增加无关前端/长期进程，只集中完成后端扩库与验证

### 如果上下文被压缩，下一步从这里继续

优先继续：

1. 重新读 `README.md` 和 `PROJECT_PROGRESS.md`
2. 验证 live `GET /api/thermo/registry` 是否返回 `count = 16`
3. 选 `Cr-Ti` 和 `Fe-Co` 先做 live `/api/agent/chat` 验证
4. 再继续筛下一批安全候选，目标继续扩到 `18-20` 个稳定体系
