# MatterLab 桌面安装包

桌面层为现有 React + FastAPI 项目增加可安装外壳，不重写 Agent，也不删除传统前后端启动方式。macOS 产物是 DMG，Windows 产物是 NSIS EXE；两个平台必须在各自操作系统上构建，因为 LAMMPS、Python 扩展和 OVITO 都包含平台原生二进制文件。

## 1. 交付结构

```text
MatterLab installer
├── Electron desktop shell
├── React production build
├── FastAPI / Agent source resources
├── runtime.tar.gz
    ├── Python 3.12
    ├── scientific dependencies
    ├── FFmpeg
    └── OVITO
└── platform LAMMPS + potentials
```

首次启动时，Electron 将 `runtime.tar.gz` 解压到用户数据目录并运行 `conda-unpack`。这样安装目录保持只读，代码签名内容不会在启动后被修改，科学运行时仍能迁移到实际安装路径。macOS 从 conda-forge 构建 LAMMPS；conda-forge 当前没有 Windows LAMMPS 包，因此 Windows 流水线固定下载官方 `4Jul2026` 非 MPI 安装包，核对 SHA-256 后静默展开并随 MatterLab 一起交付。

## 2. 普通用户启动

### macOS

1. 打开 `MatterLab-<version>-<arch>.dmg`。
2. 将 `MatterLab 研材体` 拖入“应用程序”。
3. 从“应用程序”双击启动。
4. 等待首次运行时初始化完成，然后在设置页填写 API Base URL、模型与 API Key。

### Windows

1. 双击 `MatterLab-Setup-<version>-x64.exe`。
2. 安装程序自动写入当前用户目录，并建立桌面和开始菜单快捷方式。
3. 启动 `MatterLab 研材体`，等待首次运行时初始化。
4. 在设置页填写 API 配置。

开发构建默认没有商业代码签名，系统可能显示安全提醒。正式发行需要另外配置证书，不能通过在仓库中写入私钥来解决。

## 3. 传统前后端启动

开发者仍然使用原有方式：

```bash
# Terminal 1
cd backend
conda run -n lammps_agent uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5174
```

浏览器访问 `http://127.0.0.1:5174`。桌面模式通过 `MATTERLAB_DESKTOP_FRONTEND_DIR` 才会挂载生产前端，因此普通开发模式不会受到桌面入口影响。

## 4. 一键构建安装包

推荐打开 GitHub 仓库的 **Actions → Desktop installers → Run workflow**。工作流会并行使用原生 macOS arm64 和 Windows x64 Runner，并在完成后提供两个可下载 artifact。Tag 名以 `v` 开头时也会自动构建。

构建流水线依次执行：

```text
create target-platform Conda environment
  → install backend runtime dependencies
  → npm ci + Vite desktop build
  → validate LAMMPS / FFmpeg / OVITO / backend tests
  → conda-pack runtime.tar.gz
  → electron-builder DMG or NSIS EXE
  → upload installer artifact
```

工作流文件位于 `.github/workflows/desktop-build.yml`。

## 5. macOS 本机构建

先准备专用构建环境和 Node 依赖：

```bash
conda env create -f desktop/environment.yml
conda install -n matterlab-desktop -y -c conda-forge lammps
conda run -n matterlab-desktop python -m pip install \
  -r backend/requirements/base.txt \
  -r backend/requirements/visualization.txt
npm ci --prefix frontend
npm ci --prefix desktop
```

再生成生产前端、图标、运行时和 DMG：

```bash
VITE_DESKTOP_BUILD=true npm --prefix frontend run build
conda run -n matterlab-desktop python desktop/scripts/check_runtime.py
conda run -n matterlab-desktop python desktop/scripts/build_icons.py
conda run -n matterlab-desktop python desktop/scripts/prepare_runtime.py
CSC_IDENTITY_AUTO_DISCOVERY=false npm --prefix desktop run dist:mac
```

结果位于 `desktop/release/`。Apple Silicon 与 Intel 运行时不能简单合并；需要支持两种架构时，应分别在对应架构 Runner 上构建，而不是把单架构 Conda 环境伪装成 universal app。

## 6. Windows 本机构建

在 Windows PowerShell 或 Git Bash 中执行与 macOS 相同的环境和前端准备步骤，最后运行：

```powershell
$env:VITE_DESKTOP_BUILD = "true"
desktop/scripts/prepare_windows_lammps.ps1
npm --prefix frontend run build
conda run -n matterlab-desktop python desktop/scripts/check_runtime.py
conda run -n matterlab-desktop python desktop/scripts/build_icons.py
conda run -n matterlab-desktop python desktop/scripts/prepare_runtime.py
$env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
npm --prefix desktop run dist:win
```

Windows 安装程序输出到 `desktop/release/`。

## 7. 本地数据与安全

安装版只监听 `127.0.0.1`，并为每次启动选择空闲端口。前端和 API 同源运行，桌面 renderer 开启 `sandbox` 与 `contextIsolation`，禁用 Node integration；Python、LAMMPS、OVITO、FFmpeg 与 MCP 子进程仍统一经过项目的 `SandboxRunner`。

| 数据 | 安装版位置 |
| --- | --- |
| API Key | `<userData>/config/.env` |
| 非敏感运行配置 | `<userData>/config/llm_config.json` |
| 对话、记忆和计算产物 | `<userData>/outputs/` |
| 后端启动日志 | `<userData>/logs/backend.log` |
| 展开的计算运行时 | `<userData>/runtime-v1-<platform>-<arch>/` |

任何 API Key、签名证书或公证密码都不应提交到 Git。代码签名信息应通过 CI Secret 注入。
