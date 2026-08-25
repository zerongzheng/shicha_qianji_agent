import { createApp, nextTick } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import MonitoringPanel from "./MonitoringPanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("MonitoringPanel", () => {
  it("展示自动采集批次和分级通知", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const app = createApp(MonitoringPanel, {
        monitoring: {
          status: "success",
          monitor: { running: true },
          notification_channels: { wecom: { enabled: true, configured: true } },
          enabled_source_count: 1,
          sources: [{
            source_id: "src_demo01",
            name: "阀门测试台",
            source_type: "directory",
            endpoint: "E:/incoming",
            interval_seconds: 10,
            enabled: true,
            last_success_at: "2026-08-12T12:00:00+08:00",
          }],
          ingestions: [{
            ingestion_id: "ing_demo01",
            file_name: "batch.csv",
            status: "completed",
            run_id: "run_demo01",
            detected_at: "2026-08-12T12:00:00+08:00",
          }],
          notifications: [{
            notification_id: "ntf_demo01",
            priority: "P1",
            title: "压力与流量关系异常",
            recipient_name: "张工",
            recipient_role: "生产负责人",
            status: "sent",
          }],
        },
        currentRunId: "run_demo01",
        analysis: {
          detector: "time_frequency_relation",
          execution_trace: [
            { step_id: "profile", status: "completed" },
            { step_id: "detect", status: "completed" },
          ],
          anomaly_events: [{ severity: "高风险" }],
          work_order_drafts: [{ work_order_id: "WO-01" }],
          model_selection: {
            selected_detector_name: "时频关系多路径检测器",
            analysis_goal_name: "综合平衡",
            reason: "依据设备配置自动路由主模型。",
          },
          root_cause_diagnoses: [{
            primary_candidate: { name: "传感器漂移或采集链路异常" },
          }],
        },
        formatDate: (value) => value || "暂无",
    });
    app.mount(container);

    expect(container.textContent).toContain("监测服务运行中");
    expect(container.textContent).toContain("企业微信机器人已启用");
    expect(container.textContent).toContain("batch.csv");
    expect(container.textContent).toContain("压力与流量关系异常");
    expect(container.textContent).toContain("张工");
    expect(container.textContent).toContain("2 个模块自动完成");
    expect(container.textContent).toContain("时频关系多路径检测器");
    expect(container.textContent).toContain("识别 1 个异常事件");
    expect(container.textContent).toContain("传感器漂移或采集链路异常");
    expect(container.textContent).toContain("生成 1 张工单");
    app.unmount();
  });

  it("数据源表单不读取或展示企业微信 Webhook 密钥", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const app = createApp(MonitoringPanel, {
      monitoring: {
        status: "success",
        monitor: { running: false },
        notification_channels: { wecom: { enabled: false, configured: true } },
        sources: [],
        ingestions: [],
        notifications: [],
      },
      formatDate: (value) => value || "暂无",
    });
    app.mount(container);

    expect(container.textContent).toContain("企业微信机器人未启用");
    const createButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "新增数据源");
    createButton.click();
    await nextTick();
    expect(container.querySelector('input[type="url"]')).toBeNull();
    expect(container.textContent).toContain("机器人密钥不会发送到浏览器");
    app.unmount();
  });

  it("完成的自动任务可以直接进入结果工作区", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const onViewRun = vi.fn();
    const app = createApp(MonitoringPanel, {
      monitoring: {
        status: "success",
        monitor: { running: true },
        sources: [],
        ingestions: [{
          ingestion_id: "ing_demo02",
          file_name: "new-batch.csv",
          status: "completed",
          run_id: "run_auto02",
          detected_at: "2026-08-12T13:00:00+08:00",
        }],
        notifications: [],
      },
      formatDate: (value) => value || "暂无",
      onViewRun,
    });
    app.mount(container);

    container.querySelector(".view-result").click();
    expect(onViewRun).toHaveBeenCalledWith("run_auto02");
    app.unmount();
  });

  it("主列表只突出启用数据源，停用配置收纳到审计区", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const app = createApp(MonitoringPanel, {
      monitoring: {
        status: "success",
        monitor: { running: true },
        enabled_source_count: 1,
        sources: [
          { source_id: "active", name: "生产监测源", source_type: "directory", endpoint: "../SKAB/data/valve1", interval_seconds: 60, enabled: true },
          { source_id: "inactive", name: "旧验证源", source_type: "directory", endpoint: "../SKAB/data/valve1", interval_seconds: 60, enabled: false },
        ],
        ingestions: [],
        notifications: [],
      },
      formatDate: (value) => value || "暂无",
    });
    app.mount(container);

    const mainList = container.querySelector(":scope > .monitoring-stack > .source-list");
    const archive = container.querySelector(".inactive-sources");
    expect(mainList.textContent).toContain("生产监测源");
    expect(mainList.textContent).not.toContain("旧验证源");
    expect(archive.textContent).toContain("1 项历史配置");
    expect(archive.textContent).toContain("旧验证源");
    expect(container.textContent).toContain("../SKAB/data/valve1");
    app.unmount();
  });
});
