import { useEffect, useState } from "react";
import { ArrowUp, Folder, HardDrive, Save } from "lucide-react";
import { api, json } from "./api";
import "./StorageSettings.css";

type Storage = {
  directory: string;
  default_directory: string;
  data_directory: string;
  cache_directory: string;
  managed_assets: number;
  local_mode: boolean;
  copied_files?: number;
  retained_directory?: string;
};
type DirectoryListing = {
  path: string;
  parent: string | null;
  directories: { name: string; path: string }[];
};

export function StorageSettings({
  active,
  onChange,
}: {
  active: boolean;
  onChange: () => void;
}) {
  const [storage, setStorage] = useState<Storage | null>(null);
  const [directory, setDirectory] = useState("");
  const [listing, setListing] = useState<DirectoryListing | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (!active) return;
    let alive = true;
    api<Storage>("/settings/storage")
      .then((value) => {
        if (!alive) return;
        setStorage(value);
        setDirectory(value.directory);
        setListing(null);
        setError("");
      })
      .catch((e) => {
        if (alive) setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [active]);

  async function browse(path = "") {
    setError("");
    try {
      setListing(
        await api<DirectoryListing>(
          "/directories" + (path ? "?path=" + encodeURIComponent(path) : ""),
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await api<Storage>(
        "/settings/storage",
        json("PUT", { directory }),
      );
      setStorage(value);
      setDirectory(value.directory);
      setListing(null);
      setNotice(
        value.retained_directory
          ? `存储位置已更新，复制并校验了 ${value.copied_files} 个原文件。旧副本保留在 ${value.retained_directory}。`
          : "当前已使用此存储目录。",
      );
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-section storage-settings">
      <h3>
        <HardDrive size={18} />
        图片存储位置
      </h3>
      <p className="subtle">
        上传、AI 生成和 SVG
        编辑的图片副本统一保存在这里。关联目录中的原文件仍保留原位。
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
      {storage && (
        <>
          <dl className="storage-locations">
            <div>
              <dt>当前图片目录 · {storage.managed_assets} 张素材</dt>
              <dd>{storage.directory}</dd>
            </div>
            <div>
              <dt>数据库与缓存 · 保留在本机</dt>
              <dd>{storage.data_directory}</dd>
            </div>
          </dl>
          {storage.local_mode ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void save();
              }}
            >
              <fieldset disabled={busy}>
                <label>
                  图片存储目录
                  <input
                    value={directory}
                    onChange={(e) => setDirectory(e.target.value)}
                    required
                    placeholder="输入目标文件夹的完整路径"
                  />
                </label>
                <div className="button-row storage-browse-actions">
                  <button type="button" onClick={() => void browse()}>
                    <Folder size={15} />
                    浏览目录
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setDirectory(storage.default_directory);
                      setListing(null);
                    }}
                  >
                    使用默认目录
                  </button>
                </div>
                {listing && (
                  <div className="storage-browser">
                    <p className="subtle">{listing.path || "选择一个位置"}</p>
                    <div className="button-row">
                      {listing.parent && (
                        <button
                          type="button"
                          onClick={() => void browse(listing.parent!)}
                        >
                          <ArrowUp size={14} />
                          上一级
                        </button>
                      )}
                      {!!listing.path && (
                        <button
                          type="button"
                          onClick={() => {
                            setDirectory(listing.path);
                            setListing(null);
                          }}
                        >
                          选择此目录
                        </button>
                      )}
                      <button type="button" onClick={() => setListing(null)}>
                        收起
                      </button>
                    </div>
                    <div className="storage-directory-list">
                      {listing.directories.map((child) => (
                        <button
                          key={child.path}
                          type="button"
                          onClick={() => void browse(child.path)}
                        >
                          <Folder size={14} />
                          {child.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <p className="subtle">
                  请选择空目录，或输入完整路径创建专用文件夹。保存时会复制并校验现有图片，成功后切换；旧目录保留副本。图片较多时请等待完成。目标磁盘或网盘目录需保持可访问。
                </p>
                <button
                  type="submit"
                  disabled={directory.trim() === storage.directory}
                >
                  <Save size={15} />
                  {busy ? "正在复制并校验…" : "保存图片存储位置"}
                </button>
              </fieldset>
            </form>
          ) : (
            <p className="subtle">
              当前使用远程服务，目录位于服务器，需由管理员设置。
            </p>
          )}
        </>
      )}
    </section>
  );
}
