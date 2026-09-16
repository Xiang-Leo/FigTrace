# 运行 FigTrace 0.3

这是可运行的个人图库初版。产品与浏览器交互方案已确认，当前功能边界如下。

AI 服务配置和使用步骤见 [AI 分类与图片生成](ai.md)。

## 已实现

- 独立项目管理：创建空项目、名称/说明/颜色、素材数量、重命名、归档/取消归档、删除未关联内容的空项目。已有项目字段自动迁移。
- 项目工作区：展示项目说明、Figure/版本数量与状态统计。从项目页上传、关联目录或打开 AI 生成时，默认选择当前项目；已有未完成上传队列保留原归属。
- Figure 状态：草稿、待审核、定稿；详情编辑、批量修改与状态筛选。旧图库自动迁移为草稿，归组采用第一张 Figure 的状态，拆分继承原状态。
- 文件改名/移动追踪：扫描发现内容匹配且对应关系唯一时，保留原 Figure、标签、项目、版本和源文件关联，记录旧路径与新路径。支持登记目录内及登记目录之间的移动。
- AI 分类：可配置 OpenAI 兼容的图片输入模型，单张或批量分析，建议标签/分类/描述/识别文字，用户采纳后追加标签和备注。
- AI 图片生成：提示词、项目、可选尺寸与质量，后台生成后自动入库，保留模型、参数、提示词与生成记录。
- 浏览器工作台：项目与目录筛选、关键词搜索、分页图库、明暗主题、图片详情。
- 目录原位索引：递归扫描、每小时增量检查、手动重扫、失联提示与目录重新定位；跳过符号链接。
- 浏览器导入：多选文件、选择文件夹、目录拖拽（视浏览器能力）、统一项目与标签、来源层级保留。
- 文件传输：3 文件并发、4 MB 分片、客户端完整 SHA-256、服务端完整校验、暂停/继续、失败重试；刷新后重选相同文件可恢复。未完成的上传保留 7 天，单文件上限 20 GB。
- 同名不同内容保留不同记录；重复上传内容复用磁盘存储，并保留来源。目录文件在后台确认内容指纹后可复用缩略图。
- 手动版本归组、拆分、指定当前采用版、并排比较；批量追加标签和修改项目。
- 关联源文件、脚本与数据文件的后端路径，下载原文件或关联文件、复制路径。
- JPG/PNG/WebP/BMP/GIF 静态预览、常规 TIFF 与 PDF 分页预览；条件支持 PSD 内嵌合成预览和含 PDF 内容的 AI；EPS 需额外安装 Ghostscript。
- 每日默认自动备份、周期与保留数量设置、立即备份、备份下载与恢复。恢复前自动保存当前图库信息，恢复后重建预览。
- 单用户远程模式：密码登录、同源请求校验、主机名与素材根目录限制；同一后端提供静态前端和 API。

## 尚未覆盖

- 导入后无人确认的自动 AI 分类、语义搜索、参考图编辑、仅返回 URL 的图片生成服务，以及外部网盘 API 直连。
- SVG 预览、PSD 完整图层渲染、全部 Illustrator/EPS 变体、多通道科研 TIFF 专业显示与色彩精确校样。失败素材仍可管理。
- 在浏览器中直接调用桌面编辑软件；当前提供下载与复制原路径。
- 原文件全量归档与源文件历史快照。当前备份不包含原图片、上传的文件副本和关联源文件，需另行备份这些目录。
- 多用户权限、跨设备双向同步、多实例后端和移动端常驻服务。

目录扫描的“未下载”检测取决于操作系统与网盘客户端标记，不能覆盖所有占位实现。默认选择已下载的目录；只有明确允许时才读取已识别的在线文件。文件本身被编辑时，索引更新该文件的记录；需要保留旧版时应另存文件并进行版本归组。

自动找回要求旧记录已有 SHA-256、旧路径已消失但旧登记目录仍可访问，且本次扫描中新旧内容候选一一对应、扩展名相同。复制文件、移动同时修改内容、未完成指纹计算、重复内容导致歧义或旧目录离线时，不自动合并。图库标题和项目归属保持原值，实际文件名与路径更新；关联脚本等源文件的路径不会猜测改写。移动记录显示在详情底部，最近 50 条可见，并纳入图库信息备份。

后台扫描和预览保持单任务执行以限制资源占用，但耗时转换不再持有上传写入锁；预览先写临时文件，再原子替换。备份恢复及目录重新定位在后台扫描/预览运行时会提示稍后重试。大文件完成上传时的校验、备份及按需 PDF/TIFF 分页仍有串行处理，不能保证所有操作完全无等待。

## 本机启动

需要 Python 3.11+、Node.js 22.12+，在项目根目录操作。Python 环境只创建在当前项目中。

macOS / Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e . --no-deps
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/figtrace
```

Windows PowerShell：

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock
.venv\Scripts\python -m pip install -e . --no-deps
npm --prefix frontend ci
npm --prefix frontend run build
.venv\Scripts\figtrace
```

打开 [本机工作台](http://127.0.0.1:8765)。默认只监听本机地址，停止终端进程会停止后台扫描与备份；关闭浏览器不会停止后端。若希望开机自动运行，可将该命令交给操作系统服务管理器，当前版本不自动安装系统服务。

可指定数据目录和端口：

```sh
.venv/bin/figtrace --data-dir /path/to/local/figtrace-data --port 8765
```

默认数据目录：

- macOS：`~/Library/Application Support/FigTrace`
- Windows：`%LOCALAPPDATA%\FigTrace`
- Linux：`$XDG_DATA_HOME/figtrace`，未配置时为 `~/.local/share/figtrace`

目录内包含 `library.sqlite3`、`originals`（上传副本）、`cache`（可重建预览）、`uploads`（临时分片）和 `backups`。运行中的数据库应放在本地持久化磁盘；可以把成功生成的备份包同步到网盘。

首次使用可点击“关联已有目录”，选择现有素材文件夹，原文件不移动。目录中不受支持的脚本与数据不会作为图片卡片扫描，但可以在图片详情的“关联文件”里填写其路径。

## 开发模式

一个终端运行后端，另一个运行：

```sh
npm --prefix frontend run dev
```

Vite 开发服务将 `/api` 代理到本机 `8765`。若修改后端端口，需要同步修改代理配置。生产前端无需 CDN、外部字体或网络连接。

## 自托管部署

已提供 `Dockerfile` 与 `compose.yaml`，默认单服务、单进程。在 `.env` 中自行设置以下变量，`.env` 已加入 Git 忽略：

```dotenv
FIGTRACE_PASSWORD=替换为你自己的访问密码
FIGTRACE_HOSTS=localhost,127.0.0.1,figtrace.example.com
FIGTRACE_SOURCE_DIR=/absolute/path/to/figures
```

```sh
docker compose up --build -d
```

Compose 默认仅把端口发布到服务器回环地址。外网访问通过 HTTPS 反向代理连接该端口，保留原始 Host，并配置正确的代理转发头。不要在公开网络以明文 HTTP 发送密码。容器中 `/sources` 是只读素材挂载，`/data` 是本地持久化卷；应用上传的副本写入 `/data/originals`。停止或重建容器时应保留该卷。

非容器的远程运行需要同时设置 `FIGTRACE_PASSWORD`、`FIGTRACE_HOSTS` 和 `FIGTRACE_ALLOWED_ROOTS`，然后以 `--host 0.0.0.0` 启动。允许根目录以操作系统路径分隔符分隔：macOS/Linux 为 `:`，Windows 为 `;`。远程模式可通过界面修改备份时间，但不能任意重定向服务器备份路径。

此版本限单个后端进程。不要使用多个 Uvicorn worker，也不要让多个实例共同打开同一图库目录。服务器部署只能访问服务器磁盘和挂载目录；网页不会自动获得访问者电脑上的持续目录访问权。

## 检查与验收

```sh
.venv/bin/python -m pytest -q
npm --prefix frontend run build
.venv/bin/python scripts/benchmark.py --count 5000
```

自动化测试覆盖分片续传、服务重启、内容校验、路径限制、同名文件、预览失败隔离、PDF/TIFF 分页、目录迁移、版本与源文件关联、批量元数据更新和备份恢复。0.3 新增改名/跨目录找回、歧义不合并、离线目录保护、转换期间上传、Figure 状态与旧库迁移测试。CI 配置 Windows、macOS、Linux 矩阵，各次提交结果以 GitHub Checks 为准。

0.1 初版本机测量（macOS、Python 3.14）：5,000 个 32×24 PNG 文件索引耗时约 8.49 秒；30 次预热 API 查询每页 48 条，中位数 10.5 毫秒，P95 11.7 毫秒。该测试不含 5,000 张缩略图转换、浏览器渲染、真实大文件或网盘读取，不作为当前版本的通用性能保证。

当前本机已验证前端构建和浏览器核心流程；Docker 镜像以及 Windows/Linux 的实际运行仍待对应环境验证。EPS/Ghostscript 与真实复杂 AI/PSD 样本也需要进一步兼容性验证。
