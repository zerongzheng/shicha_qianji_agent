import { afterEach, describe, expect, it, vi } from "vitest";
import { health, listDeviceProfiles, setSessionToken } from "./api";

afterEach(() => {
  setSessionToken("");
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function jsonResponse(payload, status = 200, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json", ...headers }),
    json: vi.fn(async () => payload),
    text: vi.fn(async () => JSON.stringify(payload)),
  };
}

describe("前端 API 请求封装", () => {
  it("可以读取设备配置列表", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ profiles: [{ profile_id: "skab_valve" }] }),
    );

    await expect(listDeviceProfiles()).resolves.toEqual({
      profiles: [{ profile_id: "skab_valve" }],
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/device-profiles"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("登录后自动为业务请求附带 Bearer 会话令牌", async () => {
    setSessionToken("session-test-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ status: "ok" }));

    await health();

    const requestOptions = fetch.mock.calls[0][1];
    expect(requestOptions.headers.get("Authorization")).toBe("Bearer session-test-token");
  });

  it("GET 遇到临时 503 后自动重试并返回结果", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "暂时不可用" }, 503))
      .mockResolvedValueOnce(jsonResponse({ status: "ok" }));

    const result = await health();

    expect(result).toEqual({ status: "ok" });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("429 不会被转换成空白错误，并说明需要稍后重试", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "QPM 已达到限制" }, 429),
    );

    await expect(health()).rejects.toThrow("QPM 已达到限制");
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("请求超时会给出明确提示", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_url, options) => (
      new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      })
    ));

    const promise = health({ timeoutMs: 20 });
    const assertion = expect(promise).rejects.toThrow("请求超时");
    await vi.advanceTimersByTimeAsync(20);
    await assertion;
  });

  it("网络连接失败时显示后端地址而不是 Failed to fetch", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(health()).rejects.toThrow("无法连接后端服务（http://127.0.0.1:8000）");
  });
});
