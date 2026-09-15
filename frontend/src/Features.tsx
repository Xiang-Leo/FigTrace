import { useEffect, useRef, useState } from "react";
import { X, FolderPlus, Sparkles } from "lucide-react";
import { api, json, when } from "./api";

type Project = {
  id: string;
  name: string;
  description: string;
  color: string;
  archived: number;
  figures: number;
  assets: number;
};
type Config = {
  base_url: string;
  model: string;
  has_key: boolean;
  api_key?: string;
  clear_key?: boolean;
};
type Task = {
  title?: string;
  preview?: string;
  id: string;
  kind: string;
  status: string;
  asset_id: string;
  prompt: string;
  model: string;
  base_url: string;
  created: number;
  error: string;
  result: {
    category?: string;
    description?: string;
    tags?: string[];
    visible_text?: string;
    revised_prompt?: string;
  };
};
const taskStatus: Record<string, string> = {
  queued: "排队中",
  running: "正在请求 AI",
  completed: "已完成",
  failed: "失败",
  applied: "已采纳",
  dismissed: "已取消 / 忽略",
};

function Shell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} className="modal feature-modal" onCancel={onClose}>
      <header className="feature-header">
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭" onClick={onClose}>
          <X size={20} />
        </button>
      </header>
      {children}
    </dialog>
  );
}

export function ProjectManager({
  onClose,
  onChange,
  onOpen,
}: {
  onClose: () => void;
  onChange: () => void;
  onOpen: (name: string) => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]),
    [editing, setEditing] = useState<Project | null>(null),
    [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [color, setColor] = useState("#6b7c61"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [archived, setArchived] = useState(false);
  async function load() {
    setProjects(await api("/projects"));
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);
  function edit(p: Project | null) {
    setEditing(p);
    setName(p?.name || "");
    setDescription(p?.description || "");
    setColor(p?.color || "#6b7c61");
    setError("");
  }
  async function action(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      await load();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Shell title="项目管理" onClose={onClose}>
      <p className="subtle">
        按论文、课题或交付任务组织
        Figure。项目可先创建，再导入素材；重命名会同步已有归属。
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="project-layout">
        <section>
          <div className="button-row">
            <button onClick={() => edit(null)}>
              <FolderPlus size={16} />
              新建项目
            </button>
            <label className="check">
              <input
                type="checkbox"
                checked={archived}
                onChange={(e) => setArchived(e.target.checked)}
              />
              显示已归档
            </label>
          </div>
          <div className="project-list">
            {projects
              .filter((p) => archived || !p.archived)
              .map((p) => (
                <article
                  className="project-card"
                  key={p.id}
                  style={{ borderLeftColor: p.color }}
                >
                  <div className="feature-header">
                    <strong>{p.name}</strong>
                    {!!p.archived && <small>已归档</small>}
                  </div>
                  <p className="subtle">{p.description || "暂无项目说明"}</p>
                  <p>
                    {p.figures} 张 Figure · {p.assets} 个文件版本
                  </p>
                  <div className="button-row">
                    <button onClick={() => onOpen(p.name)}>打开项目</button>
                    <button disabled={busy} onClick={() => edit(p)}>
                      编辑
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        action(() =>
                          api(
                            "/projects/" + p.id,
                            json("PUT", { ...p, archived: !p.archived }),
                          ),
                        )
                      }
                    >
                      {p.archived ? "取消归档" : "归档"}
                    </button>
                  </div>
                </article>
              ))}
            {!projects.length && (
              <p className="subtle">还没有项目。从右侧创建第一个项目。</p>
            )}
          </div>
        </section>
        <form
          className="feature-form"
          onSubmit={(e) => {
            e.preventDefault();
            action(async () => {
              await api(
                "/projects" + (editing ? "/" + editing.id : ""),
                json(editing ? "PUT" : "POST", {
                  name,
                  description,
                  color,
                  archived: !!editing?.archived,
                }),
              );
              edit(null);
            });
          }}
        >
          <h3>{editing ? "编辑项目" : "创建项目"}</h3>
          <label>
            项目名称
            <input
              required
              maxLength={200}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：论文 · 单细胞图谱"
            />
          </label>
          <label>
            项目说明
            <textarea
              rows={4}
              maxLength={3000}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="研究主题、交付目标或整理约定"
            />
          </label>
          <label>
            项目颜色
            <input
              type="color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
            />
          </label>
          <button className="primary" disabled={busy || !name.trim()}>
            {editing ? "保存项目" : "创建项目"}
          </button>
          {editing && editing.figures === 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                action(async () => {
                  await api("/projects/" + editing.id, json("DELETE"));
                  edit(null);
                })
              }
            >
              删除空项目
            </button>
          )}
          <p className="subtle">归档后项目从侧栏隐藏，素材仍保留在图库中。</p>
        </form>
      </div>
    </Shell>
  );
}

function Suggestion({
  task,
  onApply,
}: {
  task: Task;
  onApply: (tags: string[], description: boolean) => void;
}) {
  const [tags, setTags] = useState(task.result.tags || []),
    [include, setInclude] = useState(true);
  return (
    <>
      <p>
        <strong>{task.result.category}</strong> · {task.result.description}
      </p>
      <div className="suggestion-tags">
        {task.result.tags?.map((t) => (
          <label className="check" key={t}>
            <input
              type="checkbox"
              checked={tags.includes(t)}
              onChange={(e) =>
                setTags(
                  e.target.checked ? [...tags, t] : tags.filter((x) => x !== t),
                )
              }
            />
            {t}
          </label>
        ))}
      </div>
      {task.result.visible_text && (
        <details>
          <summary>识别到的文字</summary>
          <p>{task.result.visible_text}</p>
        </details>
      )}
      <label className="check">
        <input
          type="checkbox"
          checked={include}
          onChange={(e) => setInclude(e.target.checked)}
        />
        将分类与描述追加到备注（可搜索）
      </label>
      <button onClick={() => onApply(tags, include)}>采纳所选建议</button>
    </>
  );
}

export function AIWorkspace({
  initialTab,
  assetIds,
  onClose,
  onChange,
  onSelect,
}: {
  initialTab: string;
  assetIds: string[];
  onClose: () => void;
  onChange: (assetId?: string) => void;
  onSelect: (id: string) => void;
}) {
  const [tab, setTab] = useState(initialTab),
    [configs, setConfigs] = useState<Record<string, Config> | null>(null),
    [projects, setProjects] = useState<Project[]>([]),
    [tasks, setTasks] = useState<Task[]>([]),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [prompt, setPrompt] = useState(""),
    [project, setProject] = useState(""),
    [size, setSize] = useState(""),
    [quality, setQuality] = useState(""),
    [models, setModels] = useState<Record<string, string[]>>({});
  const requestId = useRef(crypto.randomUUID());
  async function load() {
    setTasks(await api("/ai/tasks"));
  }
  useEffect(() => {
    Promise.all([api("/ai/config"), api("/projects"), api("/ai/tasks")])
      .then(([c, p, t]) => {
        setConfigs(c);
        setProjects(p);
        setTasks(t);
      })
      .catch((e) => setError(e.message));
    const timer = setInterval(
      () => load().catch((e) => setError(e.message)),
      2500,
    );
    return () => clearInterval(timer);
  }, []);
  async function action(work: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      await load();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const visibleTasks =
    tab === "analysis"
      ? tasks.filter(
          (t) =>
            t.kind === "analysis" &&
            (!assetIds.length || assetIds.includes(t.asset_id)),
        )
      : tab === "generation"
        ? tasks.filter((t) => t.kind === "generation")
        : tasks;
  return (
    <Shell title="AI 工作台" onClose={onClose}>
      <div className="feature-tabs">
        {[
          ["analysis", "图片分类"],
          ["generation", "图片生成"],
          ["history", "任务记录"],
          ["config", "AI 设置"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={tab === key ? "primary" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {tab === "config" && configs && (
        <>
          <p className="subtle">
            支持 OpenAI 或兼容接口。填写完整 Base URL（通常以 /v1
            结尾）及服务商提供的模型
            ID。密钥仅保存于后端本机文件，不返回浏览器、不纳入图库备份。
          </p>
          <div className="provider-grid">
            {["analysis", "generation"].map((kind) => {
              const c = configs[kind];
              return (
                <form
                  className="feature-form"
                  key={kind}
                  onSubmit={(e) => {
                    e.preventDefault();
                    action(async () => {
                      const result = await api(
                        "/ai/config/" + kind,
                        json("PUT", c),
                      );
                      setConfigs((prev) => ({
                        ...prev!,
                        [kind]: result[kind],
                      }));
                      setNotice(
                        "已保存。更换服务地址会清除旧密钥，请为新服务重新填写。",
                      );
                    });
                  }}
                >
                  <h3>{kind === "analysis" ? "分类服务" : "图片生成服务"}</h3>
                  <label>
                    API Base URL
                    <input
                      required
                      type="url"
                      value={c.base_url}
                      onChange={(e) =>
                        setConfigs({
                          ...configs,
                          [kind]: { ...c, base_url: e.target.value },
                        })
                      }
                    />
                  </label>
                  <label>
                    模型 ID
                    <input
                      required
                      list={"models-" + kind}
                      value={c.model}
                      onChange={(e) =>
                        setConfigs({
                          ...configs,
                          [kind]: { ...c, model: e.target.value },
                        })
                      }
                      placeholder={
                        kind === "analysis"
                          ? "支持图片输入的模型 ID"
                          : "支持图片生成的模型 ID"
                      }
                    />
                    <datalist id={"models-" + kind}>
                      {models[kind]?.map((m) => (
                        <option key={m} value={m} />
                      ))}
                    </datalist>
                  </label>
                  <label>
                    API Key
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={c.api_key || ""}
                      onChange={(e) =>
                        setConfigs({
                          ...configs,
                          [kind]: { ...c, api_key: e.target.value },
                        })
                      }
                      placeholder={
                        c.has_key
                          ? "已保存；留空保留原密钥"
                          : "填写密钥，本机无鉴权服务可留空"
                      }
                    />
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={c.clear_key || false}
                      onChange={(e) =>
                        setConfigs({
                          ...configs,
                          [kind]: { ...c, clear_key: e.target.checked },
                        })
                      }
                    />
                    清除已存密钥
                  </label>
                  <div className="button-row">
                    <button className="primary" disabled={busy}>
                      保存配置
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        action(async () => {
                          const response = await api(
                            "/ai/test/" + kind,
                            json("POST"),
                          );
                          setModels({ ...models, [kind]: response.models });
                          setNotice(
                            "已连接到已保存的服务，获取到 " +
                              response.models.length +
                              " 个模型。具体能力以服务商为准。",
                          );
                        })
                      }
                    >
                      检查已保存服务
                    </button>
                  </div>
                  <p className="subtle">
                    {kind === "analysis"
                      ? "使用 /chat/completions，发送图片预览。"
                      : "使用 /images/generations，需返回 b64_json 图片。"}{" "}
                    未填写尺寸与质量时使用服务默认值。
                  </p>
                </form>
              );
            })}
          </div>
        </>
      )}
      {tab === "analysis" && (
        <section className="ai-intro">
          <Sparkles size={24} />
          <h3>
            {assetIds.length
              ? "分析选中的 " + assetIds.length + " 张图片"
              : "先从图库选择图片"}
          </h3>
          <p>
            点击分析后，将所选图片的预览发送到你配置的服务。多页 PDF / TIFF
            仅分析第一页。分类建议经你采纳后才追加到标签和备注。
          </p>
          <p className="subtle">
            服务：{configs?.analysis.base_url} · 模型：
            {configs?.analysis.model || "尚未配置"} · 按服务商规则计费
          </p>
          <button
            className="primary"
            disabled={busy || !assetIds.length || !configs?.analysis.model}
            onClick={() =>
              action(async () => {
                await api("/ai/analyze", json("POST", { asset_ids: assetIds }));
                setNotice("已加入分类队列，可以关闭窗口继续整理。");
              })
            }
          >
            开始分析 {assetIds.length || ""} 张图片
          </button>
          {!configs?.analysis.model && (
            <button onClick={() => setTab("config")}>配置分类服务</button>
          )}
        </section>
      )}
      {tab === "generation" && (
        <form
          className="feature-form generation-form"
          onSubmit={(e) => {
            e.preventDefault();
            action(async () => {
              await api(
                "/ai/generate",
                json("POST", {
                  request_id: requestId.current,
                  prompt,
                  project_id: project || null,
                  size,
                  quality,
                }),
              );
              requestId.current = crypto.randomUUID();
              setPrompt("");
              setNotice("生成任务已提交，完成后自动保存到图库。");
            });
          }}
        >
          <label>
            图片提示词
            <textarea
              required
              rows={4}
              maxLength={12000}
              value={prompt}
              onChange={(e) => {
                setPrompt(e.target.value);
                requestId.current = crypto.randomUUID();
              }}
              placeholder="描述用途、构图、配色与需要出现的文字，例如：用于论文导言的细胞信号通路示意图…"
            />
          </label>
          <div className="provider-grid">
            <label>
              保存到项目
              <select
                value={project}
                onChange={(e) => {
                  setProject(e.target.value);
                  requestId.current = crypto.randomUUID();
                }}
              >
                <option value="">未分配项目</option>
                {projects
                  .filter((p) => !p.archived)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              尺寸
              <select
                value={size}
                onChange={(e) => {
                  setSize(e.target.value);
                  requestId.current = crypto.randomUUID();
                }}
              >
                {[
                  ["", "服务默认"],
                  ["1024x1024", "方形 1024 × 1024"],
                  ["1536x1024", "横向 1536 × 1024"],
                  ["1024x1536", "竖向 1024 × 1536"],
                ].map(([v, n]) => (
                  <option key={v} value={v}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <label>
              质量
              <select
                value={quality}
                onChange={(e) => {
                  setQuality(e.target.value);
                  requestId.current = crypto.randomUUID();
                }}
              >
                {[
                  ["", "服务默认"],
                  ["low", "低"],
                  ["medium", "中"],
                  ["high", "高"],
                ].map(([v, n]) => (
                  <option key={v} value={v}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="subtle">
            服务：{configs?.generation.base_url} · 模型：
            {configs?.generation.model || "尚未配置"}。每次生成 1 张，可能产生
            API 费用。科学示意图入库后请核对内容。
          </p>
          <div className="button-row">
            <button
              className="primary"
              disabled={busy || !prompt.trim() || !configs?.generation.model}
            >
              生成并保存到图库
            </button>
            {!configs?.generation.model && (
              <button type="button" onClick={() => setTab("config")}>
                配置生成服务
              </button>
            )}
          </div>
        </form>
      )}
      {tab !== "config" && (
        <section className="ai-history">
          <h3>{tab === "history" ? "最近 100 条任务" : "最近任务"}</h3>
          {!visibleTasks.length && <p className="subtle">暂无任务记录。</p>}
          {visibleTasks.map((task) => (
            <article className="ai-task" key={task.id}>
              <div className="feature-header">
                <strong>
                  {task.kind === "analysis"
                    ? "分类 · " + (task.title || "图片")
                    : task.prompt.slice(0, 80)}
                </strong>
                <span>{taskStatus[task.status]}</span>
              </div>
              <p className="subtle">
                {task.model} · {when(task.created)}
              </p>
              {task.error && <p className="error">{task.error}</p>}
              {task.kind === "analysis" &&
                task.status !== "completed" &&
                task.result.description && (
                  <details>
                    <summary>分类记录</summary>
                    <p>
                      {task.result.category} · {task.result.description}
                    </p>
                    <p>{task.result.tags?.join("、")}</p>
                  </details>
                )}
              {task.kind === "analysis" && task.status === "completed" && (
                <fieldset disabled={busy}>
                  <Suggestion
                    task={task}
                    onApply={(tags, include_description) =>
                      action(async () => {
                        await api(
                          "/ai/tasks/" + task.id + "/apply",
                          json("POST", { tags, include_description }),
                        );
                        onChange(task.asset_id);
                      })
                    }
                  />
                </fieldset>
              )}
              {task.kind === "generation" &&
                task.status === "completed" &&
                task.preview === "ready" &&
                task.asset_id && (
                  <img
                    className="generated-thumb"
                    src={"/api/assets/" + task.asset_id + "/preview"}
                    alt="生成图片预览"
                  />
                )}
              <div className="button-row">
                {task.asset_id && (
                  <button onClick={() => onSelect(task.asset_id)}>
                    查看图片
                  </button>
                )}
                {(task.status === "queued" ||
                  (task.kind === "analysis" &&
                    task.status === "completed")) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      action(() =>
                        api("/ai/tasks/" + task.id + "/dismiss", json("POST")),
                      )
                    }
                  >
                    {task.status === "queued" ? "取消排队" : "忽略建议"}
                  </button>
                )}
              </div>
              {task.kind === "generation" && (
                <details>
                  <summary>生成记录</summary>
                  <p>{task.prompt}</p>
                  {task.result.revised_prompt && (
                    <p>修订提示词：{task.result.revised_prompt}</p>
                  )}
                  <p className="subtle">{task.base_url}</p>
                </details>
              )}
            </article>
          ))}
        </section>
      )}
    </Shell>
  );
}
