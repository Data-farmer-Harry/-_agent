# Phase Diagram Agent

一个面向材料科研场景的多能力 Agent 项目，当前主线包含两条真实本地计算链路：

- 相图计算：`pycalphad + TDB`
- 分子动力学：`LAMMPS + OVITO`

项目当前以**前后端分离**形式组织：

```text
phase_diagram_agent/
├── frontend/      # 前端界面
├── backend/       # 后端、agent、runtime、registry、tests
├── docs/          # 结构说明和专项设计文档
├── README.md
└── PROJECT_PROGRESS.md
```

## 项目定位

这个项目不是单纯的聊天机器人，而是一个面向材料科研任务的 agent 工作台：

- 用 LLM 做真实路由和任务理解
- 用本地工具做真实计算
- 保留会话 memory、artifact、follow-up
- 保持结果可信度、环境诊断和可交接性

## 当前主结构

### 前端

- 目录：`frontend/`
- 负责：
  - 聊天 UI
  - 截图/文件上传
  - 相图与 LAMMPS artifact 展示
  - 系统设置与环境诊断面板

### 后端

- 目录：`backend/`
- 负责：
  - 统一 API
  - LangGraph 编排
  - 4 agent
  - memory 模块
  - thermo registry / thermo RAG
  - LAMMPS runtime
  - artifact 与 trace 管理

## 当前 4-agent 架构

- `SupervisorAgent`
  - 读取用户输入和上下文
  - 用 LLM 做真实任务路由
- `RecognitionAgent`
  - 处理截图识别
  - 输出结构化识别结果
- `ComputeAgent`
  - 分发到：
    - `PhaseDiagramRuntime`
    - `LammpsRuntime`
- `ChatAgent`
  - 负责普通问答、结果解释、follow-up

### Memory 设计

当前不是 `Memory Agent`，而是 `memory module`：

- `state`
- `persistence`
- `summary`
- `last_run_context`

memory 相关文件：

- `backend/app/state.py`
- `backend/app/memory.py`
- `backend/app/graph.py`

## 当前相图链路

相图生成保留真实计算路径：

1. `SupervisorAgent` 路由到相图生成
2. `ComputeAgent` 进入 `PhaseDiagramRuntime`
3. thermo registry 查库
4. LLM 生成薄 wrapper
5. 本地 Python 执行 wrapper
6. `pycalphad + TDB` 真计算
7. review / repair
8. accuracy gate

当前已接入的 TDB 体系见：

- `backend/configs/thermo_registry.json`

## 当前 thermo RAG 定位

项目当前有 `thermo RAG`，但它不是执行主链路，而是**查库增强层**：

- exact/alias registry 命中优先
- exact miss 才进入 thermo RAG
- RAG 只负责召回候选和补充数据库知识
- 真执行仍然是：
  - `registry card -> .tdb path -> pycalphad`

RAG 相关文件：

- `backend/app/thermo/rag_models.py`
- `backend/app/thermo/rag_index.py`
- `backend/app/thermo/rag_retriever.py`
- `backend/app/thermo/rag_service.py`

## 当前 LAMMPS 链路

LAMMPS 生成保留真实本地执行路径：

1. `SupervisorAgent` 路由到 LAMMPS 计算
2. `ComputeAgent` 进入 `LammpsRuntime`
3. request parse / registry / validator
4. 生成 `in.lammps`
5. 本地运行 LAMMPS
6. OVITO 后处理
7. 返回 plot / report / gif / mp4 / 轨迹文件

## 运行入口

常用端口：

- 前端：`http://127.0.0.1:5174`
- 后端：`http://127.0.0.1:8000`

后端主入口：

- `backend/app/api.py`

## 文档说明

如果要快速恢复项目上下文，请优先读：

1. `README.md`
2. `PROJECT_PROGRESS.md`
3. `docs/ARCHITECTURE.md`

其中：

- `README.md` 只保留项目规划、结构和主线说明
- `PROJECT_PROGRESS.md` 是最详细的开发/测试/未完成事项台账

## 当前开发约定

- 优先保留已验证通过的真实计算链路
- 新能力先旁路增强，不直接替换稳定主链路
- 前端与后端边界保持清晰
- 每次较大改动后，详细状态写入 `PROJECT_PROGRESS.md`
- 在上下文压缩后，优先重新读取 `README.md` 和 `PROJECT_PROGRESS.md`
