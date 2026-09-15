export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    ...options,
    headers: {
      "X-Figtrace-Request": "1",
      ...(options.body && typeof options.body === "string"
        ? { "Content-Type": "application/json" }
        : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new ApiError(
      typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail),
      response.status,
    );
  }
  return response.json();
}
export const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});
export const size = (bytes: number) =>
  bytes >= 1024 ** 3
    ? (bytes / 1024 ** 3).toFixed(1) + " GB"
    : bytes >= 1024 ** 2
      ? (bytes / 1024 ** 2).toFixed(1) + " MB"
      : bytes >= 1024
        ? (bytes / 1024).toFixed(1) + " KB"
        : bytes + " B";
export const when = (timestamp: number) =>
  timestamp
    ? new Date(timestamp * 1000).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "尚未运行";
export type Asset = {
  id: string;
  figure_id: string;
  title: string;
  name: string;
  format: string;
  project: string;
  tags: string[];
  notes: string;
  version_note: string;
  preview: string;
  error: string;
  size: number;
  pages: number;
  mode: string;
  width: number | null;
  height: number | null;
  mtime: number;
  created: number;
  versions: number;
  preferred: string;
  path: string;
  source_path: string;
  root_id: string;
  relative_path: string;
  versions_list: Asset[];
  links: { id: string; path: string; label: string }[];
};
export type Root = {
  id: string;
  name: string;
  path: string;
  kind: string;
  status: string;
  last_scan: number;
  project: string;
  allow_remote: number;
};
export type BackupConfig = {
  enabled: boolean;
  interval_hours: number;
  keep: number;
  directory: string;
  last_success: number;
  last_error: string;
  has_backup: boolean;
};
export type Overview = {
  assets: number;
  figures: number;
  pending: number;
  projects: string[];
  roots: Root[];
  active_jobs: number;
  backup: BackupConfig;
  local_mode: boolean;
  data_dir: string;
};
export const statusName: Record<string, string> = {
  queued: "等待预览",
  ready: "预览就绪",
  running: "处理中",
  failed: "预览失败",
  unsupported: "暂无预览",
  missing: "原文件离线",
  partial: "部分可用",
  offline: "目录离线",
  done: "已完成",
};
