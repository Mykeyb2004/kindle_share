"use strict";

const jobsContainer = document.getElementById("conversion-jobs");
const message = document.getElementById("page-message");
let jobs = JSON.parse(document.getElementById("initial-jobs").textContent);
let polling = false;

function showMessage(text) {
  message.textContent = text;
  message.hidden = false;
}

function renderJobs() {
  document.getElementById("no-jobs").hidden = jobs.length > 0;
  jobsContainer.replaceChildren();
  for (const job of jobs) {
    const item = document.createElement("div");
    item.className = `job ${job.status}`;
    const title = document.createElement("p");
    title.className = "job-name";
    title.textContent = job.name;
    const detail = document.createElement("p");
    detail.className = "job-status";
    const label = { queued: "排队中", running: "转换中", done: "已完成", failed: "转换失败" }[job.status];
    detail.textContent = `${label} · ${job.message}`;
    item.append(title, detail);
    if (job.status === "running") {
      const progress = document.createElement("progress");
      progress.setAttribute("aria-label", "正在转换");
      item.append(progress);
    }
    if (job.status === "done") {
      const download = document.createElement("a");
      download.textContent = "下载 MOBI ↓";
      download.href = `/download/${job.output.split("/").map(encodeURIComponent).join("/")}`;
      item.append(download);
    }
    jobsContainer.append(item);
  }
  for (const button of document.querySelectorAll(".convert-button")) {
    const job = jobs.find((entry) => entry.source === button.dataset.path);
    if (job?.status === "queued" || job?.status === "running") {
      button.disabled = true;
      button.textContent = job.status === "queued" ? "已加入队列" : "正在转换…";
    }
  }
}

async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    const response = await fetch("/admin/jobs", { cache: "no-store" });
    if (!response.ok) throw new Error("暂时无法获取转换状态，请检查服务是否仍在运行。");
    const previous = jobs;
    jobs = (await response.json()).jobs;
    renderJobs();
    const finished = previous.some((job) => ["queued", "running"].includes(job.status)) &&
      !jobs.some((job) => ["queued", "running"].includes(job.status));
    if (finished) {
      window.location.reload();
      return;
    }
    if (jobs.some((job) => ["queued", "running"].includes(job.status))) {
      window.setTimeout(pollJobs, 1200);
    }
  } catch (error) {
    showMessage(`${error.message} 刷新页面可重新获取状态。`);
  } finally {
    polling = false;
  }
}

for (const button of document.querySelectorAll(".convert-button")) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "正在提交…";
    message.hidden = true;
    try {
      const response = await fetch("/admin/convert", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content,
        },
        body: JSON.stringify({ path: button.dataset.path }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "无法开始转换，请刷新页面后重试。");
      jobs = [data.job, ...jobs.filter((job) => job.id !== data.job.id)];
      renderJobs();
      void pollJobs();
    } catch (error) {
      showMessage(error.message);
      button.disabled = false;
      button.textContent = "重试转换 →";
    }
  });
}

document.getElementById("copy-address").addEventListener("click", async (event) => {
  const input = document.getElementById("kindle-address");
  const button = event.currentTarget;
  try {
    await navigator.clipboard.writeText(input.value);
    button.textContent = "地址已复制 ✓";
  } catch {
    input.focus();
    input.select();
    button.textContent = "地址已选中，请按 Ctrl/Cmd+C";
  }
});

renderJobs();
if (jobs.some((job) => ["queued", "running"].includes(job.status))) void pollJobs();
