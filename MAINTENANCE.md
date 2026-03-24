# Maintenance Notes

这个仓库把 Python backend、React frontend 和旧 Vue frontend 放在同一个工作区里，方便联调，但也意味着运行产物和依赖目录会很快变大。

## Safe To Remove

这些目录或文件可以清理，且不会影响源码：

- `frontend/node_modules/`
- `frontend/dist/`
- `frontend-react/node_modules/`
- `frontend-react/dist/`
- `backend/tmp/`
- `backend/app/**/__pycache__/`
- `backend/.venv/`，前提是你接受重新创建虚拟环境

优先使用：

```bash
scripts/cleanup-generated.sh
```

先 dry-run，再决定是否清理。

## Keep

这些目录是当前项目的源码和叙事主干，不应该被随意删改：

- `backend/app/`
- `backend/tests/`
- `frontend/src/`
- `frontend-react/src/`
- `README.md`
- `backend/README.md`
- `docs/`

## Current Layout Notes

仓库里当前有两个前端：

- `frontend/`：旧 Vue 实现
- `frontend-react/`：当前主用工作台

在没有明确迁移计划前，不建议删除其中任何一个。当前最有效的减重方式仍然是清理生成产物，而不是清理源码目录。

## Documentation Sync Rules

这个项目会被用于面试展示，所以文档必须持续和代码现状对齐。下面几类变化发生时，需要同步更新文档：

### 1. route 或 workspace 发生变化

至少检查这些文件：

- `README.md`
- `backend/README.md`
- `docs/ARCHITECTURE.md`
- `docs/INTERVIEW_PLAYBOOK.md`

### 2. 新增或删除 tool

至少更新：

- 根 README 的能力说明
- backend README 的工具表和扩展说明
- architecture 文档里的模块图

### 3. LAMMPS 状态推进

如果 `lammps_codegen`、`lammps_execute` 或 `lammps_repair` 真正落地，必须同步改掉当前所有 “stub / reserved” 相关表述，避免面试时夸大现状。

### 4. 图片识别模块策略变化

如果未来从“结构化 spec + 确定性渲染”改成别的模式，要同步更新文档里的设计取舍说明。当前文档的核心前提是：

- 模型不直接生成最终页面
- 用户提供坐标轴校准
- 没把握时回退 `manual_calibrated`

## Claim Discipline

为了让项目包装可用于正式面试，文档遵循几个约束：

- 不把 stub 说成已完成能力
- 不把 placeholder 图说成真实热力学结果
- 不把规则化 planner 说成开放式自主规划
- 不把多模态识别说成“高准确率自动重建”，除非测试和评估真的支持

如果代码还没跑通，文档也不要先写“已支持”。

## Demo Hygiene

如果你要演示项目，建议在开始前做三件事：

1. 清理 `backend/tmp/`，避免旧结果混淆当前 run
2. 确认 React frontend 指向正确的 backend 地址
3. 跑一遍 backend contract tests，确保 catalog、route 和 fallback 没被新改动破坏
