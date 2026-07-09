# Thermo RAG Schema

本文档定义相图 `TDB RAG` 的建议数据格式。目标不是替代当前 `thermo registry + pycalphad + TDB` 真执行链路，而是为后续这些能力提供统一数据底座：

- 模糊查询召回
- 数据库候选推荐
- 结果解释
- review 辅助
- 用户上传 TDB 后的自动索引

## 设计原则

### 1. RAG 只增强，不替代执行

执行真相层仍然是：

1. `registry card`
2. `database_file`
3. `pycalphad + TDB`

RAG 负责：

- 检索
- 候选排序
- 知识补充
- 解释和审查辅助

### 2. 结构化 card 优先于 raw TDB

不建议一开始把 `.tdb` 文件全文当成主检索对象。更稳的做法是：

- 主检索：`system_card` / `phase_card`
- 辅助检索：`provenance_card` / `tdb_chunk`

### 3. metadata 必须能回到真实执行资产

每一条 RAG 文档都必须能够关联回：

- `system_name`
- `database_name`
- `database_file`

否则检索再好，也不能安全进入真实计算。

## 文档类型

建议统一用 `doc_type` 区分 4 类文档。

### 1. `system_card`

用途：

- 主召回对象
- 用户模糊查询的第一入口

建议字段：

- `id`
- `doc_type`
- `system_name`
- `aliases`
- `database_name`
- `database_file`
- `components`
- `phases`
- `tags`
- `summary`
- `documentation_url`
- `source_url`
- `provenance`
- `accuracy_reference`
- `content`

### 2. `phase_card`

用途：

- 检索某个体系有哪些 phase
- 给 codegen / review / ChatAgent 提供更细粒度背景

建议字段：

- `id`
- `doc_type`
- `system_name`
- `database_name`
- `database_file`
- `phase_name`
- `components`
- `phase_group`
- `content`

### 3. `provenance_card`

用途：

- 解释数据库来源
- 给结果可信度和 review 提供上下文

建议字段：

- `id`
- `doc_type`
- `system_name`
- `database_name`
- `database_file`
- `source_url`
- `documentation_url`
- `provenance`
- `content`

### 4. `tdb_chunk`

用途：

- 用于专家解释和 debug
- 不建议作为第一主召回对象

建议字段：

- `id`
- `doc_type`
- `system_name`
- `database_name`
- `database_file`
- `section_type`
- `section_name`
- `content`

## 通用字段

所有文档建议共享这些字段：

- `id`
- `doc_type`
- `system_name`
- `database_name`
- `database_file`
- `family`
- `format`
- `source_url`
- `provenance`
- `content`
- `metadata`

其中：

- `family`
  - 例如 `tdb_calculated_binary`
- `format`
  - 例如 `tdb`

## 推荐的 JSONL 形态

每行一条文档，便于：

- 本地索引
- 后续导入向量库
- 增量追加用户上传的 TDB 文档

示例文件：

- [thermo_rag_documents.example.jsonl](../backend/configs/thermo_rag_documents.example.jsonl)

## 推荐的索引顺序

### v1

- 先只索引：
  - `system_card`
  - `phase_card`

### v2

- 再补：
  - `provenance_card`
  - `tdb_chunk`

## 推荐的 chunk 策略

### 不推荐

- 固定 token 长度硬切 `.tdb`

### 推荐

按 TDB 的结构块切：

- `ELEMENT`
- `PHASE`
- `CONSTITUENT`
- `FUNCTION`
- `PARAMETER`

然后把每个结构块转成一条 `tdb_chunk`。

## embedding 模型建议

### 当前推荐

- `BAAI/bge-m3`

原因：

- 中英文混合查询更稳
- 对体系名、元素名、phase 名、短技术短语更友好
- 适合后续 hybrid retrieval

### 备选

- `Qwen/Qwen3-Embedding-0.6B`
- 更适合和现有 Qwen agent 生态统一

## 在当前项目里的挂点

### `PhaseDiagramAgent`

- 请求理解时：
  - 用 `system_card` 召回候选
- review 时：
  - 用 `system_card + provenance_card` 提供知识上下文

### `ChatAgent`

- 用于解释：
  - 数据库来源
  - phase 覆盖
  - 适用范围
  - 可信边界

### 不建议当前就让 RAG 直接决定执行

推荐流程：

1. `LLM normalize request`
2. `registry deterministic select`
3. `RAG add context / candidates / explanation`
4. `pycalphad + TDB`

而不是：

1. `RAG decide final database`
2. `execute`

## 最小落地建议

如果要开始做真正的 TDB RAG，我建议：

1. 先生成 `system_card` 和 `phase_card`
2. 先做本地 `jsonl + embedding`
3. 先只给 `ChatAgent / review` 用
4. 主执行链路先不要改
