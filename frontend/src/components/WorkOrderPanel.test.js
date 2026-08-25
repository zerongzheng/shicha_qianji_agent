import { createApp } from "vue";
import { afterEach, describe, expect, it } from "vitest";
import WorkOrderPanel from "./WorkOrderPanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

function mountPanel(order) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const feedback = {
    status: order.status,
    confirmed_cause: order.confirmed_cause || "",
    feedback_note: order.feedback_note || "",
    handled_by: order.handled_by || "",
  };
  const app = createApp(WorkOrderPanel, {
    workOrders: [order],
    workOrderTotal: 1,
    selectedWorkOrder: order,
    feedback,
    formatDate: (value) => value || "暂无",
    formatNumber: (value) => String(value ?? "暂无"),
  });
  app.mount(container);
  return { app, container };
}

describe("WorkOrderPanel", () => {
  it("展示 SLA 超时升级状态", () => {
    const order = {
      record_id: "run_demo:WO-01",
      run_id: "run_demo",
      title: "压力与流量关系异常",
      priority: "P1",
      status: "待确认",
      assigned_role: "设备运维",
      sla_level: 2,
      evidence_summary: [],
      actions: [],
    };
    const { app, container } = mountPanel(order);

    expect(container.textContent).toContain("已超时升级");
    expect(container.textContent).toContain("已自动升级至生产负责人");
    app.unmount();
  });

  it("展示待验证工单的自动复检计划", () => {
    const order = {
      record_id: "run_demo:WO-02",
      run_id: "run_demo",
      title: "阀门执行机构异常",
      priority: "P2",
      status: "待验证",
      assigned_role: "设备工程师",
      confirmed_cause: "阀门执行机构卡滞",
      feedback_note: "完成清理和标定",
      reinspection_status: "pending",
      reinspection_scheduled_at: "2026-08-15T12:00:00+08:00",
      evidence_summary: [],
      actions: [],
    };
    const { app, container } = mountPanel(order);

    expect(container.textContent).toContain("等待自动复检");
    expect(container.textContent).toContain("同一数据源产生新批次后自动复检");
    app.unmount();
  });

  it("展示自动复检失败的任务和结论", () => {
    const order = {
      record_id: "run_demo:WO-03",
      run_id: "run_demo",
      title: "压力异常仍然存在",
      priority: "P1",
      status: "处理中",
      assigned_role: "设备运维",
      reinspection_status: "failed",
      reinspection_run_id: "run_recheck_01",
      reinspection_summary: "维修后同源批次仍检出原异常主导测点：pressure",
      evidence_summary: [],
      actions: [],
    };
    const { app, container } = mountPanel(order);

    expect(container.textContent).toContain("自动复检未通过");
    expect(container.textContent).toContain("run_recheck_01");
    expect(container.textContent).toContain("仍检出原异常主导测点");
    app.unmount();
  });
});
