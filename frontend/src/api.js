/**
 * 前端 API 封装。
 *
 * Vue 页面不直接接触数据库，也不读取本地 CSV 路径，所有数据都通过 FastAPI 的受控接口
 * 获取。这样以后切换 PostgreSQL、部署到服务器或接入万悟时，前端不需要改业务逻辑。
 */

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const API_KEY = import.meta.env.VITE_API_KEY || "";
const REQUEST_TIMEOUT_MS = Number(import.meta.env.VITE_API_TIMEOUT_MS || 30000);
const MAX_GET_RETRIES = 2;
const SESSION_TOKEN_KEY = "shicha_qianji_session_token";

export function getSessionToken() {
  return window.localStorage.getItem(SESSION_TOKEN_KEY) || "";
}

export function setSessionToken(token) {
  if (token) window.localStorage.setItem(SESSION_TOKEN_KEY, token);
  else window.localStorage.removeItem(SESSION_TOKEN_KEY);
}

function isRetryableStatus(status) {
  return [408, 425, 429].includes(status) || status >= 500;
}

function retryDelay(response, retryIndex) {
  const retryAfter = Number(response?.headers?.get("Retry-After"));
  if (Number.isFinite(retryAfter) && retryAfter >= 0) {
    return Math.min(retryAfter * 1000, 5000);
  }
  return Math.min(600 * (2 ** retryIndex), 3000);
}

function sleep(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function request(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const canRetry = ["GET", "HEAD"].includes(method);
  const timeoutMs = Number(options.timeoutMs || REQUEST_TIMEOUT_MS);
  const { timeoutMs: _timeoutOption, ...fetchOptions } = options;

  for (let retryIndex = 0; retryIndex <= (canRetry ? MAX_GET_RETRIES : 0); retryIndex += 1) {
    const headers = new Headers(fetchOptions.headers || {});
    if (API_KEY) headers.set("X-API-Key", API_KEY);
    const sessionToken = getSessionToken();
    if (sessionToken) headers.set("Authorization", `Bearer ${sessionToken}`);
    const controller = new AbortController();
    let timedOut = false;
    let timeoutId = null;
    let externalAbortHandler = null;

    if (fetchOptions.signal) {
      if (fetchOptions.signal.aborted) controller.abort(fetchOptions.signal.reason);
      externalAbortHandler = () => controller.abort(fetchOptions.signal.reason);
      fetchOptions.signal.addEventListener("abort", externalAbortHandler, { once: true });
    }

    if (timeoutMs > 0) {
      timeoutId = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, timeoutMs);
    }

    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        ...fetchOptions,
        method,
        headers,
        signal: controller.signal,
      });

      if (!response.ok && canRetry && retryIndex < MAX_GET_RETRIES && isRetryableStatus(response.status)) {
        await sleep(retryDelay(response, retryIndex));
        continue;
      }

      const contentType = response.headers.get("content-type") || "";
      let payload;
      try {
        payload = contentType.includes("application/json")
          ? await response.json()
          : await response.text();
      } catch {
        payload = "服务端返回了无法解析的响应";
      }
      if (!response.ok) {
        const detail = typeof payload === "object" ? payload.detail || payload.message : payload;
        // 会话过期后清除本地令牌；页面下一次刷新会回到登录入口，避免反复携带失效令牌。
        if (response.status === 401 && getSessionToken() && !path.startsWith("/api/v1/auth/login")) {
          setSessionToken("");
        }
        if (response.status === 429) {
          throw new Error(detail || "请求过于频繁，请稍后再试。系统会自动控制请求频率。");
        }
        throw new Error(detail || `请求失败：${response.status}`);
      }
      return payload;
    } catch (error) {
      if (error?.name === "AbortError") {
        if (timedOut) {
          const seconds = Math.max(1, Math.ceil(timeoutMs / 1000));
          throw new Error(`请求超时（${seconds} 秒），请检查后端服务或网络连接。`);
        }
        throw new Error("请求已取消。");
      }
      const shouldRetry = canRetry && retryIndex < MAX_GET_RETRIES && (
        error instanceof TypeError || /网络|超时|连接/i.test(error?.message || "")
      );
      if (shouldRetry) {
        await sleep(Math.min(600 * (2 ** retryIndex), 3000));
        continue;
      }
      if (error instanceof TypeError) {
        throw new Error(
          `无法连接后端服务（${API_BASE_URL}）。请确认后端已启动，并检查前端端口是否已被后端允许。`,
        );
      }
      throw error;
    } finally {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      if (externalAbortHandler && fetchOptions.signal) {
        fetchOptions.signal.removeEventListener("abort", externalAbortHandler);
      }
    }
  }
}

export function health(options = {}) {
  return request("/health", options);
}

export function getAuthConfig() {
  return request("/api/v1/auth/config");
}

export function login(username, password) {
  return request("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function getCurrentUser() {
  return request("/api/v1/auth/me");
}

export async function logout() {
  try {
    return await request("/api/v1/auth/logout", { method: "POST" });
  } finally {
    setSessionToken("");
  }
}

export function listUsers() {
  return request("/api/v1/users");
}

export function listMyNotifications(unreadOnly = false) {
  return request(`/api/v1/notifications/mine?unread_only=${unreadOnly ? "true" : "false"}`);
}

export function acknowledgeNotification(notificationId) {
  return request("/api/v1/notifications/acknowledge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notification_id: notificationId }),
  });
}

// 自动监测看板同时返回数据源、采集批次和主动通知，避免前端分别拼接多份状态。
export function getMonitoringStatus() {
  return request("/api/v1/monitoring/status");
}

export function saveMonitoringSource(payload) {
  return request("/api/v1/monitoring/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function pollMonitoringSource(sourceId) {
  return request(`/api/v1/monitoring/sources/${encodeURIComponent(sourceId)}/poll`, {
    method: "POST",
  });
}

export function deleteMonitoringSource(sourceId) {
  return request(`/api/v1/monitoring/sources/${encodeURIComponent(sourceId)}`, {
    method: "DELETE",
  });
}

// 获取后端已启用的设备配置，供分析前选择；接口不返回企业文件和本地路径。
export function listDeviceProfiles() {
  return request("/api/v1/device-profiles");
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

// 对已完成任务按需生成辅助解释；不重新上传文件或执行工业分析。
export function explainRun(runId, question = "") {
  return request("/api/v1/wanwu/jobs/explain", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, ...(question.trim() ? { question: question.trim() } : {}) }),
  });
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
  if (options.mine) params.set("mine", "true");
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

export function acceptWorkOrder(recordId) {
  return request(`/api/v1/work-orders/${encodeURIComponent(recordId)}/accept`, {
    method: "POST",
  });
}

export function assignWorkOrder(recordId, userId) {
  return request(`/api/v1/work-orders/${encodeURIComponent(recordId)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
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

export function deleteArchivedWorkOrder(recordId) {
  return request(`/api/v1/work-orders/${encodeURIComponent(recordId)}`, { method: "DELETE" });
}

export function deleteArchivedRun(runId) {
  return request(`/api/v1/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
}

export { API_BASE_URL };
