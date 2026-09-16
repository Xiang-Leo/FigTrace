import { useEffect, useRef, useState } from "react";
import { Archive, Download, RotateCw, Save, X } from "lucide-react";
import {
  api,
  json,
  size,
  when,
  Overview,
  BackupConfig,
  Root,
  statusName,
} from "./api";

export function Settings({
  open,
  onClose,
  overview,
  onRefresh,
  onRelocate,
}: {
  open: boolean;
  onClose: () => void;
  overview: Overview | null;
  onRefresh: () => void;
  onRelocate: (root: Root) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [config, setConfig] = useState<BackupConfig | null>(null),
    [backups, setBackups] = useState<
      { name: string; size: number; created: number }[]
    >([]),
    [jobs, setJobs] = useState<any[]>([]),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [restoreName, setRestoreName] = useState(""),
    [confirmation, setConfirmation] = useState("");
  async function refresh() {
    try {
      const [b, j] = await Promise.all([api("/backups"), api("/jobs")]);
      setBackups(b);
      setJobs(j);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    if (open) {
      ref.current?.showModal();
      setConfig(overview?.backup || null);
      setError("");
      setNotice("");
      refresh();
      const timer = setInterval(refresh, 3000);
      return () => clearInterval(timer);
    }
    ref.current?.close();
  }, [open]);
  async function action(callback: () => Promise<any>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await callback();
      setNotice(message);
      await refresh();
      onRefresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog ref={ref} className="modal settings-modal" onCancel={onClose}>
      <header className="modal-header">
        <div>
          <span className="eyebrow">LIBRARY SETTINGS</span>
          <h2>目录、任务与备份</h2>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="关闭设置">
          <X size={20} />
        </button>
      </header>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      <section className="settings-section">
        <h3>素材目录</h3>
        {overview?.roots.map((root) => (
          <div className="root-row" key={root.id}>
            <div>
              <strong>{root.name}</strong>
              <p>{root.path}</p>
              <small>
                {root.kind === "managed"
                  ? "由 FigTrace 管理上传副本"
                  : (root.status === "ready"
                      ? "目录可用"
                      : statusName[root.status] || root.status) +
                    " · 扫描于 " +
                    when(root.last_scan)}
              </small>
            </div>
            {root.kind === "linked" && (
              <div className="button-row">
                <button
                  disabled={busy}
                  onClick={() =>
                    action(
                      () => api("/roots/" + root.id + "/scan", json("POST")),
                      "扫描已加入后台队列",
                    )
                  }
                >
                  <RotateCw size={14} />
                  扫描
                </button>
                <button onClick={() => onRelocate(root)}>重新定位</button>
              </div>
            )}
          </div>
        ))}
      </section>
      <section className="settings-section">
        <h3>
          <Archive size={18} />
          图库信息备份
        </h3>
        <p className="subtle">
          包含项目、标签、版本和文件关联，不包含原图片、上传副本或源文件。请另外备份原文件。写入网盘目录不代表已同步到云端。
        </p>
        {config && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action(
                () => api("/settings/backup", json("PUT", config)),
                "备份计划已保存",
              );
            }}
          >
            <label className="check">
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={(e) =>
                  setConfig({ ...config, enabled: e.target.checked })
                }
              />
              启用定期自动备份
            </label>
            <div className="form-grid">
              <label>
                间隔（小时）
                <input
                  type="number"
                  min={1}
                  max={8760}
                  value={config.interval_hours}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      interval_hours: Number(e.target.value),
                    })
                  }
                  required
                />
              </label>
              <label>
                保留成功备份数量
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={config.keep}
                  onChange={(e) =>
                    setConfig({ ...config, keep: Number(e.target.value) })
                  }
                  required
                />
              </label>
            </div>
            <label>
              备份目录
              <input
                value={config.directory}
                onChange={(e) =>
                  setConfig({ ...config, directory: e.target.value })
                }
                required
                readOnly={!overview?.local_mode}
              />
            </label>
            <p className="subtle">
              上次成功：
              {overview?.backup.has_backup
                ? when(overview.backup.last_success)
                : "尚无备份"}{" "}
              · 下次计划：
              {config.enabled
                ? when(
                    Math.max(
                      config.last_success,
                      overview?.backup.last_success || 0,
                    ) +
                      config.interval_hours * 3600,
                  )
                : "已停用"}
            </p>
            {overview?.backup.last_error && (
              <p className="error-text">{overview.backup.last_error}</p>
            )}
            <div className="button-row">
              <button disabled={busy}>
                <Save size={15} />
                保存计划
              </button>
              <button
                className="primary"
                type="button"
                disabled={busy}
                onClick={() =>
                  action(
                    () => api("/backups", json("POST")),
                    "备份已加入后台队列",
                  )
                }
              >
                <Archive size={15} />
                立即备份
              </button>
            </div>
          </form>
        )}
        <div className="backup-list">
          {backups.length === 0 ? (
            <p className="subtle">尚无成功备份。</p>
          ) : (
            backups.slice(0, 20).map((backup) => (
              <div className="backup-row" key={backup.name}>
                <div>
                  <strong>{backup.name}</strong>
                  <span>
                    {when(backup.created)} · {size(backup.size)}
                  </span>
                </div>
                <a
                  className="button"
                  href={
                    "/api/backups/" +
                    encodeURIComponent(backup.name) +
                    "/download"
                  }
                  aria-label={"下载备份 " + backup.name}
                >
                  <Download size={15} />
                </a>
                <button
                  disabled={busy}
                  onClick={() => {
                    setRestoreName(backup.name);
                    setConfirmation("");
                  }}
                >
                  恢复
                </button>
              </div>
            ))
          )}
        </div>
        {restoreName && (
          <div className="restore-confirm">
            <h4>恢复 {restoreName}</h4>
            <p>
              当前整理信息将替换为此备份；系统先保存一份恢复前备份。原图片不会被删除，恢复后需检查目录映射。
            </p>
            <label>
              输入“恢复”以继续
              <input
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
              />
            </label>
            <div className="button-row">
              <button
                disabled={confirmation !== "恢复" || busy}
                onClick={() =>
                  action(async () => {
                    await api(
                      "/backups/" +
                        encodeURIComponent(restoreName) +
                        "/restore",
                      json("POST"),
                    );
                    setRestoreName("");
                  }, "已恢复图库信息，预览正在重建")
                }
              >
                确认恢复
              </button>
              <button onClick={() => setRestoreName("")}>取消</button>
            </div>
          </div>
        )}
      </section>
      <section className="settings-section">
        <h3>最近任务</h3>
        <p className="subtle">
          关闭浏览器后，后端继续处理索引、预览与备份；电脑休眠或后端停止时暂停，启动后补做到期任务。
        </p>
        {jobs.length === 0 ? (
          <p className="subtle">暂无后台任务。</p>
        ) : (
          jobs.slice(0, 20).map((job) => (
            <div className="job-row" key={job.id}>
              <strong>
                {{ scan: "目录扫描", preview: "预览转换", backup: "备份" }[
                  job.kind as string
                ] || job.kind}
              </strong>
              <span>
                {job.status === "queued"
                  ? "等待处理"
                  : job.status === "failed"
                    ? "失败"
                    : statusName[job.status] || job.status}
              </span>
              <p>{job.message || "等待后台处理"}</p>
            </div>
          ))
        )}
      </section>
      <p className="subtle">图库数据目录：{overview?.data_dir}</p>
    </dialog>
  );
}
