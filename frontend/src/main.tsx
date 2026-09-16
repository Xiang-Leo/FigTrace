import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Images,
  Inbox,
  Folder,
  FolderPlus,
  Plus,
  Search,
  Settings2,
  ChevronLeft,
  ChevronRight,
  GitBranch,
  Check,
  Sun,
  Moon,
  Layers,
  Upload,
  ArrowUpRight,
  X,
  LogOut,
} from "lucide-react";
import { api, json, Asset, Overview, Root, ApiError, statusName } from "./api";
import { stageNames, ProjectSummary } from "./api";
import { ImportDialog } from "./ImportDialog";
import { Detail, Preview } from "./Detail";
import { Settings } from "./Settings";
import { ProjectManager, AIWorkspace } from "./Features";
import "./style.css";

function App() {
  const [session, setSession] = useState<{
      authenticated: boolean;
      password_required: boolean;
      local_mode: boolean;
    } | null>(null),
    [password, setPassword] = useState(""),
    [error, setError] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null),
    [assets, setAssets] = useState<Asset[]>([]),
    [total, setTotal] = useState(0),
    [page, setPage] = useState(1),
    [search, setSearch] = useState(""),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState({ type: "all", value: "" }),
    [stage, setStage] = useState(""),
    [projectInfo, setProjectInfo] = useState<ProjectSummary | null>(null),
    [grouped, setGrouped] = useState(true),
    [selected, setSelected] = useState<string | null>(null),
    [checked, setChecked] = useState<Set<string>>(new Set()),
    [loading, setLoading] = useState(false),
    [revision, setRevision] = useState(0);
  const [modal, setModal] = useState(""),
    [aiSelection, setAISelection] = useState<string[]>([]),
    [importMode, setImportMode] = useState("upload"),
    [relocate, setRelocate] = useState<Root | null>(null),
    [theme, setTheme] = useState(
      () => localStorage.getItem("figtrace-theme") || "system",
    ),
    [groupConfirm, setGroupConfirm] = useState(false),
    [busy, setBusy] = useState(false),
    [toast, setToast] = useState("");
  const groupDialog = useRef<HTMLDialogElement>(null),
    requestVersion = useRef(0);
  const bulkDialog = useRef<HTMLDialogElement>(null),
    [bulkOpen, setBulkOpen] = useState(false),
    [bulkProject, setBulkProject] = useState(""),
    [bulkStage, setBulkStage] = useState(""),
    [bulkTags, setBulkTags] = useState("");
  useEffect(() => {
    api("/session")
      .then(setSession)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(search);
      setPage(1);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("figtrace-theme", theme);
  }, [theme]);
  useEffect(() => {
    if (groupConfirm) groupDialog.current?.showModal();
    else groupDialog.current?.close();
  }, [groupConfirm]);
  useEffect(() => {
    if (bulkOpen) bulkDialog.current?.showModal();
    else bulkDialog.current?.close();
  }, [bulkOpen]);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(""), 3500);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  const refresh = useCallback(
    async (background = false) => {
      if (!session?.authenticated) return;
      const request = ++requestVersion.current;
      if (!background) setLoading(true);
      const params = new URLSearchParams({
        q: query,
        page: String(page),
        grouped: String(grouped),
      });
      if (filter.type === "project") params.set("project", filter.value);
      if (filter.type === "root") params.set("root_id", filter.value);
      if (filter.type === "pending") params.set("pending", "true");
      if (stage) params.set("stage", stage);
      try {
        const [list, summary, projects] = await Promise.all([
          api("/assets?" + params),
          api("/overview"),
          filter.type === "project"
            ? api<ProjectSummary[]>("/projects")
            : Promise.resolve([]),
        ]);
        if (request !== requestVersion.current) return;
        setAssets(list.items);
        setTotal(list.total);
        setOverview(summary);
        setProjectInfo(
          projects.find((p: ProjectSummary) => p.name === filter.value) || null,
        );
        setError("");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401)
          setSession((s) => (s ? { ...s, authenticated: false } : s));
        else setError((e as Error).message);
      } finally {
        if (request === requestVersion.current) setLoading(false);
      }
    },
    [session?.authenticated, query, page, grouped, filter, revision, stage],
  );
  useEffect(() => {
    refresh();
    const timer = setInterval(() => refresh(true), 4000);
    return () => clearInterval(timer);
  }, [refresh]);
  function update() {
    setRevision((v) => v + 1);
  }
  function navigate(type: string, value = "") {
    setFilter({ type, value });
    setPage(1);
    setChecked(new Set());
    setStage("");
    setProjectInfo(null);
  }
  function openImport(mode = "upload") {
    setImportMode(mode);
    setRelocate(null);
    setModal("import");
  }
  async function login(event: React.FormEvent) {
    event.preventDefault();
    try {
      await api("/login", json("POST", { password }));
      setPassword("");
      setSession(await api("/session"));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function merge() {
    setBusy(true);
    try {
      const result = await api(
        "/group",
        json("POST", { asset_ids: Array.from(checked) }),
      );
      setChecked(new Set());
      setGroupConfirm(false);
      setSelected(null);
      setTimeout(() => setSelected(result.asset_id), 0);
      update();
      setToast("已合并版本，可在详情中指定当前采用版");
    } catch (e) {
      setError((e as Error).message);
      setGroupConfirm(false);
    } finally {
      setBusy(false);
    }
  }
  async function organize(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api(
        "/bulk",
        json("POST", {
          asset_ids: Array.from(checked),
          project: bulkProject || null,
          stage: bulkStage || null,
          tags: bulkTags
            .split(/[,，]/)
            .map((t) => t.trim())
            .filter(Boolean),
        }),
      );
      setBulkOpen(false);
      setChecked(new Set());
      setSelected(null);
      setBulkProject("");
      setBulkStage("");
      setBulkTags("");
      update();
      setToast("已批量更新项目、状态与标签");
    } catch (e) {
      setError((e as Error).message);
      setBulkOpen(false);
    } finally {
      setBusy(false);
    }
  }
  const title =
    filter.type === "project"
      ? filter.value
      : filter.type === "root"
        ? overview?.roots.find((r) => r.id === filter.value)?.name || "素材目录"
        : filter.type === "pending"
          ? "待整理"
          : "全部图片";
  if (!session)
    return (
      <div className="loading-screen">
        <span className="brand">FigTrace</span>
        <p>{error || "正在连接图库…"}</p>
        {error && <button onClick={() => location.reload()}>重新连接</button>}
      </div>
    );
  if (!session.authenticated)
    return (
      <main className="login-page">
        <form onSubmit={login}>
          <span className="brand">FigTrace</span>
          <h1>连接你的图库</h1>
          <p>输入服务器访问密码，开始整理你的 Figure。</p>
          <label>
            访问密码
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              autoFocus
            />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary">
            进入工作台 <ArrowUpRight size={17} />
          </button>
        </form>
      </main>
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/">
          FigTrace
          <span className="brand-dot" />
        </a>
        <div className="library-label">
          <span className="connection-dot" />
          {session.local_mode ? "本机图库" : "服务器图库"}
          <small>LOCAL FIRST</small>
        </div>
        <nav aria-label="主导航">
          <button
            className={filter.type === "all" ? "active" : ""}
            onClick={() => navigate("all")}
          >
            <Images size={18} />
            <span>全部图片</span>
            <small>{overview?.figures || 0}</small>
          </button>
          <button
            className={filter.type === "pending" ? "active" : ""}
            onClick={() => navigate("pending")}
          >
            <Inbox size={18} />
            <span>待整理</span>
            <small>{overview?.pending || 0}</small>
          </button>
          <div className="nav-heading">项目</div>
          <button onClick={() => setModal("projects")}>
            <FolderPlus size={17} />
            <span>项目管理</span>
          </button>
          {overview?.projects.length ? (
            overview.projects.map((project) => (
              <button
                key={project}
                className={
                  filter.type === "project" && filter.value === project
                    ? "active"
                    : ""
                }
                onClick={() => navigate("project", project)}
              >
                <Folder size={16} />
                <span>{project}</span>
              </button>
            ))
          ) : (
            <p className="nav-hint">在项目管理中创建第一个项目</p>
          )}
          <div className="nav-heading">素材来源</div>
          {overview?.roots.map((root) => (
            <button
              key={root.id}
              className={
                filter.type === "root" && filter.value === root.id
                  ? "active"
                  : ""
              }
              onClick={() => navigate("root", root.id)}
            >
              {root.kind === "managed" ? (
                <Upload size={16} />
              ) : (
                <Folder size={16} />
              )}
              <span>{root.name}</span>
            </button>
          ))}
          <button
            className="add-directory"
            onClick={() => openImport("directory")}
          >
            <FolderPlus size={16} />
            关联目录
          </button>
        </nav>
        <div className="sidebar-bottom">
          <button
            onClick={() => {
              setAISelection([]);
              setModal("ai-generation");
            }}
          >
            ✦ AI 生成
          </button>
          <button
            onClick={() => {
              setAISelection([]);
              setModal("ai-config");
            }}
          >
            AI 设置与任务
          </button>
          <button onClick={() => setModal("settings")}>
            <Settings2 size={17} />
            设置与备份
          </button>
          <div className="theme-row">
            <button
              className="icon-button"
              aria-label="切换明暗主题"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <span>FigTrace 0.3</span>
            {session.password_required && (
              <button
                className="icon-button"
                aria-label="退出登录"
                onClick={async () => {
                  await api("/logout", json("POST"));
                  setSession({ ...session, authenticated: false });
                }}
              >
                <LogOut size={16} />
              </button>
            )}
          </div>
        </div>
      </aside>
      <main className="workspace">
        <header className="workspace-header">
          <div>
            <span className="eyebrow">YOUR VISUAL WORKSPACE</span>
            <h1>{title}</h1>
          </div>
          <button className="primary" onClick={() => openImport()}>
            <Plus size={18} />
            添加素材
          </button>
        </header>
        {filter.type === "project" && projectInfo && (
          <section
            className="project-overview"
            style={{ borderLeftColor: projectInfo.color }}
          >
            <p>
              {projectInfo.description ||
                "在这个项目中整理图片、追踪版本和确认定稿。"}
            </p>
            <div className="button-row">
              <span>
                {projectInfo.figures} 张 Figure · {projectInfo.assets} 个版本
                {projectInfo.archived ? " · 已归档" : ""}
              </span>
              {Object.entries(stageNames).map(([key, label]) => (
                <button
                  key={key}
                  className={stage === key ? "primary" : ""}
                  onClick={() => {
                    setStage(stage === key ? "" : key);
                    setPage(1);
                    setChecked(new Set());
                  }}
                >
                  {label} {projectInfo.stages[key] || 0}
                </button>
              ))}
            </div>
            <small>
              从此处添加素材或生成图片，会默认归入当前项目；已有上传队列保留原设置。
            </small>
          </section>
        )}
        <div className="search-toolbar">
          <div className="search-field">
            <Search size={18} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索图片、标签、项目或路径…"
              aria-label="搜索图片"
            />
            {search && (
              <button
                className="icon-button"
                aria-label="清除搜索"
                onClick={() => setSearch("")}
              >
                <X size={15} />
              </button>
            )}
          </div>
          <label className="check group-toggle">
            <input
              type="checkbox"
              checked={grouped}
              onChange={(e) => {
                setGrouped(e.target.checked);
                setPage(1);
                setChecked(new Set());
              }}
            />
            <Layers size={16} />
            折叠版本
          </label>
          <select
            aria-label="筛选 Figure 状态"
            value={stage}
            onChange={(e) => {
              setStage(e.target.value);
              setPage(1);
              setChecked(new Set());
            }}
          >
            <option value="">全部状态</option>
            {Object.entries(stageNames).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {error && (
          <div className="error workspace-error" role="alert">
            {error}
          </div>
        )}
        <div className="result-toolbar">
          <span>
            {loading
              ? "正在读取…"
              : total + " " + (grouped ? "张 Figure" : "个文件版本")}
          </span>
          {checked.size > 0 ? (
            <div className="button-row">
              <span>已选 {checked.size} 项</span>
              <button onClick={() => setBulkOpen(true)}>批量整理</button>
              <button
                onClick={() => {
                  setAISelection(Array.from(checked));
                  setModal("ai-analysis");
                }}
              >
                AI 分类
              </button>
              <button
                disabled={checked.size < 2}
                onClick={() => setGroupConfirm(true)}
              >
                <GitBranch size={15} />
                合并版本
              </button>
              <button onClick={() => setChecked(new Set())}>取消选择</button>
            </div>
          ) : (
            <span>
              {overview?.active_jobs
                ? `${overview.active_jobs} 个后台任务`
                : "按加入时间排序"}
            </span>
          )}
        </div>
        {assets.length ? (
          <div className="gallery-grid">
            {assets.map((asset) => (
              <article
                key={asset.id}
                className={
                  "asset-card " +
                  (selected === asset.id ? "selected" : "") +
                  (checked.has(asset.id) ? " checked" : "")
                }
              >
                <label
                  className="select-asset"
                  aria-label={"选择 " + asset.name}
                >
                  <input
                    type="checkbox"
                    checked={checked.has(asset.id)}
                    onChange={(e) => {
                      const next = new Set(checked);
                      if (e.target.checked) next.add(asset.id);
                      else next.delete(asset.id);
                      setChecked(next);
                    }}
                  />
                  <span>
                    <Check size={13} />
                  </span>
                </label>
                <button
                  className="asset-open"
                  onClick={() => setSelected(asset.id)}
                >
                  <Preview asset={asset} />
                  <div className="asset-caption">
                    <strong>{asset.title}</strong>
                    <span>
                      {asset.format.toUpperCase()}
                      {grouped && asset.versions > 1
                        ? " · " + asset.versions + " 个版本"
                        : ""}
                      {!grouped ? " · " + asset.name : ""}
                    </span>
                    <div className="asset-bottom">
                      <span>{asset.project || "待整理"}</span>
                      <span className={"figure-stage stage-" + asset.stage}>
                        {stageNames[asset.stage]}
                      </span>
                      {["failed", "unsupported", "missing"].includes(
                        asset.preview,
                      ) && (
                        <span className="status-label">
                          {statusName[asset.preview]}
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              </article>
            ))}
          </div>
        ) : (
          !loading && (
            <div className="empty-library">
              <div className="empty-art">
                <div />
                <div />
                <Images size={38} />
              </div>
              <span className="eyebrow">A HOME FOR YOUR FIGURES</span>
              <h2>
                {query
                  ? "没有找到匹配的图片"
                  : overview?.assets
                    ? "这个视图还没有素材"
                    : "让每张图，都有迹可循。"}
              </h2>
              <p>
                {query
                  ? "试试文件名、项目名称，或你添加过的标签。"
                  : overview?.assets
                    ? "添加素材，或修改项目和标签后在这里查看。"
                    : "把散落在文件夹里的图片、版本和源文件，整理到同一个工作台。"}
              </p>
              <div className="button-row">
                <button
                  className="primary"
                  onClick={() => openImport("directory")}
                >
                  <FolderPlus size={17} />
                  关联已有目录
                </button>
                <button onClick={() => openImport()}>
                  <Upload size={17} />
                  批量上传
                </button>
              </div>
              <small>原位索引 · 可追溯版本 · 本地数据库</small>
            </div>
          )
        )}
        {total > 48 && (
          <div className="pager gallery-pager">
            <button disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
              <ChevronLeft size={16} />
              上一页
            </button>
            <span>
              {page} / {Math.ceil(total / 48)}
            </span>
            <button
              disabled={page * 48 >= total}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
              <ChevronRight size={16} />
            </button>
          </div>
        )}
        <footer className="workspace-footer">
          <span>
            {overview?.assets || 0} 个原文件 · {overview?.figures || 0} 张
            Figure
          </span>
          <button onClick={() => setModal("settings")}>
            {overview?.backup.last_error
              ? "备份需要处理"
              : overview?.backup.has_backup
                ? "图库信息已有备份"
                : "尚无图库信息备份"}
            <ChevronRight size={13} />
          </button>
        </footer>
      </main>
      {selected && (
        <Detail
          key={selected}
          id={selected}
          onClose={() => setSelected(null)}
          onChange={update}
          onSelect={setSelected}
          onAI={() => {
            setAISelection([selected]);
            setModal("ai-analysis");
          }}
        />
      )}
      {modal === "projects" && (
        <ProjectManager
          onClose={() => setModal("")}
          onChange={() => {
            navigate("all");
            setSelected(null);
            update();
          }}
          onOpen={(name) => {
            navigate("project", name);
            setModal("");
          }}
        />
      )}
      {modal.startsWith("ai-") && (
        <AIWorkspace
          initialTab={modal.slice(3)}
          initialProject={filter.type === "project" ? filter.value : ""}
          assetIds={aiSelection}
          onClose={() => setModal("")}
          onChange={(assetId) => {
            update();
            if (assetId && assetId === selected) {
              setSelected(null);
              setTimeout(() => setSelected(assetId), 0);
            }
          }}
          onSelect={(id) => {
            setSelected(null);
            setTimeout(() => setSelected(id), 0);
            setModal("");
            update();
          }}
        />
      )}
      <ImportDialog
        open={modal === "import"}
        onClose={() => setModal("")}
        onRefresh={update}
        overview={overview}
        initialMode={importMode}
        initialProject={filter.type === "project" ? filter.value : ""}
        relocate={relocate}
      />
      <Settings
        open={modal === "settings"}
        onClose={() => setModal("")}
        overview={overview}
        onRefresh={update}
        onRelocate={(root) => {
          setRelocate(root);
          setImportMode("directory");
          setModal("import");
        }}
      />
      <dialog
        className="modal confirm-modal"
        ref={groupDialog}
        onCancel={() => setGroupConfirm(false)}
        onClose={() => setGroupConfirm(false)}
      >
        <h2>合并为同一张 Figure？</h2>
        <p>
          将选中 Figure
          的全部版本归到一起，合并标签并保留备注。第一项的名称、项目和状态作为合并结果，原文件保持原位。
        </p>
        <p className="subtle">之后可在“版本”中将某个文件重新独立成 Figure。</p>
        <div className="modal-actions">
          <button onClick={() => setGroupConfirm(false)}>取消</button>
          <button className="primary" disabled={busy} onClick={merge}>
            {busy ? "合并中…" : "合并版本"}
          </button>
        </div>
      </dialog>
      <dialog
        className="modal confirm-modal"
        ref={bulkDialog}
        onCancel={() => setBulkOpen(false)}
      >
        <form onSubmit={organize}>
          <h2>批量整理 {checked.size} 项</h2>
          <p className="subtle">
            新标签会追加到现有标签。项目留空时保留原项目。
          </p>
          <label>
            归入项目
            <input
              value={bulkProject}
              list="project-options"
              onChange={(e) => setBulkProject(e.target.value)}
              placeholder="保留原项目"
            />
          </label>
          <label style={{ marginTop: 14 }}>
            追加标签
            <input
              value={bulkTags}
              onChange={(e) => setBulkTags(e.target.value)}
              placeholder="用逗号分隔"
            />
          </label>
          <label style={{ marginTop: 14 }}>
            Figure 状态
            <select
              value={bulkStage}
              onChange={(e) => setBulkStage(e.target.value)}
            >
              <option value="">保留原状态</option>
              {Object.entries(stageNames).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div className="modal-actions">
            <button type="button" onClick={() => setBulkOpen(false)}>
              取消
            </button>
            <button className="primary" disabled={busy}>
              保存整理
            </button>
          </div>
        </form>
      </dialog>
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
