import { useEffect, useRef, useState } from "react";
import {
  Download,
  ExternalLink,
  FileImage,
  Link2,
  Plus,
  RotateCw,
  Save,
  X,
  ChevronLeft,
  ChevronRight,
  Columns2,
} from "lucide-react";
import { api, json, size, when, statusName, stageNames, Asset } from "./api";

export function Preview({
  asset,
  page = 0,
  large = false,
}: {
  asset: Asset;
  page?: number;
  large?: boolean;
}) {
  const [broken, setBroken] = useState(false);
  useEffect(
    () => setBroken(false),
    [asset.id, asset.preview, asset.mtime, page],
  );
  return (
    <div className={"preview " + (large ? "preview-large" : "")}>
      {asset.preview === "ready" && !broken ? (
        <img
          src={
            "/api/assets/" +
            asset.id +
            "/preview?page=" +
            page +
            "&v=" +
            asset.mtime
          }
          alt={asset.title || asset.name}
          loading="lazy"
          onError={() => setBroken(true)}
        />
      ) : (
        <div className="preview-placeholder">
          <FileImage size={large ? 38 : 28} />
          <strong>{asset.format.toUpperCase()}</strong>
          <span>
            {broken
              ? "此页预览不可用"
              : statusName[asset.preview] || asset.preview}
          </span>
        </div>
      )}
    </div>
  );
}

export function Detail({
  id,
  onClose,
  onChange,
  onSelect,
  onAI,
}: {
  id: string;
  onClose: () => void;
  onChange: () => void;
  onSelect: (id: string) => void;
  onAI: () => void;
}) {
  const [asset, setAsset] = useState<Asset | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [saving, setSaving] = useState(false),
    [tab, setTab] = useState("info"),
    [page, setPage] = useState(0),
    [link, setLink] = useState(""),
    [enlarged, setEnlarged] = useState(false),
    [comparison, setComparison] = useState(""),
    [formKey, setFormKey] = useState(0);
  const big = useRef<HTMLDialogElement>(null);
  async function load() {
    try {
      setAsset(await api("/assets/" + id));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    let alive = true;
    setAsset(null);
    setPage(0);
    setNotice("");
    setComparison("");
    api<Asset>("/assets/" + id)
      .then((value) => {
        if (alive) setAsset(value);
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [id]);
  useEffect(() => {
    if (!asset || !["queued", "running"].includes(asset.preview)) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [id, asset?.preview]);
  useEffect(() => {
    if (enlarged) big.current?.showModal();
    else big.current?.close();
  }, [enlarged]);
  async function act(path: string, body?: unknown) {
    setError("");
    try {
      await api(path, json("POST", body));
      await load();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      await api(
        "/assets/" + id,
        json("PUT", {
          title: data.get("title"),
          project: data.get("project"),
          stage: data.get("stage"),
          tags: String(data.get("tags"))
            .split(/[,，]/)
            .map((s) => s.trim())
            .filter(Boolean),
          notes: data.get("notes"),
          version_note: data.get("version_note"),
        }),
      );
      setNotice("已保存");
      await load();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  async function addLink(event: React.FormEvent) {
    event.preventDefault();
    await act("/assets/" + id + "/links", { path: link });
    setLink("");
  }
  const compareAsset = asset?.versions_list.find((v) => v.id === comparison);
  return (
    <aside className="detail-panel" aria-label="图片详情">
      <div className="detail-heading">
        <span className="eyebrow">FIGURE DETAILS</span>
        <button className="icon-button" aria-label="关闭详情" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {!asset ? (
        <p className="subtle">正在读取详情…</p>
      ) : (
        <>
          <h2>{asset.title}</h2>
          <button
            className="preview-button"
            onClick={() => setEnlarged(true)}
            aria-label="查看大图"
          >
            <Preview asset={asset} page={page} />
          </button>
          {asset.pages > 1 && (
            <div className="pager">
              <button
                aria-label="上一页图片"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <span>
                第 {page + 1} / {asset.pages} 页
              </span>
              <button
                aria-label="下一页图片"
                disabled={page + 1 >= asset.pages}
                onClick={() => setPage((p) => p + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          )}
          <div className="file-facts">
            <span>
              {asset.format.toUpperCase()} · {size(asset.size)}
            </span>
            {asset.width && (
              <span>
                {asset.width} × {asset.height}{" "}
                {asset.format === "pdf" || asset.format === "ai" ? "pt" : "px"}
              </span>
            )}
          </div>
          <button onClick={onAI}>✦ AI 分类与建议</button>
          {asset.error && <div className="notice">{asset.error}</div>}
          {asset.mode && <p className="subtle">{asset.mode}</p>}
          <div className="button-row">
            <a className="button" href={"/api/assets/" + id + "/download"}>
              <Download size={15} />
              下载原文件
            </a>
            <button
              onClick={() => act("/assets/" + id + "/retry")}
              title="重新生成预览"
            >
              <RotateCw size={15} />
              重试预览
            </button>
          </div>
          <div className="tabs detail-tabs">
            <button
              aria-pressed={tab === "info"}
              onClick={() => setTab("info")}
            >
              信息
            </button>
            <button
              aria-pressed={tab === "versions"}
              onClick={() => setTab("versions")}
            >
              版本 {asset.versions_list.length}
            </button>
            <button
              aria-pressed={tab === "sources"}
              onClick={() => setTab("sources")}
            >
              关联文件
            </button>
          </div>
          {tab === "info" && (
            <form
              key={id + ":" + formKey}
              onSubmit={save}
              className="detail-form"
            >
              <label>
                名称
                <input
                  name="title"
                  defaultValue={asset.title}
                  required
                  maxLength={300}
                />
              </label>
              <label>
                项目
                <input
                  list="project-options"
                  name="project"
                  defaultValue={asset.project}
                  placeholder="暂不归类"
                  maxLength={200}
                />
              </label>
              <label>
                标签
                <input
                  name="tags"
                  defaultValue={asset.tags.join(", ")}
                  placeholder="用逗号分隔"
                />
              </label>
              <label>
                Figure 状态
                <select name="stage" defaultValue={asset.stage}>
                  {Object.entries(stageNames).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                本版本说明
                <input
                  name="version_note"
                  defaultValue={asset.version_note}
                  placeholder="例如：投稿版，修改配色"
                  maxLength={1000}
                />
              </label>
              <label>
                Figure 备注
                <textarea
                  name="notes"
                  defaultValue={asset.notes}
                  rows={3}
                  maxLength={10000}
                />
              </label>
              <div className="button-row">
                <button type="submit" className="primary" disabled={saving}>
                  <Save size={15} />
                  {saving ? "保存中…" : "保存信息"}
                </button>
                <span className="success" role="status">
                  {notice}
                </span>
              </div>
            </form>
          )}
          {tab === "versions" && (
            <div className="versions-list">
              <p className="subtle">
                选中图库中的多张 Figure 后可以合并版本。当前采用版作为图库封面。
              </p>
              {asset.versions_list.map((v, i) => (
                <div
                  key={v.id}
                  className={"version-item " + (id === v.id ? "selected" : "")}
                >
                  <button
                    className="version-select"
                    onClick={() => onSelect(v.id)}
                  >
                    <strong>{v.name}</strong>
                    <span>
                      {v.version_note || "未填写版本说明"} · {when(v.created)}
                    </span>
                  </button>
                  {asset.preferred === v.id ? (
                    <span className="badge">当前采用</span>
                  ) : (
                    <button
                      onClick={() => act("/assets/" + v.id + "/preferred")}
                    >
                      采用此版
                    </button>
                  )}
                </div>
              ))}
              {asset.versions_list.length > 1 && (
                <>
                  <button
                    onClick={() => {
                      setComparison(
                        asset.versions_list.find((v) => v.id !== id)!.id,
                      );
                      setEnlarged(true);
                    }}
                  >
                    <Columns2 size={16} />
                    并排比较
                  </button>
                  <button onClick={() => act("/assets/" + id + "/ungroup")}>
                    将本版本独立为 Figure
                  </button>
                </>
              )}
            </div>
          )}
          {tab === "sources" && (
            <>
              <p className="subtle">
                关联可编辑文件、绘图脚本或数据。请输入后端可访问的文件完整路径。
              </p>
              {asset.links.map((source) => (
                <div className="source-item" key={source.id}>
                  <a href={"/api/links/" + source.id + "/download"}>
                    <Link2 size={15} />
                    <span>
                      {source.label}
                      <small>{source.path}</small>
                    </span>
                  </a>
                  <button
                    className="icon-button"
                    aria-label={"解除关联 " + source.label}
                    onClick={async () => {
                      try {
                        await api("/links/" + source.id, json("DELETE"));
                        await load();
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
              <form className="inline-field" onSubmit={addLink}>
                <input
                  value={link}
                  onChange={(e) => setLink(e.target.value)}
                  placeholder="源文件完整路径"
                  aria-label="源文件完整路径"
                  required
                />
                <button aria-label="添加源文件关联">
                  <Plus size={18} />
                </button>
              </form>
            </>
          )}
          <div className="location">
            <span className="eyebrow">文件位置</span>
            <p>{asset.path}</p>
            {asset.root_id === "managed" && (
              <>
                <span className="eyebrow">上传来源</span>
                <p>{asset.source_path}</p>
              </>
            )}
            <button
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(asset.path);
                  setNotice("路径已复制");
                } catch {
                  setError("无法自动复制，请选中上方路径手动复制。");
                }
              }}
            >
              <ExternalLink size={14} />
              复制路径
            </button>
          </div>
          <dialog
            className="modal image-modal"
            ref={big}
            onCancel={() => setEnlarged(false)}
            onClose={() => setEnlarged(false)}
          >
            <header className="modal-header">
              <h2>{asset.title}</h2>
              <button
                className="icon-button"
                aria-label="关闭大图"
                onClick={() => setEnlarged(false)}
              >
                <X size={20} />
              </button>
            </header>
            <div className={"compare-grid " + (compareAsset ? "two" : "")}>
              <section>
                <p>{asset.name}</p>
                <Preview asset={asset} large page={page} />
              </section>
              {compareAsset && (
                <section>
                  <select
                    aria-label="比较版本"
                    value={comparison}
                    onChange={(e) => setComparison(e.target.value)}
                  >
                    {asset.versions_list
                      .filter((v) => v.id !== id)
                      .map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.name}
                        </option>
                      ))}
                  </select>
                  <Preview asset={compareAsset} large />
                </section>
              )}
            </div>
          </dialog>
          {!!asset.locations?.length && (
            <details className="location-history">
              <summary>文件移动记录（最近 50 条）</summary>
              {asset.locations.map((location, i) => (
                <p key={i}>
                  <small>{when(location.created)}</small>
                  <br />
                  {location.old_path}
                  <br />→ {location.new_path}
                </p>
              ))}
            </details>
          )}
        </>
      )}
    </aside>
  );
}
