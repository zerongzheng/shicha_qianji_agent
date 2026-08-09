/**
 * 前端 API 封装。
 *
 * Vue 页面不直接接触数据库，也不读取本地 CSV 路径，所有数据都通过 FastAPI 的受控接口
 * 获取。这样以后切换 PostgreSQL、部署到服务器或接入万悟时，前端不需要改业务逻辑。
 */

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const API_KEY = import.meta.env.VITE_API_KEY || "";

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (API_KEY) headers.set("X-API-Key", API_KEY);
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail || payload.message : payload;
    throw new Error(detail || `请求失败：${response.status}`);
  }
  return payload;
}

export function health() {
  return request("/health");
}

export function uploadCsv(file) {
  const formData = new FormData();
  formData.append("file", file);
  return request("/api/v1/files", { method: "POST", body: formData });
}

// 登记项目内置 SKAB 样例；文件仍由后端统一管理，浏览器不直接访问本地路径。
export function registerDefaultSkabSample() {
  return request("/api/v1/samples/skab/default", { method: "POST" });
}

export function getFilePreflight(fileId) {
  return request(`/api/v1/files/${encodeURIComponent(fileId)}/preflight`);
}

export function createJob(fileId, config = {}) {
  return request("/api/v1/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, operation: "analyze", config }),
  });
}

export function getJobStatus(runId) {
  return request(`/api/v1/jobs/${encodeURIComponent(runId)}`);
}

// 取消尚未完成的分析任务。后端会根据任务当前状态决定是否允许取消。
export function cancelJob(runId) {
  return request(`/api/v1/jobs/${encodeURIComponent(runId)}`, { method: "DELETE" });
}

export function getJobResult(runId) {
  return request(`/api/v1/jobs/${encodeURIComponent(runId)}/result`);
}

export function getRun(runId) {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}`);
}

export function listRuns(status = "", includeArchived = false, archivedOnly = false) {
  const params = new URLSearchParams({ limit: "50" });
  if (status) params.set("status", status);
  if (includeArchived) params.set("include_archived", "true");
  if (archivedOnly) params.set("archived_only", "true");
  const query = `?${params.toString()}`;
  return request(`/api/v1/runs${query}`);
}

export function listWorkOrders(includeArchived = false, archivedOnly = false, options = {}) {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 10),
    offset: String(options.offset ?? 0),
  });
  if (options.status) params.set("status", options.status);
  if (options.priority) params.set("priority", options.priority);
  // 事件与工单联动时必须限定所属分析任务，避免不同任务中相同事件编号串线。
  if (options.run_id) params.set("run_id", options.run_id);
  if (options.search?.trim()) params.set("search", options.search.trim());
  if (includeArchived) params.set("include_archived", "true");
  if (archivedOnly) params.set("archived_only", "true");
  const query = `?${params.toString()}`;
  return request(`/api/v1/work-orders${query}`);
}

export function updateWorkOrder(recordId, payload) {
  return request(`/api/v1/work-orders/${encodeURIComponent(recordId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function listCases(includeArchived = false, archivedOnly = false) {
  const params = new URLSearchParams({ limit: "50" });
  if (includeArchived) params.set("include_archived", "true");
  if (archivedOnly) params.set("archived_only", "true");
  const query = `?${params.toString()}`;
  return request(`/api/v1/cases${query}`);
}

// 删除案例记忆；来源分析任务和原始数据不会被删除。
export function removeCase(caseId) {
  return request(`/api/v1/cases/${encodeURIComponent(caseId)}`, { method: "DELETE" });
}

export function archiveRun(runId, reason = "用户归档") {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}/archive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

export function restoreRun(runId) {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}/restore`, { method: "POST" });
}

export function archiveWorkOrder(recordId, reason = "用户归档") {
  return request(`/api/v1/work-orders/${encodeURIComponent(recordId)}/archive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

export function restoreWorkOrder(recordId) {
  return request(`/api/v1/work-orders/${encodeURIComponent(recordId)}/restore`, { method: "POST" });
}

export { API_BASE_URL };
