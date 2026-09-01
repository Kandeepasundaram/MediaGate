/**
 * History tab: the operations table, its filters, CSV export, and undo.
 */

import { showConfirm } from "./archive-tab.js";
import { $, $all, api } from "./core.js";
import { downloadCsv, rowsToCsv } from "./gallery.js";

// ---- History tab ----
function currentHistoryFilterParams() {
  const params = new URLSearchParams();
  const type = $("#history-type-filter").value;
  const status = $("#history-status-filter").value;
  const since = $("#history-since-filter").value;
  const until = $("#history-until-filter").value;
  if (type) params.set("operation_type", type);
  if (status) params.set("status", status);
  if (since) params.set("since", since);
  if (until) params.set("until", `${until}T23:59:59`); // inclusive of the whole day
  return params;
}

export async function loadHistory() {
  const tbody = $("#history-table tbody");
  tbody.innerHTML = "<tr><td colspan=5>Loading...</td></tr>";
  try {
    const data = await api(`/api/archive/history?${currentHistoryFilterParams().toString()}`);
    tbody.innerHTML = data.operations.map((op) => `
      <tr>
        <td>${new Date(op.created_at).toLocaleString()}</td>
        <td>${op.operation_type}</td>
        <td class="status-${op.status}">${op.status}</td>
        <td>${op.error_message || (op.details ? JSON.stringify(op.details) : "")}</td>
        <td>${op.status === "success" && (op.operation_type === "archive" || op.operation_type === "rename")
          ? `<button class="danger undo-btn" data-id="${op.id}">Undo</button>` : ""}</td>
      </tr>
    `).join("") || "<tr><td colspan=5>No history yet.</td></tr>";
    $all(".undo-btn").forEach((btn) => {
      btn.addEventListener("click", () => undoOperation(Number(btn.dataset.id)));
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan=5>Error: ${e.message}</td></tr>`;
  }
}

export async function exportHistoryView() {
  const params = currentHistoryFilterParams();
  params.set("limit", "10000"); // export everything matching the filters, not just the on-screen page
  $("#history-status").textContent = "Exporting...";
  try {
    const data = await api(`/api/archive/history?${params.toString()}`);
    const header = ["Time", "Type", "Status", "Details"];
    const rows = data.operations.map((op) => [
      op.created_at, op.operation_type, op.status,
      op.error_message || (op.details ? JSON.stringify(op.details) : ""),
    ]);
    downloadCsv(`history-export-${new Date().toISOString().slice(0, 10)}.csv`, rowsToCsv(header, rows));
    $("#history-status").textContent = "";
  } catch (e) {
    $("#history-status").textContent = `Export failed: ${e.message}`;
  }
}

async function undoOperation(opId) {
  const ok = await showConfirm("Undo this operation? This moves/deletes the file on disk.");
  if (!ok) return;
  $("#history-status").textContent = "Undoing...";
  try {
    await api(`/api/archive/history/${opId}/undo`, { method: "POST" });
    $("#history-status").textContent = "";
    loadHistory();
  } catch (e) {
    $("#history-status").textContent = `Undo failed: ${e.message}`;
  }
}
