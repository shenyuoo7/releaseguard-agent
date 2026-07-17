"use strict";

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "操作失败，请稍后重试。");
  }
  return payload;
}

function showMessage(element, text, type = "info") {
  if (!element) return;
  element.hidden = false;
  element.className = `message ${type}`;
  element.textContent = text;
}

function aiPayload() {
  return {
    provider: document.querySelector("#provider").value,
    base_url: document.querySelector("#base-url").value,
    model: document.querySelector("#model").value,
    api_key: document.querySelector("#api-key").value,
    remember_device: document.querySelector('input[name="storage"]:checked').value === "device",
    timeout_seconds: 60,
  };
}

document.addEventListener("DOMContentLoaded", () => {
  const chooseButton = document.querySelector("#choose-project");
  let selectedProject = null;

  function displayProject(project) {
    selectedProject = project;
    document.querySelector("#project-name").textContent = project.name;
    document.querySelector("#project-detail").textContent = `${project.path} · 约 ${project.file_count} 个文件`;
  }

  chooseButton?.addEventListener("click", async () => {
    const message = document.querySelector("#home-message");
    chooseButton.disabled = true;
    try {
      const payload = await requestJson("/api/local/select-folder", {method: "POST", body: "{}"});
      if (!payload.cancelled) displayProject(payload.project);
    } catch (error) {
      showMessage(message, error.message, "error");
    } finally {
      chooseButton.disabled = false;
    }
  });

  document.querySelector("#use-project-path")?.addEventListener("click", async () => {
    const message = document.querySelector("#home-message");
    try {
      const payload = await requestJson("/api/local/project-info", {
        method: "POST",
        body: JSON.stringify({project_path: document.querySelector("#project-path-input").value}),
      });
      displayProject(payload);
    } catch (error) {
      showMessage(message, error.message, "error");
    }
  });

  async function startRun(url, body) {
    const message = document.querySelector("#home-message");
    try {
      const payload = await requestJson(url, {method: "POST", body: JSON.stringify(body)});
      window.location.assign(payload.progress_url);
    } catch (error) {
      showMessage(message, error.message, "error");
    }
  }

  document.querySelector("#start-review")?.addEventListener("click", () => {
    if (!selectedProject) {
      showMessage(document.querySelector("#home-message"), "请先选择需要审查的项目文件夹。", "error");
      return;
    }
    const mode = document.querySelector('input[name="review-mode"]:checked').value;
    startRun("/api/runs", {project_path: selectedProject.path, mode});
  });
  document.querySelector("#run-ai-demo")?.addEventListener("click", () => startRun("/api/runs/demo", {mode: "ai"}));
  document.querySelector("#run-basic-demo")?.addEventListener("click", () => startRun("/api/runs/demo", {mode: "basic"}));

  const provider = document.querySelector("#provider");
  provider?.addEventListener("change", () => {
    const option = provider.selectedOptions[0];
    document.querySelector("#base-url").value = option.dataset.baseUrl || "";
    document.querySelector("#model").value = option.dataset.model || "";
  });

  document.querySelector("#test-ai")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const message = document.querySelector("#settings-message");
    button.disabled = true;
    showMessage(message, "正在连接真实模型，请稍候……");
    try {
      const result = await requestJson("/api/settings/ai/test", {method: "POST", body: JSON.stringify(aiPayload())});
      showMessage(message, result.ok ? `连接成功：${result.provider} · ${result.model} · ${result.latency_ms} ms，已实际收到模型响应。` : result.message, result.ok ? "success" : "error");
    } catch (error) {
      showMessage(message, error.message, "error");
    } finally {
      document.querySelector("#api-key").value = "";
      button.disabled = false;
    }
  });

  document.querySelector("#save-ai")?.addEventListener("click", async () => {
    const message = document.querySelector("#settings-message");
    try {
      const result = await requestJson("/api/settings/ai/save", {method: "POST", body: JSON.stringify(aiPayload())});
      showMessage(message, result.status === "connected" ? "设置已保存，连接测试仍然有效。" : "设置已保存，请测试连接后再运行 AI 审查。", "success");
    } catch (error) {
      showMessage(message, error.message, "error");
    } finally {
      document.querySelector("#api-key").value = "";
    }
  });

  const progressPage = document.querySelector("#progress-page");
  if (progressPage) {
    const runId = progressPage.dataset.runId;
    const poll = async () => {
      try {
        const status = await requestJson(`/api/runs/${runId}/status`);
        document.querySelector("#elapsed").textContent = status.elapsed_seconds;
        (status.steps || []).forEach((step) => {
          const item = document.querySelector(`[data-step="${step.key}"]`);
          if (item) item.className = step.status;
        });
        document.querySelector("#model-wait").hidden = !status.waiting_for_model;
        if (status.status === "completed") {
          window.location.reload();
          return;
        }
        if (status.status === "failed") {
          showMessage(document.querySelector("#run-error"), status.error_message, "error");
          return;
        }
        window.setTimeout(poll, 700);
      } catch (error) {
        showMessage(document.querySelector("#run-error"), error.message, "error");
      }
    };
    poll();
  }

  document.querySelector("#open-result-directory")?.addEventListener("click", async (event) => {
    try {
      await requestJson(`/api/runs/${event.currentTarget.dataset.runId}/open-directory`, {method: "POST", body: "{}"});
    } catch (error) {
      window.alert(error.message);
    }
  });

  document.querySelector(".rerun-ai")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const payload = await requestJson("/api/runs", {
        method: "POST",
        body: JSON.stringify({project_path: button.dataset.projectPath, mode: "ai"}),
      });
      window.location.assign(payload.progress_url);
    } catch (error) {
      window.alert(error.message);
      button.disabled = false;
    }
  });
});
