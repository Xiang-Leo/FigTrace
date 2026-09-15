import { useEffect, useRef, useState } from "react";
import {
  Upload,
  FolderPlus,
  Folder,
  ArrowUp,
  Pause,
  Play,
  X,
  Check,
  RotateCw,
} from "lucide-react";
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { api, json, size, Overview, Root } from "./api";

type QueueItem = {
  key: string;
  file: File;
  path: string;
  status: string;
  offset: number;
  uploadId?: string;
  error?: string;
  digest?: string;
  duplicate?: boolean;
};
const chunkSize = 4 * 1024 * 1024;
const accepted = [
  "jpg",
  "jpeg",
  "png",
  "tif",
  "tiff",
  "pdf",
  "ai",
  "eps",
  "psd",
  "webp",
  "bmp",
  "gif",
  "svg",
];
const storageKey = "figtrace-upload-sessions-v1";
function stored(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(storageKey) || "{}");
  } catch {
    return {};
  }
}
function saveSession(key: string, id: string) {
  try {
    const values = stored();
    values[key] = id;
    const keys = Object.keys(values);
    for (const old of keys.slice(0, Math.max(0, keys.length - 10000)))
      delete values[old];
    localStorage.setItem(storageKey, JSON.stringify(values));
  } catch {
    /* Uploads still work if browser storage is unavailable. */
  }
}
function forgetSession(key: string) {
  try {
    const values = stored();
    delete values[key];
    localStorage.setItem(storageKey, JSON.stringify(values));
  } catch {}
}

async function walkEntry(
  entry: any,
  prefix = "",
): Promise<{ file: File; path: string }[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      entry.file(resolve, reject),
    );
    return [{ file, path: prefix + file.name }];
  }
  const reader = entry.createReader();
  let result: { file: File; path: string }[] = [];
  for (;;) {
    const batch: any[] = await new Promise((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (!batch.length) break;
    for (const child of batch)
      result.push(...(await walkEntry(child, prefix + entry.name + "/")));
  }
  return result;
}

export function ImportDialog({
  open,
  onClose,
  onRefresh,
  overview,
  initialMode = "upload",
  relocate,
}: {
  open: boolean;
  onClose: () => void;
  onRefresh: () => void;
  overview: Overview | null;
  initialMode?: string;
  relocate: Root | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    fileInput = useRef<HTMLInputElement>(null),
    folderInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState(initialMode),
    [project, setProject] = useState(""),
    [tags, setTags] = useState(""),
    [keep, setKeep] = useState(true),
    [error, setError] = useState(""),
    [info, setInfo] = useState("");
  const [queue, setQueue] = useState<QueueItem[]>([]),
    queueRef = useRef<QueueItem[]>([]),
    paused = useRef(true),
    running = useRef(false),
    [active, setActive] = useState(false),
    [slice, setSlice] = useState(0);
  const [directory, setDirectory] = useState(""),
    [listing, setListing] = useState<{
      path: string;
      parent: string | null;
      directories: { name: string; path: string }[];
    }>({ path: "", parent: null, directories: [] }),
    [remote, setRemote] = useState(false),
    [linking, setLinking] = useState(false);
  const batch = useRef({ project: "", tags: [] as string[], keep: true });
  const renderQueue = () => setQueue([...queueRef.current]);
  useEffect(() => {
    if (open) {
      dialog.current?.showModal();
      setMode(initialMode);
      setError("");
      setInfo("");
      if (relocate) {
        setDirectory(relocate.path);
        setRemote(Boolean(relocate.allow_remote));
      }
    } else dialog.current?.close();
  }, [open, initialMode, relocate]);
  useEffect(() => {
    if (open && mode === "directory") browse(relocate?.path || "");
  }, [open, mode]);
  async function browse(path: string) {
    try {
      const data = await api(
        "/directories" + (path ? "?path=" + encodeURIComponent(path) : ""),
      );
      setListing(data);
      setDirectory(data.path);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function addFiles(files: { file: File; path: string }[]) {
    let excluded = 0;
    const existing = new Set(
      queueRef.current.map(
        (item) =>
          item.path + "|" + item.file.size + "|" + item.file.lastModified,
      ),
    );
    for (const { file, path } of files) {
      if (!accepted.includes(file.name.split(".").pop()?.toLowerCase() || "")) {
        excluded++;
        continue;
      }
      const identity = path + "|" + file.size + "|" + file.lastModified;
      if (existing.has(identity)) continue;
      existing.add(identity);
      queueRef.current.push({
        key: crypto.randomUUID(),
        file,
        path,
        status: "待上传",
        offset: 0,
      });
    }
    setInfo(
      excluded
        ? `已略过 ${excluded} 个暂不支持上传的文件；脚本和数据文件可在图片详情中关联。`
        : "已加入清单，开始前可统一设置项目和标签。",
    );
    renderQueue();
  }
  async function dropped(event: React.DragEvent) {
    event.preventDefault();
    setError("");
    try {
      const entries = Array.from(event.dataTransfer.items)
        .map((item) => (item as any).webkitGetAsEntry?.())
        .filter(Boolean);
      const files = Array.from(event.dataTransfer.files);
      if (entries.length) {
        for (const entry of entries) addFiles(await walkEntry(entry));
      } else addFiles(files.map((file) => ({ file, path: file.name })));
    } catch (e) {
      setError(
        "无法读取拖入的目录，请使用“选择文件夹”：" + (e as Error).message,
      );
    }
  }
  async function upload(item: QueueItem) {
    let sessionKey = "";
    try {
      if (!item.digest) {
        item.status = "校验文件";
        renderQueue();
        const hash = sha256.create();
        for (let offset = 0; offset < item.file.size; offset += chunkSize) {
          if (paused.current) {
            item.status = "已暂停";
            return;
          }
          hash.update(
            new Uint8Array(
              await item.file.slice(offset, offset + chunkSize).arrayBuffer(),
            ),
          );
        }
        item.digest = bytesToHex(hash.digest());
      }
      sessionKey = [
        item.digest,
        item.path,
        item.file.size,
        batch.current.project,
        batch.current.tags.join(","),
        batch.current.keep,
      ].join("|");
      let saved: string | undefined = item.uploadId || stored()[sessionKey];
      let server: any = null;
      if (saved) {
        try {
          server = await api("/uploads/" + saved);
        } catch {
          saved = undefined;
          forgetSession(sessionKey);
        }
      }
      if (!server) {
        server = await api(
          "/uploads",
          json("POST", {
            relative_path: batch.current.keep ? item.path : item.file.name,
            size: item.file.size,
            sha256: item.digest,
            project: batch.current.project,
            tags: batch.current.tags,
          }),
        );
        saved = server.id;
      }
      item.uploadId = server.id;
      saveSession(sessionKey, server.id);
      item.offset = server.offset;
      if (server.sha256 !== item.digest || server.size !== item.file.size)
        throw new Error("重选文件与原上传内容不一致");
      if (!server.asset_id) {
        while (item.offset < item.file.size) {
          if (paused.current) {
            item.status = "已暂停";
            return;
          }
          item.status = "上传中";
          renderQueue();
          const part = item.file.slice(item.offset, item.offset + chunkSize);
          let result: any;
          try {
            result = await api("/uploads/" + saved + "?offset=" + item.offset, {
              method: "PUT",
              body: part,
            });
          } catch (e) {
            const latest = await api("/uploads/" + saved);
            if (latest.offset > item.offset) {
              item.offset = latest.offset;
              continue;
            }
            throw e;
          }
          item.offset = result.offset;
        }
        item.status = "服务端校验";
        renderQueue();
        const result = await api(
          "/uploads/" + saved + "/complete",
          json("POST"),
        );
        item.duplicate = result.duplicate;
      }
      item.status = "已入库";
      item.offset = item.file.size;
      forgetSession(sessionKey);
    } catch (e) {
      item.status = "失败";
      item.error = (e as Error).message;
    } finally {
      renderQueue();
    }
  }
  async function start() {
    if (running.current) return;
    setError("");
    if (
      !queueRef.current.some((item) =>
        ["待上传", "已暂停", "失败"].includes(item.status),
      )
    )
      return;
    if (!queueRef.current.some((item) => item.uploadId))
      batch.current = {
        project,
        tags: tags
          .split(/[,，]/)
          .map((v) => v.trim())
          .filter(Boolean),
        keep,
      };
    paused.current = false;
    running.current = true;
    setActive(true);
    queueRef.current.forEach((item) => {
      if (["失败", "已暂停"].includes(item.status)) {
        item.status = "待上传";
        item.error = "";
      }
    });
    renderQueue();
    const pending = queueRef.current.filter((item) => item.status === "待上传");
    let next = 0;
    async function worker() {
      while (!paused.current && next < pending.length) {
        const item = pending[next++];
        await upload(item);
      }
    }
    await Promise.all([worker(), worker(), worker()]);
    running.current = false;
    setActive(false);
    onRefresh();
  }
  async function cancel(item: QueueItem) {
    if (active) return;
    try {
      if (item.uploadId && item.status !== "已入库")
        await api("/uploads/" + item.uploadId, json("DELETE"));
      queueRef.current = queueRef.current.filter((q) => q.key !== item.key);
      renderQueue();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const total = queue.reduce((sum, item) => sum + item.file.size, 0),
    sent = queue.reduce((sum, item) => sum + item.offset, 0),
    finished = queue.filter((item) => item.status === "已入库").length;
  const locked = active || queue.some((item) => item.uploadId);
  async function link() {
    setLinking(true);
    setError("");
    try {
      await api(
        relocate ? "/roots/" + relocate.id : "/roots",
        json(relocate ? "PUT" : "POST", {
          path: directory,
          project,
          allow_remote: remote,
        }),
      );
      setInfo("目录已登记，后台正在扫描。可以关闭此窗口继续浏览。");
      onRefresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLinking(false);
    }
  }
  return (
    <dialog ref={dialog} className="modal import-modal" onCancel={onClose}>
      <header className="modal-header">
        <div>
          <span className="eyebrow">YOUR FIGURES, CONNECTED</span>
          <h2>{relocate ? "重新定位素材目录" : "添加素材"}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="关闭添加素材"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </header>
      <div className="tabs">
        <button
          aria-pressed={mode === "upload"}
          onClick={() => setMode("upload")}
          disabled={!!relocate}
        >
          <Upload size={16} />
          批量上传
        </button>
        <button
          aria-pressed={mode === "directory"}
          onClick={() => setMode("directory")}
        >
          <FolderPlus size={16} />
          关联已有目录
        </button>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {info && (
        <div className="notice" role="status">
          {info}
        </div>
      )}
      {mode === "upload" ? (
        <>
          <div
            className="drop-zone"
            onDragOver={(e) => e.preventDefault()}
            onDrop={dropped}
          >
            <Upload size={28} />
            <h3>把图片和文件夹拖到这里</h3>
            <p>JPG、PNG、TIFF、PDF、AI、EPS、PSD 等</p>
            <div className="button-row">
              <button onClick={() => fileInput.current?.click()}>
                选择文件
              </button>
              <button onClick={() => folderInput.current?.click()}>
                选择文件夹
              </button>
            </div>
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept={accepted.map((v) => "." + v).join(",")}
            onChange={(e) => {
              addFiles(
                Array.from(e.target.files || []).map((file) => ({
                  file,
                  path: file.name,
                })),
              );
              e.target.value = "";
            }}
          />
          <input
            ref={folderInput}
            type="file"
            multiple
            hidden
            {...({ webkitdirectory: "" } as any)}
            onChange={(e) => {
              addFiles(
                Array.from(e.target.files || []).map((file) => ({
                  file,
                  path: file.webkitRelativePath || file.name,
                })),
              );
              e.target.value = "";
            }}
          />
          <p className="subtle">
            上传到{overview?.local_mode ? "本机" : "服务器"}的 FigTrace
            素材区，将保存文件副本。已有本机图库可使用“关联已有目录”。
          </p>
          <div className="form-grid">
            <label>
              归入项目
              <input
                list="project-options"
                value={project}
                onChange={(e) => setProject(e.target.value)}
                placeholder="暂不归类"
                disabled={locked}
              />
            </label>
            <label>
              统一标签
              <input
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="投稿, 待检查"
                disabled={locked}
              />
            </label>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={keep}
              onChange={(e) => setKeep(e.target.checked)}
              disabled={locked}
            />
            保留文件夹层级信息
          </label>
          {queue.length > 0 && (
            <>
              <div className="queue-summary">
                <strong>
                  {queue.length} 个文件 · {size(total)}
                </strong>
                <span>
                  已入库 {finished} / {queue.length}
                </span>
              </div>
              <progress value={sent} max={total || 1} aria-label="总上传进度" />
              <div className="upload-list">
                {queue.slice(slice * 20, slice * 20 + 20).map((item) => (
                  <div className="upload-row" key={item.key}>
                    <div>
                      <strong>{item.path}</strong>
                      <span>
                        {size(item.file.size)} · {item.status}
                        {item.duplicate ? " · 复用已有内容" : ""}
                        {item.status === "上传中"
                          ? " " +
                            Math.floor((item.offset / item.file.size) * 100) +
                            "%"
                          : ""}
                      </span>
                      {item.error && (
                        <span className="error-text">{item.error}</span>
                      )}
                    </div>
                    <button
                      className="icon-button"
                      disabled={active}
                      aria-label={"移出清单 " + item.file.name}
                      onClick={() => cancel(item)}
                    >
                      {item.status === "已入库" ? (
                        <Check size={16} />
                      ) : (
                        <X size={16} />
                      )}
                    </button>
                  </div>
                ))}
              </div>
              {queue.length > 20 && (
                <div className="pager">
                  <button
                    disabled={slice === 0}
                    onClick={() => setSlice((s) => s - 1)}
                  >
                    上一页
                  </button>
                  <span>
                    {slice + 1} / {Math.ceil(queue.length / 20)}
                  </span>
                  <button
                    disabled={(slice + 1) * 20 >= queue.length}
                    onClick={() => setSlice((s) => s + 1)}
                  >
                    下一页
                  </button>
                </div>
              )}
            </>
          )}
          <p className="subtle">
            中断后可重选原文件续传。关闭网页会停止尚未完成的传输；已入库素材继续生成预览。预览就绪后可在图库中选择素材进行
            AI 分类。
          </p>
          <div className="modal-actions">
            {!active ? (
              <button
                className="primary"
                disabled={!queue.some((item) => item.status !== "已入库")}
                onClick={start}
              >
                <Play size={16} />
                {queue.some((item) => item.uploadId)
                  ? "继续 / 重试失败项"
                  : "开始导入"}
              </button>
            ) : (
              <button
                onClick={() => {
                  paused.current = true;
                  setInfo("当前分片完成后暂停。");
                }}
              >
                <Pause size={16} />
                暂停上传
              </button>
            )}
            {finished > 0 && !active && (
              <button
                onClick={() => {
                  queueRef.current = queueRef.current.filter(
                    (item) => item.status !== "已入库",
                  );
                  setSlice(0);
                  renderQueue();
                }}
              >
                清除已完成记录
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="notice">
            <FolderPlus size={18} />
            <span>
              选择{overview?.local_mode ? "本机后端" : "服务器"}
              能访问的目录。原文件保持原位，后台每小时检查变化。
            </span>
          </div>
          <label>
            目录完整路径
            <div className="inline-field">
              <input
                value={directory}
                onChange={(e) => setDirectory(e.target.value)}
                placeholder={
                  overview?.local_mode ? "粘贴本机目录路径" : "/srv/figures"
                }
              />
              <button onClick={() => browse(directory)}>浏览</button>
            </div>
          </label>
          <div className="directory-list">
            {listing.parent && (
              <button onClick={() => browse(listing.parent!)}>
                <ArrowUp size={16} />
                上一级
              </button>
            )}
            {listing.directories.map((child) => (
              <button key={child.path} onClick={() => browse(child.path)}>
                <Folder size={17} />
                {child.name}
              </button>
            ))}
            {listing.path && !listing.directories.length && (
              <p className="subtle">
                没有可浏览的子目录，可以直接关联当前目录。
              </p>
            )}
          </div>
          {!relocate && (
            <label>
              归入项目
              <input
                list="project-options"
                value={project}
                onChange={(e) => setProject(e.target.value)}
                placeholder="暂不归类"
              />
            </label>
          )}
          <label className="check">
            <input
              type="checkbox"
              checked={remote}
              onChange={(e) => setRemote(e.target.checked)}
            />
            允许读取网盘在线文件（可能触发下载）
          </label>
          <p className="subtle">
            默认跳过能识别的在线占位文件；不同网盘的标记存在差异，建议先选已下载的目录。默认递归包含子文件夹。
          </p>
          <div className="modal-actions">
            <button
              className="primary"
              disabled={!directory || linking}
              onClick={link}
            >
              {linking ? (
                <RotateCw size={16} className="spin" />
              ) : (
                <FolderPlus size={16} />
              )}{" "}
              {relocate ? "更新目录并扫描" : "关联目录并扫描"}
            </button>
          </div>
        </>
      )}
      <datalist id="project-options">
        {overview?.projects.map((p) => (
          <option key={p} value={p} />
        ))}
      </datalist>
    </dialog>
  );
}
