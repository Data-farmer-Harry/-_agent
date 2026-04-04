# Gemini Frontend Handoff

## 1. 目标

请基于当前后端接口，实现一个前端页面，用于：

1. 输入自然语言需求
2. 显示 agent 的解释与参数抽取结果
3. 在参数满足条件时启动模拟
4. 轮询任务状态
5. 展示图表、报告、视频和下载链接
6. 查看历史任务
7. 可选展示 LLM / LAMMPS 当前配置

## 2. 后端基础信息

- 服务默认地址：`http://127.0.0.1:8765`
- 协议：HTTP
- 认证：无
- WebSocket：无
- 运行状态获取方式：轮询
- 文件访问方式：HTTP 静态路径 `/artifacts/<run_id>/<filename>`

## 3. 当前页面约定

### 3.1 会话输入区

包含：

- 多行输入框
- 左侧 `+` 按钮
- 发送按钮

说明：

- `+` 按钮当前只保留界面占位，未接入实际上传功能
- 前端主流程只围绕文本对话和模拟启动展开

### 3.2 参数与校验区

展示：

- `normalized_request`
- `missing_fields`
- `validation.errors`
- `validation.warnings`
- `intent`
- `parse_source`

如果 `intent=general_help`：

- 只展示说明性内容
- 不应强制显示运行参数澄清提示

### 3.3 任务进度区

展示：

- `run_id`
- `status`
- `mode`
- `summary.progress.stage`
- `summary.progress.percent`
- `summary.progress.message`

轮询规则：

- `status=queued` 或 `status=running` 时，每 1 到 2 秒轮询一次 `GET /api/run/<run_id>`
- `status=completed` 或 `status=failed` 或 `status=cancelled` 时停止轮询

### 3.4 结果展示区

优先展示：

- `summary.metrics`
- `plot.png`
- `report.md`
- `structure_summary.json`

如果存在以下产物，再额外展示：

- `diffusion_trajectory.png`
- `diffusion_trajectory_3d.gif`
- `ovito.mp4`
- `diffusion_metadata.json`

### 3.5 历史任务区

通过 `GET /api/runs` 获取列表，允许用户：

- 选择历史 `run_id`
- 加载历史任务详情
- 重新渲染历史结果

## 4. 接口调用顺序

### 4.1 解析需求

请求：

```http
POST /api/chat
Content-Type: application/json
```

```json
{
  "message": "请帮我做一个铜材料的升温模拟，温度 900 K，步数 4000，用 EAM 势",
  "normalized_request": {}
}
```

返回关键字段：

```json
{
  "reply": "参数已满足运行要求，解析来源=hybrid，可以开始生成 LAMMPS 任务。警告：无。",
  "needs_input": false,
  "can_run": true,
  "state": {
    "intent": "simulation_request",
    "normalized_request": {
      "material": "Cu",
      "potential_family": "eam",
      "task_type": "heating",
      "temperature": 900,
      "steps": 4000
    },
    "missing_fields": [],
    "validation": {
      "is_reasonable": true,
      "errors": [],
      "warnings": []
    }
  }
}
```

前端逻辑：

1. 显示 `reply`
2. 把 `state.normalized_request` 渲染成结构化卡片
3. 如果 `can_run=true`，允许启动模拟
4. 如果 `needs_input=true`，保持在澄清模式

### 4.2 启动模拟

请求：

```http
POST /api/run
Content-Type: application/json
```

```json
{
  "user_query": "请帮我做一个铜材料的升温模拟，温度 900 K，步数 4000，用 EAM 势",
  "normalized_request": {
    "material": "Cu",
    "potential_family": "eam",
    "task_type": "heating",
    "temperature": 900,
    "steps": 4000
  }
}
```

返回：

```json
{
  "run_id": "a1b2c3d4e5f6",
  "status": "queued",
  "state": {
    "run_id": "a1b2c3d4e5f6",
    "status": "queued"
  }
}
```

前端逻辑：

1. 保存 `run_id`
2. 进入任务轮询
3. 显示“已入队”状态

### 4.3 轮询任务状态

请求：

```http
GET /api/run/<run_id>
```

返回关键字段：

```json
{
  "run_id": "a1b2c3d4e5f6",
  "status": "running",
  "mode": "real",
  "error": "",
  "summary": {
    "status": "running",
    "progress": {
      "stage": "running_lammps",
      "percent": 28,
      "message": "正在执行 LAMMPS 模拟。"
    },
    "artifacts": {}
  },
  "artifacts": {}
}
```

注意：

- 前端应使用顶层 `artifacts` 中的 URL
- 不要使用 `summary.artifacts` 里的本地绝对路径

## 5. 文件与组件映射建议

- `plot.png`
  - 作为热力学主图
- `report.md`
  - 作为 Markdown 报告渲染
- `thermo.csv`
  - 提供下载
- `structure_summary.json`
  - 作为结构摘要卡片或 JSON 视图
- `diffusion_trajectory.png`
  - 作为扩散轨迹静态图
- `diffusion_trajectory_3d.gif`
  - 作为 GIF 动画
- `ovito.mp4`
  - 作为视频播放器
- `diffusion_metadata.json`
  - 作为附加说明卡片

## 6. 历史任务接口

### 6.1 获取全部历史

```http
GET /api/runs
```

### 6.2 获取最近一次任务

```http
GET /api/run/latest
```

## 7. 配置面板接口

### 7.1 LLM 配置

- `GET /api/config/llm`
- `POST /api/config/llm`

可展示字段：

- `provider`
- `base_url`
- `model`
- `timeout_seconds`
- `api_key_set`
- `api_key_masked`

### 7.2 LAMMPS 配置

- `GET /api/config/lammps`
- `POST /api/config/lammps`

可展示字段：

- `lammps_command`
- `potentials_dir`
- `allow_mock_fallback`
- `force_mock`
- `lammps_command_exists`
- `potentials_dir_exists`
- `ovito_available`
- `ovito_backend`
- `ovito_location`

## 8. 模板 Schema 接口

```http
GET /api/template/lammps
```

## 9. 当前边界

- 当前不提供 `/api/uploads`
- 当前不需要实现附件 tray / chip / 多模态上传
- 左侧 `+` 按钮如果保留，应视为未启用态或纯占位控件
