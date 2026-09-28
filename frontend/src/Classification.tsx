import { useEffect, useRef, useState } from "react";
import { Check, Pencil, Save, Sparkles, Tags, X } from "lucide-react";
import {
  api,
  json,
  when,
  Asset,
  ClassificationRecord,
  classificationCategories,
} from "./api";

export type ClassificationConfig = {
  rules_enabled: boolean;
  auto_ai: boolean;
  daily_limit: number;
  used_today: number;
  remaining_today: number;
  analysis_ready: boolean;
  analysis_base_url: string;
  analysis_model: string;
  categories: string[];
  pending: number;
};
type BatchResult = {
  processed: number;
  queued: number;
  skipped: number;
  task_ids: string[];
};

export function AutomaticClassification({
  assetIds,
  initialProject,
  projects,
  onConfigure,
  onChange,
}: {
  assetIds: string[];
  initialProject: string;
  projects: { name: string }[];
  onConfigure: () => void;
  onChange: (assetId?: string) => void;
}) {
  const [config, setConfig] = useState<ClassificationConfig | null>(null);
  const [form, setForm] = useState({
    rules_enabled: true,
    auto_ai: false,
    daily_limit: 20,
  });
  const [scope, setScope] = useState(
    assetIds.length
      ? "selected"
      : initialProject
        ? "project:" + initialProject
        : "library",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const initialized = useRef(false);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const value = await api<ClassificationConfig>(
          "/classification/config",
          { signal: controller.signal },
        );
        if (!alive) return;
        setConfig(value);
        if (!initialized.current) {
          initialized.current = true;
          setForm({
            rules_enabled: value.rules_enabled,
            auto_ai: value.auto_ai,
            daily_limit: value.daily_limit,
          });
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) timer = setTimeout(poll, 3000);
      }
    }
    void poll();
    return () => {
      alive = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await api<ClassificationConfig>(
        "/classification/config",
        json("PUT", form),
      );
      setConfig(value);
      setForm({
        rules_enabled: value.rules_enabled,
        auto_ai: value.auto_ai,
        daily_limit: value.daily_limit,
      });
      setNotice("自动整理设置已保存；已有图片可在下方单独整理。");
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function batch(method: "rules" | "ai") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<BatchResult>(
        "/classification/batch",
        json("POST", {
          method,
          ...(scope === "selected" ? { asset_ids: assetIds } : {}),
          ...(scope.startsWith("project:") ? { project: scope.slice(8) } : {}),
        }),
      );
      setNotice(
        `已处理 ${result.processed} 张，已排队 ${result.queued} 张，跳过 ${result.skipped} 张。${method === "ai" ? "结果会自动加入图片的分类信息；可在任务记录查看进度。" : "可用分类筛选或自动标签搜索，并在详情中修正。"}`,
      );
      onChange(assetIds.length === 1 ? assetIds[0] : undefined);
      try {
        setConfig(await api<ClassificationConfig>("/classification/config"));
      } catch {
        /* The regular status refresh will retry; never resubmit this batch. */
      }
    } catch (e) {
      setError((e as Error).message + "；请先查看任务记录再决定是否重新提交。");
    } finally {
      setBusy(false);
    }
  }
  const selectedTooMany = scope === "selected" && assetIds.length > 100;
  return (
    <section className="classification-workspace">
      <div className="classification-intro">
        <Tags size={24} />
        <div>
          <h3>让新图片自动归类</h3>
          <p>
            分类与自动标签独立保存，可搜索、修正或忽略；不会覆盖手动标签、备注或项目归属。
          </p>
        </div>
      </div>
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
      {!config ? (
        <p className="subtle">正在读取自动整理设置…</p>
      ) : (
        <>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
            className="classification-settings"
          >
            <fieldset disabled={busy}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.rules_enabled}
                  onChange={(e) =>
                    setForm({ ...form, rules_enabled: e.target.checked })
                  }
                />
                导入时自动进行本地分类
              </label>
              <p className="subtle">
                根据文件名、路径和格式归类，不识别图片内容、不发送图片、不产生
                API 费用。
              </p>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.auto_ai}
                  disabled={!config.analysis_ready && !form.auto_ai}
                  onChange={(e) =>
                    setForm({ ...form, auto_ai: e.target.checked })
                  }
                />
                新导入图片自动调用 AI 分类
              </label>
              <p className="classification-disclosure">
                开启后，新导入图片的预览会自动发送至{" "}
                {config.analysis_base_url || "尚未配置的分类服务"}，使用模型{" "}
                {config.analysis_model || "尚未配置"}，并按服务商规则计费。多页
                PDF/TIFF
                只分析第一页。保存后只作用于新导入图片，已有图库需手动发起下方批处理。
              </p>
              {!config.analysis_ready && (
                <div className="button-row">
                  <span className="subtle">
                    先配置可用的图片分类接口和密钥。
                  </span>
                  <button type="button" onClick={onConfigure}>
                    配置 AI 分类服务
                  </button>
                </div>
              )}
              <label className="classification-limit">
                24 小时 AI 分类上限
                <input
                  type="number"
                  min={1}
                  max={200}
                  required
                  value={form.daily_limit}
                  onChange={(e) =>
                    setForm({ ...form, daily_limit: Number(e.target.value) })
                  }
                />
              </label>
              <p className="subtle">
                最近 24 小时已提交 {config.used_today} 次，剩余{" "}
                {config.remaining_today} 次；等待分类 {config.pending}{" "}
                张。失败请求也计入额度，避免反复调用。此额度用于自动整理的 AI
                分类。
              </p>
              <button type="submit">
                <Save size={15} />
                {busy ? "处理中…" : "保存自动整理设置"}
              </button>
            </fieldset>
          </form>
          <section className="classification-batch">
            <h3>整理已有图片</h3>
            <label>
              整理范围
              <select
                value={scope}
                disabled={busy}
                onChange={(e) => setScope(e.target.value)}
              >
                {assetIds.length > 0 && (
                  <option value="selected">
                    所选 {assetIds.length} 张图片
                  </option>
                )}
                <option value="library">全图库 · 下一批未分类图片</option>
                {projects.map((project) => (
                  <option key={project.name} value={"project:" + project.name}>
                    项目：{project.name} · 下一批
                  </option>
                ))}
              </select>
            </label>
            <p className="subtle">
              每批最多 100 张。本地规则可刷新所选图片中未经人工修正的结果；AI
              会跳过已处理、失败或已提交的同内容图片，排队还受剩余额度限制。全图库和项目范围只选择下一批符合条件的图片。手动修正和已忽略的结果会保留。
              如需再次分析失败图片，可选中它后使用「图片分类」页的人工审核流程。
            </p>
            {selectedTooMany && (
              <p className="error" role="alert">
                一次最多选择 100 张，请减少所选图片。
              </p>
            )}
            <div className="button-row">
              <button
                disabled={busy || selectedTooMany}
                onClick={() => void batch("rules")}
              >
                <Tags size={16} />
                运行本地分类
              </button>
              <button
                className="primary"
                disabled={
                  busy ||
                  selectedTooMany ||
                  !config.analysis_ready ||
                  config.remaining_today <= 0
                }
                onClick={() => void batch("ai")}
              >
                <Sparkles size={16} />
                发送预览并 AI 分类
              </button>
            </div>
          </section>
        </>
      )}
    </section>
  );
}

export function ClassificationBadge({
  asset,
  selectedCategory = "",
}: {
  asset: Asset;
  selectedCategory?: string;
}) {
  const entries =
    asset.classifications?.filter((item) => item.status === "active") || [];
  const item =
    entries.find((entry) => entry.category === selectedCategory) ||
    entries.find((entry) => entry.manual_override) ||
    entries.find((entry) => entry.origin === "ai") ||
    entries[0];
  if (!item) return null;
  return (
    <span
      className={"classification-badge classification-" + item.origin}
      title={`${item.origin === "ai" ? "AI 分类" : "本地规则分类"}${item.manual_override ? "，已人工修正" : ""}${item.tags.length ? " · " + item.tags.join("、") : ""}`}
    >
      <span>{item.category}</span>
      <small>
        {item.manual_override ? "已修正" : item.origin === "ai" ? "AI" : "本地"}
      </small>
    </span>
  );
}

export function AssetClassifications({
  asset,
  onChange,
  onAutomatic,
}: {
  asset: Asset;
  onChange: () => void;
  onAutomatic: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function classify() {
    setBusy(true);
    setError("");
    try {
      await api(
        "/classification/batch",
        json("POST", { asset_ids: [asset.id], method: "rules" }),
      );
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const active =
    asset.classifications?.filter((entry) => entry.status === "active") || [];
  const dismissed =
    asset.classifications?.filter((entry) => entry.status === "dismissed") ||
    [];
  return (
    <section className="classification-detail">
      <p className="subtle">
        自动分类和标签可用于查找图片，独立于手动填写的信息。可直接修正结果，或忽略不适合的分类。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="button-row">
        <button disabled={busy} onClick={() => void classify()}>
          <Tags size={14} />
          本地分类
        </button>
        <button onClick={onAutomatic}>
          <Sparkles size={14} />
          自动整理
        </button>
      </div>
      {!active.length && <p className="subtle">尚无有效的自动分类。</p>}
      {active.map((record) => (
        <ClassificationEditor
          key={record.id}
          record={record}
          onChange={onChange}
        />
      ))}
      {!!dismissed.length && (
        <details className="classification-dismissed">
          <summary>已忽略的分类（{dismissed.length}）</summary>
          {dismissed.map((record) => (
            <ClassificationEditor
              key={record.id}
              record={record}
              onChange={onChange}
            />
          ))}
        </details>
      )}
    </section>
  );
}

function ClassificationEditor({
  record,
  onChange,
}: {
  record: ClassificationRecord;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [category, setCategory] = useState(record.category);
  const [tags, setTags] = useState(record.tags.join(", "));
  const [description, setDescription] = useState(record.description);
  useEffect(() => {
    if (!editing) {
      setCategory(record.category);
      setTags(record.tags.join(", "));
      setDescription(record.description);
    }
  }, [record.updated, editing]);
  async function change(dismiss = false) {
    const parsedTags = tags
      .split(/[,，]/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (!dismiss && parsedTags.length > 30) {
      setError("自动标签最多填写 30 个。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(
        "/classification/" + record.id + (dismiss ? "/dismiss" : ""),
        json(
          dismiss ? "POST" : "PUT",
          dismiss
            ? undefined
            : {
                category,
                tags: parsedTags,
                description,
              },
        ),
      );
      setEditing(false);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="classification-record">
      <div className="classification-record-heading">
        <strong>{record.origin === "ai" ? "AI 分类" : "本地规则分类"}</strong>
        {record.manual_override && (
          <span>
            <Check size={12} />
            已修正
          </span>
        )}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void change();
          }}
        >
          <fieldset disabled={busy}>
            <label>
              分类
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {!classificationCategories.includes(category) && (
                  <option>{category}</option>
                )}
                {classificationCategories.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            <label>
              自动标签
              <input
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                maxLength={2000}
                placeholder="逗号分隔"
              />
            </label>
            <label>
              描述
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                maxLength={2000}
              />
            </label>
            <div className="button-row">
              <button type="submit" className="primary">
                <Save size={14} />
                {record.status === "dismissed" ? "保存并恢复分类" : "保存修正"}
              </button>
              <button type="button" onClick={() => setEditing(false)}>
                取消
              </button>
            </div>
          </fieldset>
        </form>
      ) : (
        <>
          <h4>{record.category}</h4>
          {!!record.tags.length && (
            <div className="classification-tags">
              {record.tags.map((tag, index) => (
                <span key={index}>{tag}</span>
              ))}
            </div>
          )}
          {record.description && <p>{record.description}</p>}
          <small className="subtle">{when(record.updated)}</small>
          <div className="button-row">
            <button disabled={busy} onClick={() => setEditing(true)}>
              <Pencil size={13} />
              {record.status === "dismissed" ? "恢复并修正" : "修正"}
            </button>
            {record.status !== "dismissed" && (
              <button disabled={busy} onClick={() => void change(true)}>
                <X size={13} />
                忽略
              </button>
            )}
          </div>
        </>
      )}
    </article>
  );
}
