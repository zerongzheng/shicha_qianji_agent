import { createApp } from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import HistoryPanel from "./HistoryPanel.vue";
import WorkOrderPanel from "./WorkOrderPanel.vue";

afterEach(() => {
  document.body.innerHTML = "";
});

function mount(component, props) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const app = createApp(component, props);
  app.mount(container);
  return { app, container };
}

describe("归档记录永久删除入口", () => {
  it("历史任务只有在归档视图中才显示彻底删除", () => {
    const onDeleteRun = vi.fn();
    const baseProps = {
      runs: [],
      filteredHistoryRuns: [],
      paginatedHistoryRuns: [{
        run_id: "run_archived",
        file_name: "test.csv",
        detector: "mad",
        status: "success",
        archived_at: "2026-08-12T12:00:00+08:00",
      }],
      cases: [],
      showArchived: true,
      formatDate: (value) => value || "-",
      formatNumber: (value) => String(value),
      onDeleteRun,
    };
    const { app, container } = mount(HistoryPanel, baseProps);

    const deleteButton = [...container.querySelectorAll("button")]
      .find((button) => button.textContent === "彻底删除");
    expect(deleteButton).toBeTruthy();
    deleteButton.click();
    expect(onDeleteRun).toHaveBeenCalledWith(expect.objectContaining({ run_id: "run_archived" }));
    app.unmount();
  });

  it("归档工单列表提供恢复和彻底删除两个明确操作", () => {
    const onDeleteOrder = vi.fn();
    const { app, container } = mount(WorkOrderPanel, {
      showArchived: true,
      workOrders: [{
        record_id: "run_demo:WO-01",
        run_id: "run_demo",
        priority: "P3",
        title: "开发测试工单",
        status: "已关闭",
        assigned_role: "设备运维",
        archived_at: "2026-08-12T12:00:00+08:00",
      }],
      workOrderTotal: 1,
      feedback: { status: "已关闭" },
      formatDate: (value) => value || "-",
      formatNumber: (value) => String(value),
      onDeleteOrder,
    });

    const buttons = [...container.querySelectorAll(".work-order-row button")];
    expect(buttons.map((button) => button.textContent)).toEqual(["恢复", "彻底删除"]);
    buttons[1].click();
    expect(onDeleteOrder).toHaveBeenCalledWith(expect.objectContaining({ record_id: "run_demo:WO-01" }));
    app.unmount();
  });
});
