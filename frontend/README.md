# Frontend

## 技术栈
- Vue 3
- TypeScript
- Vite

## 安装依赖
```bash
cd frontend
npm install
```

## 启动开发环境
```bash
npm run dev
```

默认地址：`http://localhost:5173`

## 页面结构
- 左侧 40%
  - `ChatPanel.vue`：体系输入与说明
  - `ControlPanel.vue`：参数控制、滑块与数值输入
  - `SettingsPanel.vue`：API 设置
  - `ErrorPanel.vue`：stdout / stderr 日志
- 右侧 60%
  - `ResultViewer.vue`：通过 `iframe + srcdoc` 渲染后端返回的 `result.html`

## API 配置
默认配置写在 `.env.example` 中：
```env
VITE_API_BASE_URL=http://localhost:8000
VITE_GENERATE_PATH=/api/generate
VITE_RUN_PATH=/api/run
VITE_GENERATE_AND_RUN_PATH=/api/generate-and-run
VITE_REQUEST_TIMEOUT_MS=120000
VITE_ENABLE_AUTO_RETRY=false
```

前端读取优先级：
1. `localStorage`
2. `.env` / `.env.local`
3. `.env.example` 中展示的默认值

### 可配置项
- API Base URL
- Generate Path
- Run Path
- Generate & Run Path
- Timeout
- 自动重试开关（当前仅保留 UI）

### 修改 API 地址
方式一：在页面的 `SettingsPanel` 中直接修改，刷新后会保留。

方式二：复制 `.env.example` 为 `.env`：
```bash
cp .env.example .env
```
然后按需修改：
```env
VITE_API_BASE_URL=http://localhost:8000
```

## 交互流程
1. 输入体系名称，例如 `Fe-C 二元相图`
2. 调整温度、压力、步长
3. 点击“生成并运行”
4. 前端调用 `/api/generate-and-run`
5. 成功时右侧 iframe 展示 Plotly 结果
6. 失败时左侧 `ErrorPanel` 展示 stdout / stderr

## 当前边界
- 不使用复杂状态管理库
- 不接数据库或登录系统
- 自动重试仅保留 UI 开关，暂未实现复杂逻辑
