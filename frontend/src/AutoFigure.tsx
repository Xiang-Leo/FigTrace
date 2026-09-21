import { useEffect, useRef, useState } from "react";
import { Download, Pencil, RotateCw, Save, Sparkles, X } from "lucide-react";
import { api, json, when, Asset } from "./api";

type ServiceConfig = {
  enabled: boolean;
  base_url: string;
  has_token: boolean;
  provider: string;
  svg_model: string;
  model_base_url: string;
  has_api_key: boolean;
  sam_backend: string;
  has_sam_api_key: boolean;
};
type ConversionTask = {
  id: string;
  asset_id: string;
  status: string;
  remote_id: string;
  output_asset_id: string;
  error: string;
  message: string;
  created: number;
};
const taskLabels: Record<string, string> = {
  queued: "排队中",
  running: "重建示意图中",
  completed: "已保存为新版本",
  failed: "未完成",
};
function pendingRequest(assetId: string) {
  try {
    return (
      sessionStorage.getItem("autofigure-request:" + assetId) ||
      crypto.randomUUID()
    );
  } catch {
    return crypto.randomUUID();
  }
}
function hasPendingRequest(assetId: string) {
  try {
    return Boolean(sessionStorage.getItem("autofigure-request:" + assetId));
  } catch {
    return false;
  }
}

export function AutoFigureSettings({ active }: { active: boolean }) {
  const [config, setConfig] = useState<ServiceConfig | null>(null);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [samApiKey, setSamApiKey] = useState("");
  const [clearSamApiKey, setClearSamApiKey] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!active) return;
    let alive = true;
    setError("");
    setNotice("");
    setToken("");
    setClearToken(false);
    setApiKey("");
    setClearApiKey(false);
    setSamApiKey("");
    setClearSamApiKey(false);
    api<ServiceConfig>("/autofigure/config")
      .then((value) => {
        if (alive) setConfig(value);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [active]);
  async function save(test: boolean) {
    if (!config) return;
    if (config.enabled && !config.svg_model.trim()) {
      setError("请填写 AutoFigure 服务可用的 SVG 重建模型名称。");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await api<ServiceConfig>(
        "/autofigure/config",
        json("PUT", {
          enabled: config.enabled,
          base_url: config.base_url,
          provider: config.provider,
          svg_model: config.svg_model,
          model_base_url: config.model_base_url,
          api_key: clearApiKey ? "" : apiKey,
          clear_api_key: clearApiKey,
          sam_backend: config.sam_backend,
          sam_api_key: clearSamApiKey ? "" : samApiKey,
          clear_sam_api_key: clearSamApiKey,
          token: clearToken ? "" : token,
          clear_token: clearToken,
        }),
      );
      setConfig(saved);
      window.dispatchEvent(new Event("figtrace-autofigure-config"));
      setToken("");
      setClearToken(false);
      setApiKey("");
      setClearApiKey(false);
      setSamApiKey("");
      setClearSamApiKey(false);
      setNotice("AutoFigure 设置已保存");
      if (test) {
        await api("/autofigure/test", json("POST"));
        setNotice(
          "设置已保存，AutoFigure 服务连接正常。模型是否可用将在实际任务中验证。",
        );
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="settings-section autofigure-settings">
      <h3>
        <Sparkles size={18} /> 科研图编辑 · AutoFigure
      </h3>
      <p className="subtle">
        接入独立运行的 AutoFigure-Edit 服务，将图片重建为可编辑
        SVG。只有在图片详情中主动发起任务时，才会发送图片。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {config && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save(false);
          }}
        >
          <fieldset disabled={busy}>
            <label className="check">
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={(e) =>
                  setConfig({ ...config, enabled: e.target.checked })
                }
              />
              启用 AutoFigure 服务
            </label>
            <label>
              服务地址
              <input
                type="url"
                required
                value={config.base_url}
                placeholder="http://127.0.0.1:8788"
                onChange={(e) =>
                  setConfig({ ...config, base_url: e.target.value })
                }
              />
            </label>
            <div className="form-grid">
              <label>
                服务提供商
                <select
                  value={config.provider}
                  onChange={(e) =>
                    setConfig({ ...config, provider: e.target.value })
                  }
                >
                  <option value="custom">OpenAI 兼容服务</option>
                  <option value="openai_response">OpenAI Responses</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="bianxie">Bianxie</option>
                  <option value="gemini">Gemini</option>
                </select>
              </label>
              <label>
                SVG 重建模型
                <input
                  required={config.enabled}
                  value={config.svg_model}
                  placeholder="填写服务可用的模型名称"
                  maxLength={200}
                  onChange={(e) =>
                    setConfig({ ...config, svg_model: e.target.value })
                  }
                />
              </label>
            </div>
            <label>
              模型接口地址
              <input
                type="url"
                required
                value={config.model_base_url}
                placeholder="https://api.openai.com/v1"
                onChange={(e) =>
                  setConfig({ ...config, model_base_url: e.target.value })
                }
              />
            </label>
            <label>
              模型 API 密钥
              <input
                type="password"
                autoComplete="new-password"
                value={apiKey}
                disabled={clearApiKey}
                placeholder={
                  config.has_api_key
                    ? "已保存，留空保持原密钥"
                    : "用于 SVG 重建的模型密钥"
                }
                onChange={(e) => setApiKey(e.target.value)}
              />
            </label>
            {config.has_api_key && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={clearApiKey}
                  onChange={(e) => setClearApiKey(e.target.checked)}
                />
                清除已保存的模型密钥
              </label>
            )}
            <label>
              图像分割服务
              <select
                value={config.sam_backend}
                onChange={(e) =>
                  setConfig({ ...config, sam_backend: e.target.value })
                }
              >
                <option value="local">
                  本地 SAM（在 AutoFigure 服务运行）
                </option>
                <option value="fal">fal</option>
                <option value="roboflow">Roboflow</option>
              </select>
            </label>
            {config.sam_backend !== "local" && (
              <label>
                分割服务 API 密钥
                <input
                  type="password"
                  autoComplete="new-password"
                  value={samApiKey}
                  disabled={clearSamApiKey}
                  placeholder={
                    config.has_sam_api_key
                      ? "已保存，留空保持原密钥"
                      : "填写所选分割服务的 API 密钥"
                  }
                  onChange={(e) => setSamApiKey(e.target.value)}
                />
              </label>
            )}
            {config.has_sam_api_key && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={clearSamApiKey}
                  onChange={(e) => setClearSamApiKey(e.target.checked)}
                />
                清除已保存的分割服务密钥
              </label>
            )}
            <p className="subtle">
              模型密钥和分割服务密钥会随转换请求发送到上方 AutoFigure
              服务，请使用可信的服务地址。选择远程分割时，图片会发送至对应提供商。
            </p>
            <label>
              服务访问令牌（可选）
              <input
                type="password"
                autoComplete="new-password"
                value={token}
                disabled={clearToken}
                placeholder={
                  config.has_token
                    ? "已保存，留空保持原令牌"
                    : "服务启用访问保护时填写"
                }
                onChange={(e) => setToken(e.target.value)}
              />
            </label>
            {config.has_token && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={clearToken}
                  onChange={(e) => setClearToken(e.target.checked)}
                />
                清除已保存的服务令牌
              </label>
            )}
            <div className="button-row">
              <button type="submit">
                <Save size={15} />
                {busy ? "处理中…" : "保存设置"}
              </button>
              <button
                type="button"
                disabled={!config.enabled}
                onClick={() => void save(true)}
              >
                <RotateCw size={15} />
                保存并测试连接
              </button>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}

export function AutoFigureTools({
  asset,
  onChange,
  onSelect,
}: {
  asset: Asset;
  onChange: () => void;
  onSelect: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState<ServiceConfig | null>(null);
  const [tasks, setTasks] = useState<ConversionTask[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(() => hasPendingRequest(asset.id));
  const requestId = useRef(pendingRequest(asset.id));
  const observed = useRef(new Set<string>());
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const raster = ["jpg", "jpeg", "png", "webp", "bmp", "gif"].includes(
    asset.format.toLowerCase(),
  );

  useEffect(() => {
    if (!expanded) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setError("");
    function loadConfig() {
      api<ServiceConfig>("/autofigure/config", { signal: controller.signal })
        .then((value) => {
          if (alive) setConfig(value);
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    }
    loadConfig();
    window.addEventListener("figtrace-autofigure-config", loadConfig);
    async function poll() {
      try {
        const result = await api<ConversionTask[]>(
          "/autofigure/tasks?asset_id=" + encodeURIComponent(asset.id),
          { signal: controller.signal },
        );
        if (!alive) return;
        setTasks(result);
        for (const task of result) {
          if (task.status === "completed" && !observed.current.has(task.id)) {
            observed.current.add(task.id);
            onChangeRef.current();
          }
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) timer = setTimeout(poll, 2000);
      }
    }
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
      controller.abort();
      window.removeEventListener("figtrace-autofigure-config", loadConfig);
    };
  }, [expanded, asset.id]);

  async function start() {
    setBusy(true);
    setError("");
    try {
      try {
        sessionStorage.setItem(
          "autofigure-request:" + asset.id,
          requestId.current,
        );
      } catch {
        /* Keep the in-memory ID when storage is unavailable. */
      }
      const result = await api<{ task_id: string }>(
        "/autofigure/tasks",
        json("POST", { request_id: requestId.current, asset_id: asset.id }),
      );
      setUncertain(false);
      try {
        sessionStorage.removeItem("autofigure-request:" + asset.id);
      } catch {
        /* No persistent storage. */
      }
      requestId.current = crypto.randomUUID();
      setTasks((current) =>
        current.some((task) => task.id === result.task_id)
          ? current
          : [
              {
                id: result.task_id,
                asset_id: asset.id,
                status: "queued",
                remote_id: "",
                output_asset_id: "",
                error: "",
                message: "",
                created: Date.now() / 1000,
              },
              ...current,
            ],
      );
    } catch (e) {
      setUncertain(true);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function refresh(task: ConversionTask) {
    setBusy(true);
    setError("");
    try {
      const updated = await api<ConversionTask>(
        "/autofigure/tasks/" + task.id + "/refresh",
        json("POST"),
      );
      setTasks((current) =>
        current.map((entry) => (entry.id === updated.id ? updated : entry)),
      );
      if (updated.status === "completed") onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const running = tasks.some((task) =>
    ["queued", "running"].includes(task.status),
  );
  return (
    <section className="autofigure-tools">
      <div className="button-row">
        {asset.format.toLowerCase() === "svg" && (
          <button onClick={() => setEditing(true)}>
            <Pencil size={15} />
            编辑 SVG
          </button>
        )}
        <button aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          <Sparkles size={15} />
          转为可编辑示意图
        </button>
      </div>
      {expanded && (
        <div className="autofigure-convert">
          <p className="subtle">
            将「{asset.name}」发送到 AutoFigure 服务，重建为 SVG 并保存为同一
            Figure 的新版本。文本和图形需复核，部分图标可能保留为位图。
          </p>
          {config?.enabled ? (
            <p className="subtle autofigure-endpoint">
              接收服务：{config.base_url}
              <br />
              服务可能继续调用已配置的外部模型，并产生用量费用。
            </p>
          ) : (
            <p className="notice">
              请先在「图库设置」中启用并配置 AutoFigure 服务。
            </p>
          )}
          {!raster && (
            <p className="subtle">
              转换支持 JPG、PNG、WebP、BMP 和 GIF；其它格式请先导出为图片。SVG
              可直接使用上方编辑器。
            </p>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {uncertain && (
            <p className="subtle">
              提交结果尚未确认。再次点击会查询或重试同一请求，不会创建重复任务。
            </p>
          )}
          <button
            className="primary"
            disabled={
              busy ||
              !config?.enabled ||
              !config.svg_model ||
              !raster ||
              (running && !uncertain)
            }
            onClick={() => void start()}
          >
            {busy
              ? "处理中…"
              : uncertain
                ? "确认上次提交"
                : running
                  ? "任务进行中…"
                  : "发送图片并转换"}
          </button>
          <div className="autofigure-task-list" aria-live="polite">
            {tasks.map((task) => (
              <article key={task.id}>
                <strong>{taskLabels[task.status] || task.status}</strong>
                <small>{when(task.created)}</small>
                {task.message && <p>{task.message}</p>}
                {task.error && <p className="error-text">{task.error}</p>}
                <div className="button-row">
                  {task.output_asset_id && (
                    <button onClick={() => onSelect(task.output_asset_id)}>
                      打开 SVG 新版本
                    </button>
                  )}
                  {task.remote_id && task.status !== "completed" && (
                    <button disabled={busy} onClick={() => void refresh(task)}>
                      <RotateCw size={14} />
                      检查原任务进度
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </div>
      )}
      {editing && (
        <SVGEditor
          asset={asset}
          onClose={() => setEditing(false)}
          onSaved={(id) => {
            setEditing(false);
            onChange();
            onSelect(id);
          }}
        />
      )}
    </section>
  );
}

const editableSelector =
  "text,tspan,textPath,rect,circle,ellipse,line,polyline,polygon,path,image";
const textElements = ["text", "tspan", "textPath"];
function parseSVG(source: string) {
  if (/<!DOCTYPE|<!ENTITY/i.test(source))
    throw new Error("不支持带外部文档声明的 SVG");
  const doc = new DOMParser().parseFromString(source, "image/svg+xml");
  if (
    doc.querySelector("parsererror") ||
    doc.documentElement.localName !== "svg"
  )
    throw new Error("SVG 文档无法解析");
  return doc;
}
function editableElements(doc: XMLDocument) {
  return Array.from(doc.querySelectorAll(editableSelector)).filter(
    (element) => {
      if (element.closest("defs,clipPath,mask,pattern,marker,symbol"))
        return false;
      return (
        !textElements.includes(element.localName) ||
        !element.querySelector("tspan,textPath")
      );
    },
  );
}
type ElementEdit = {
  text: string;
  fill: string;
  stroke: string;
  fontSize: string;
  dx: string;
  dy: string;
};
function styleValue(key: string, value: string) {
  return key === "fontSize" && /^\d+(\.\d+)?$/.test(value)
    ? value + "px"
    : value;
}
function invalidStyle(key: string, value: string) {
  const property = key === "fontSize" ? "font-size" : key;
  return Boolean(
    value &&
    (/[<>;{}]/.test(value) ||
      (/url\s*\(/i.test(value) &&
        !/^url\(\s*(['"]?)#[A-Za-z_][\w.:-]*\1\s*\)$/.test(value)) ||
      !CSS.supports(property, styleValue(key, value))),
  );
}
function fieldIssue(fields: ElementEdit) {
  if (
    ![fields.dx, fields.dy].every(
      (value) => value.trim() && Number.isFinite(Number(value)),
    )
  )
    return "位移需要填写有效数字。";
  if (
    invalidStyle("fill", fields.fill) ||
    invalidStyle("stroke", fields.stroke)
  )
    return "请填写有效颜色，例如 #456b50、red 或 none。";
  if (invalidStyle("fontSize", fields.fontSize))
    return "请填写有效字号，例如 18 或 18px。";
  return "";
}

function SVGEditor({
  asset,
  onClose,
  onSaved,
}: {
  asset: Asset;
  onClose: () => void;
  onSaved: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const documentRef = useRef<XMLDocument | null>(null);
  const original = useRef("");
  const transforms = useRef<string[]>([]);
  const offsets = useRef<Record<number, { dx: string; dy: string }>>({});
  const saveAttempt = useRef<{ source: string; requestId: string } | null>(
    null,
  );
  const [source, setSource] = useState("");
  const [url, setUrl] = useState("");
  const [elements, setElements] = useState<Element[]>([]);
  const [selected, setSelected] = useState(0);
  const [fields, setFields] = useState<ElementEdit>({
    text: "",
    fill: "",
    stroke: "",
    fontSize: "",
    dx: "0",
    dy: "0",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [invalidInput, setInvalidInput] = useState(false);
  const [discard, setDiscard] = useState(false);
  function select(index: number, entries = elements) {
    setSelected(index);
    setInvalidInput(false);
    setError("");
    const element = entries[index];
    if (!element) return;
    const style = (element as SVGElement).style;
    setFields({
      text: element.textContent || "",
      fill: style?.fill || element.getAttribute("fill") || "",
      stroke: style?.stroke || element.getAttribute("stroke") || "",
      fontSize: style?.fontSize || element.getAttribute("font-size") || "",
      dx: offsets.current[index]?.dx || "0",
      dy: offsets.current[index]?.dy || "0",
    });
  }
  function reset(value = original.current) {
    const doc = parseSVG(value);
    const entries = editableElements(doc);
    documentRef.current = doc;
    transforms.current = entries.map(
      (element) => element.getAttribute("transform") || "",
    );
    offsets.current = {};
    saveAttempt.current = null;
    setElements(entries);
    setSource(new XMLSerializer().serializeToString(doc));
    setDirty(false);
    setInvalidInput(false);
    setError("");
    select(0, entries);
  }
  useEffect(() => {
    dialog.current?.showModal();
    let alive = true;
    api<{ svg: string }>("/autofigure/assets/" + asset.id + "/svg")
      .then((result) => {
        if (alive) {
          original.current = result.svg;
          reset(result.svg);
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [asset.id]);
  useEffect(() => {
    if (!source) return;
    const next = URL.createObjectURL(
      new Blob([source], { type: "image/svg+xml" }),
    );
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [source]);
  function change(key: keyof ElementEdit, value: string) {
    const entry = elements[selected];
    if (!entry || !documentRef.current) return;
    const next = { ...fields, [key]: value };
    setFields(next);
    if (["dx", "dy"].includes(key)) {
      if (
        !next.dx.trim() ||
        !next.dy.trim() ||
        !Number.isFinite(Number(next.dx)) ||
        !Number.isFinite(Number(next.dy))
      ) {
        setInvalidInput(true);
        setError("位移需要填写有效数字。");
        return;
      }
      offsets.current[selected] = { dx: next.dx, dy: next.dy };
      entry.setAttribute(
        "transform",
        `translate(${Number(next.dx)} ${Number(next.dy)}) ${transforms.current[selected]}`.trim(),
      );
    } else if (key === "text") {
      entry.textContent = value;
    } else {
      const property = key === "fontSize" ? "font-size" : key;
      // Restrict user-entered paint values to colors or local paint references.
      const cssValue = styleValue(key, value);
      if (invalidStyle(key, value)) {
        setInvalidInput(true);
        setError(
          key === "fontSize"
            ? "请填写有效字号，例如 18 或 18px。"
            : "请填写有效颜色，例如 #456b50、red 或 none。",
        );
        return;
      }
      const style = (entry as SVGElement).style;
      if (value) {
        entry.setAttribute(property, value);
        // Inline declarations also override styles inherited from CSS classes.
        style?.setProperty(property, cssValue);
      } else {
        entry.removeAttribute(property);
        style?.removeProperty(property);
      }
    }
    setSource(new XMLSerializer().serializeToString(documentRef.current));
    setDirty(true);
    const issue = fieldIssue(next);
    setInvalidInput(Boolean(issue));
    setError(issue);
  }
  async function save() {
    setBusy(true);
    setError("");
    if (!saveAttempt.current || saveAttempt.current.source !== source)
      saveAttempt.current = { source, requestId: crypto.randomUUID() };
    try {
      const result = await api<{ asset_id: string }>(
        "/autofigure/assets/" + asset.id + "/svg",
        json("PUT", { request_id: saveAttempt.current.requestId, svg: source }),
      );
      onSaved(result.asset_id);
    } catch (e) {
      setError(
        (e as Error).message + "；可再次保存，未修改的内容将复用同一请求。",
      );
    } finally {
      setBusy(false);
    }
  }
  function close() {
    if (busy) return;
    if (dirty) setDiscard(true);
    else onClose();
  }
  const element = elements[selected];
  const textElement = element && textElements.includes(element.localName);
  return (
    <dialog
      ref={dialog}
      className="modal autofigure-editor"
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
    >
      <header className="modal-header">
        <div>
          <span className="eyebrow">SVG EDITOR</span>
          <h2>编辑示意图</h2>
        </div>
        <button
          className="icon-button"
          disabled={busy}
          aria-label="关闭 SVG 编辑器"
          onClick={close}
        >
          <X size={20} />
        </button>
      </header>
      <p className="subtle">
        修改文字、颜色和位置，保存后会创建新版本。位图图标可移动，内部像素不可逐项编辑。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {discard && (
        <div className="notice" role="alert">
          <p>当前修改尚未保存。</p>
          <div className="button-row">
            <button onClick={() => setDiscard(false)}>继续编辑</button>
            <button onClick={onClose}>放弃修改并关闭</button>
          </div>
        </div>
      )}
      {!source ? (
        <p className="subtle">{error ? "未能加载 SVG。" : "正在加载 SVG…"}</p>
      ) : (
        <>
          <div className="autofigure-editor-grid">
            <div className="autofigure-canvas">
              {url && (
                <img
                  src={url}
                  alt="SVG 编辑预览"
                  onError={() =>
                    setError(
                      "SVG 无法显示预览，请检查图形内容。当前修改尚未保存。",
                    )
                  }
                />
              )}
            </div>
            <fieldset className="autofigure-properties" disabled={busy}>
              <label>
                选择图形或文字
                <select
                  aria-label="选择 SVG 元素"
                  value={selected}
                  onChange={(e) => select(Number(e.target.value))}
                  disabled={!elements.length}
                >
                  {elements.map((entry, index) => (
                    <option key={index} value={index}>
                      {index + 1}. {entry.localName}
                      {textElements.includes(entry.localName)
                        ? " · " + (entry.textContent || "空文字").slice(0, 70)
                        : entry.id
                          ? " · " + entry.id
                          : ""}
                    </option>
                  ))}
                </select>
              </label>
              {!elements.length && (
                <p className="subtle">此 SVG 中没有可逐项编辑的元素。</p>
              )}
              {element && (
                <>
                  {textElement && (
                    <label>
                      文字内容
                      <textarea
                        rows={3}
                        value={fields.text}
                        onChange={(e) => change("text", e.target.value)}
                      />
                    </label>
                  )}
                  {element.localName !== "image" && (
                    <>
                      <label>
                        填充颜色
                        <input
                          value={fields.fill}
                          placeholder="继承原图样式，如 #456b50 或 none"
                          onChange={(e) => change("fill", e.target.value)}
                        />
                      </label>
                      <label>
                        描边颜色
                        <input
                          value={fields.stroke}
                          placeholder="继承原图样式，如 #333333"
                          onChange={(e) => change("stroke", e.target.value)}
                        />
                      </label>
                    </>
                  )}
                  {textElement && (
                    <label>
                      字号
                      <input
                        value={fields.fontSize}
                        placeholder="如 18px"
                        onChange={(e) => change("fontSize", e.target.value)}
                      />
                    </label>
                  )}
                  <div className="form-grid">
                    <label>
                      水平位移
                      <input
                        type="number"
                        step="any"
                        value={fields.dx}
                        onChange={(e) => change("dx", e.target.value)}
                      />
                    </label>
                    <label>
                      垂直位移
                      <input
                        type="number"
                        step="any"
                        value={fields.dy}
                        onChange={(e) => change("dy", e.target.value)}
                      />
                    </label>
                  </div>
                  <p className="subtle">
                    位移以 SVG
                    坐标为单位，相对打开时的位置。颜色和字号支持有效的 CSS
                    值，留空继承原图样式。
                  </p>
                </>
              )}
            </fieldset>
          </div>
          <div className="modal-actions">
            <button disabled={busy || !dirty} onClick={() => reset()}>
              <RotateCw size={15} />
              重置修改
            </button>
            <a
              className="button"
              href={url}
              download={asset.name.replace(/\.svg$/i, "") + "-edited.svg"}
            >
              <Download size={15} />
              下载 SVG
            </a>
            {dirty ? (
              <button disabled title="先保存为新版本，再下载 PNG">
                <Download size={15} />
                预览 PNG
              </button>
            ) : (
              <a
                className="button"
                href={"/api/autofigure/assets/" + asset.id + "/png"}
                title="下载已保存版本的 PNG 预览，最长边 1600 像素"
              >
                <Download size={15} />
                预览 PNG
              </a>
            )}
            <button
              className="primary"
              disabled={busy || !dirty || invalidInput}
              onClick={() => void save()}
            >
              <Save size={15} />
              {busy ? "保存中…" : "保存为新版本"}
            </button>
          </div>
        </>
      )}
    </dialog>
  );
}
