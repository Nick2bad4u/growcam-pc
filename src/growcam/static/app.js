const connection = document.querySelector("#connection");
const timelapseForm = document.querySelector("#timelapse-form");
const confirmDialog = document.querySelector("#confirm-dialog");
const previewVideo = document.querySelector("#timelapse-preview");
const historyVideo = document.querySelector("#history-player");
const historyScrubber = document.querySelector("#history-scrubber");
const historyWindow = document.querySelector("#history-window");
const liveFeed = document.querySelector("#live-feed");
const livePlaceholder = document.querySelector("#live-placeholder");
const liveMessage = document.querySelector("#live-message");
const tabButtons = [...document.querySelectorAll(".app-tab")];

let timelapseData = null;
let historyData = null;
let filesData = null;
let selectedHistoryIndex = -1;
let selectedHistorySeconds = null;
let historyPlaybackStartSeconds = null;
let pendingConfig = null;
let formIsDirty = false;
let currentPreviewRecording = null;
let previewRequest = null;
let historyRequest = null;
let appReady = false;
let historyLoaded = false;
let timelapseLoaded = false;
let filesLoaded = false;

function hexKibibytes(value) {
  return typeof value === "string" && value.startsWith("0x") ? Number.parseInt(value, 16) : 0;
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function formatInterval(seconds) {
  if (seconds % 3600 === 0) return `${seconds / 3600} hour${seconds === 3600 ? "" : "s"}`;
  if (seconds % 60 === 0) return `${seconds / 60} minute${seconds === 60 ? "" : "s"}`;
  return `${seconds} second${seconds === 1 ? "" : "s"}`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "accelerated";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}-second`;
  return `${(seconds / 60).toFixed(1)}-minute`;
}

function formatFileDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

function cameraDate(value) {
  return new Date(String(value).replace(" ", "T"));
}

function displayDate(value) {
  if (!value) return "—";
  return cameraDate(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function displayTime(value) {
  if (!value) return "—";
  return cameraDate(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function localDateValue(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function setStatus(element, message, kind = "") {
  element.textContent = message;
  element.className = `status${kind ? ` ${kind}` : ""}`;
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function stopMediaLoad(video) {
  video.pause();
  video.removeAttribute("src");
  video.load();
}

function loadVideoStream(video, url, signal) {
  return new Promise((resolve, reject) => {
    const removeReadyListeners = () => {
      video.removeEventListener("loadeddata", handleReady);
      video.removeEventListener("error", handleError);
    };
    const removeAllListeners = () => {
      removeReadyListeners();
      signal.removeEventListener("abort", handleAbort);
    };
    const handleReady = () => {
      removeReadyListeners();
      resolve();
    };
    const handleError = () => {
      removeAllListeners();
      reject(new Error("The browser could not start this camera preview."));
    };
    const handleAbort = () => {
      removeAllListeners();
      stopMediaLoad(video);
      reject(new DOMException("The media load was aborted.", "AbortError"));
    };
    video.addEventListener("loadeddata", handleReady, { once: true });
    video.addEventListener("error", handleError, { once: true });
    signal.addEventListener("abort", handleAbort, { once: true });
    video.preload = "auto";
    video.src = url;
    video.load();
    if (signal.aborted) handleAbort();
  });
}

function loadTimeLabel(startedAt) {
  const seconds = (performance.now() - startedAt) / 1000;
  return seconds < 10 ? `${seconds.toFixed(1)} seconds` : `${Math.round(seconds)} seconds`;
}

async function loadInfo() {
  const info = await getJson("/api/info");
  const system = info.system || {};
  const partition = info.storage?.[0]?.Partition?.[0] || {};
  const totalMib = hexKibibytes(partition.TotalSpace);
  const freeMib = hexKibibytes(partition.RemainSpace);
  document.querySelector("#device").textContent = `${info.login?.device_type || "IPC"} · ${system.DeviceModel || "GrowCam"}`;
  document.querySelector("#firmware").textContent = system.SoftWareVersion || "Firmware unknown";
  document.querySelector("#storage-total").textContent = formatSize(totalMib * 1024 ** 2);
  document.querySelector("#storage-free").textContent = formatSize(freeMib * 1024 ** 2);
  document.querySelector("#storage-percent").textContent = totalMib ? `${((freeMib / totalMib) * 100).toFixed(1)}% available` : "Capacity unavailable";
  document.querySelector("#storage-window").textContent = `${partition.OldStartTime || "—"} → ${partition.NewEndTime || "—"}`;
}

function timelinePercent(value) {
  return (secondsOfDay(value) / 86400) * 100;
}

function secondsOfDay(value) {
  const parsed = cameraDate(value);
  return parsed.getHours() * 3600 + parsed.getMinutes() * 60 + parsed.getSeconds();
}

function clockForSeconds(value) {
  const safeSeconds = Math.max(0, Math.min(86399, Math.round(value)));
  const clock = new Date(2000, 0, 1, 0, 0, safeSeconds);
  return clock.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

function isoForDaySeconds(date, value) {
  const safeSeconds = Math.max(0, Math.min(86399, Math.round(value)));
  const hours = String(Math.floor(safeSeconds / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((safeSeconds % 3600) / 60)).padStart(2, "0");
  const seconds = String(safeSeconds % 60).padStart(2, "0");
  return `${date}T${hours}:${minutes}:${seconds}`;
}

function recordingBounds(recording) {
  const start = secondsOfDay(recording.beginTime);
  const beginDate = localDateValue(cameraDate(recording.beginTime));
  const endDate = localDateValue(cameraDate(recording.endTime));
  const rawEnd = secondsOfDay(recording.endTime);
  const end = endDate > beginDate || rawEnd <= start ? 86400 : rawEnd;
  return { start, end };
}

function historyRecordIndexAt(seconds) {
  const recordings = historyData?.recordings || [];
  return recordings.findIndex((recording) => {
    const bounds = recordingBounds(recording);
    return seconds >= bounds.start && seconds < bounds.end;
  });
}

function nextHistoryPointAtOrAfter(seconds) {
  const recordings = historyData?.recordings || [];
  const containingIndex = historyRecordIndexAt(seconds);
  if (containingIndex >= 0) return { index: containingIndex, seconds };
  const nextIndex = recordings.findIndex((recording) => recordingBounds(recording).start >= seconds);
  if (nextIndex < 0) return null;
  return { index: nextIndex, seconds: recordingBounds(recordings[nextIndex]).start };
}

function historyWindowLabel() {
  if (historyWindow.value === "full") return "full recording block";
  return `${Number(historyWindow.value) / 60}-minute preview`;
}

function setScrubberPosition(seconds) {
  const safeSeconds = Math.max(0, Math.min(86399, Math.round(seconds)));
  historyScrubber.value = String(safeSeconds);
  document.querySelector("#history-scrubber-time").textContent = clockForSeconds(safeSeconds);
}

function highlightHistorySelection(index) {
  document.querySelectorAll(".timeline-segment, .segment-button").forEach((button) => button.classList.remove("selected"));
  if (index < 0) return;
  document.querySelectorAll(".timeline-segment")[index]?.classList.add("selected");
  document.querySelectorAll(".segment-button")[index]?.classList.add("selected");
}

function activateTab(name, focus = false, reveal = false) {
  const selectedButton = tabButtons.find((button) => button.dataset.tab === name) || tabButtons[0];
  for (const button of tabButtons) {
    const selected = button === selectedButton;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    document.querySelector(`#${button.getAttribute("aria-controls")}`).hidden = !selected;
  }
  const url = new URL(window.location.href);
  url.hash = selectedButton.dataset.tab === "live" ? "" : selectedButton.dataset.tab;
  window.history.replaceState(null, "", url);
  if (focus) selectedButton.focus();
  if (reveal) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  const selectedTab = selectedButton.dataset.tab;
  setLiveFeedActive(selectedTab === "live");
  if (selectedTab !== "rewind" && historyRequest) {
    historyRequest.abort();
  }
  if (selectedTab !== "timelapse" && previewRequest) {
    previewRequest.abort();
  }
  if (appReady) void ensureTabData(selectedTab);
}

function setLiveFeedActive(active) {
  if (active) {
    if (!liveFeed.hasAttribute("src")) {
      liveMessage.textContent = "Starting live video…";
      livePlaceholder.hidden = false;
      liveFeed.src = liveFeed.dataset.src;
    }
    return;
  }
  liveFeed.removeAttribute("src");
  liveMessage.textContent = "Live video paused.";
  livePlaceholder.hidden = false;
}

async function ensureTabData(name) {
  try {
    if (name === "rewind" && !historyLoaded) await loadHistory();
    if (name === "timelapse" && !timelapseLoaded) await loadTimelapse();
    if (name === "files" && !filesLoaded) await loadFiles();
  } catch (error) {
    if (name === "rewind") document.querySelector("#history-status").textContent = errorMessage(error);
    if (name === "timelapse") {
      setStatus(document.querySelector("#timelapse-state"), "Camera unavailable", "error");
      document.querySelector("#timelapse-file-status").textContent = errorMessage(error);
    }
    if (name === "files") document.querySelector("#files-status").textContent = errorMessage(error);
  }
}

function renderHistory(payload) {
  if (historyRequest) historyRequest.abort();
  historyRequest = null;
  stopMediaLoad(historyVideo);
  document.querySelector(".rewind-player").setAttribute("aria-busy", "false");
  document.querySelector("#history-placeholder").hidden = false;
  document.querySelector("#history-title").textContent = "Choose a segment below";
  document.querySelector("#history-download").hidden = true;
  historyData = payload;
  selectedHistoryIndex = -1;
  selectedHistorySeconds = null;
  historyPlaybackStartSeconds = null;
  const timeline = document.querySelector("#history-timeline");
  const list = document.querySelector("#history-list");
  const recordings = payload.recordings;
  timeline.replaceChildren();
  list.replaceChildren();
  document.querySelector("#history-day-label").textContent = cameraDate(`${payload.date}T12:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });

  recordings.forEach((recording, index) => {
    const start = timelinePercent(recording.beginTime);
    let end = timelinePercent(recording.endTime);
    if (end <= start) end = 100;
    const segment = document.createElement("button");
    segment.type = "button";
    segment.className = `timeline-segment${recording.active ? " active" : ""}`;
    segment.style.left = `${start}%`;
    segment.style.width = `${Math.max(end - start, 0.22)}%`;
    segment.style.zIndex = String(Math.max(1, 1000 - Math.round((end - start) * 100)));
    segment.title = `${displayTime(recording.beginTime)}–${displayTime(recording.endTime)} · ${formatSize(recording.sizeBytes)}`;
    segment.setAttribute("aria-label", `Play recording ${index + 1}, ${displayTime(recording.beginTime)} to ${displayTime(recording.endTime)}`);
    segment.addEventListener("click", () => selectHistorySegment(index, recordingBounds(recording).start));
    timeline.append(segment);

    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "segment-button";
    const icon = document.createElement("span");
    icon.className = "nf";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "";
    const begin = document.createElement("strong");
    begin.textContent = displayTime(recording.beginTime);
    const endLabel = document.createElement("span");
    endLabel.textContent = `→ ${displayTime(recording.endTime)}`;
    const size = document.createElement("small");
    size.textContent = formatSize(recording.sizeBytes);
    button.append(icon, begin, endLabel, size);
    button.addEventListener("click", () => selectHistorySegment(index, recordingBounds(recording).start));
    item.append(button);
    list.append(item);
  });

  const status = document.querySelector("#history-status");
  status.textContent = recordings.length ? `${recordings.length} recording block${recordings.length === 1 ? "" : "s"} · choose a time.` : "No recordings found for this day.";
  document.querySelector("#history-position").textContent = `— / ${recordings.length}`;
  document.querySelector("#history-previous").disabled = true;
  document.querySelector("#history-next").disabled = true;
  setScrubberPosition(recordings.length ? recordingBounds(recordings[0]).start : 0);
}

async function loadHistory() {
  const dateInput = document.querySelector("#history-date");
  const status = document.querySelector("#history-status");
  const timelineCard = document.querySelector(".timeline-card");
  timelineCard.setAttribute("aria-busy", "true");
  status.textContent = "Loading recordings…";
  try {
    const payload = await getJson(`/api/history?date=${encodeURIComponent(dateInput.value)}`);
    renderHistory(payload);
    historyLoaded = true;
  } finally {
    timelineCard.setAttribute("aria-busy", "false");
  }
}

function previewScrubberMoment() {
  const seconds = Number(historyScrubber.value);
  setScrubberPosition(seconds);
  const index = historyRecordIndexAt(seconds);
  highlightHistorySelection(index);
  const status = document.querySelector("#history-status");
  status.textContent = index >= 0 ? `Release to open ${clockForSeconds(seconds)}.` : `No footage at ${clockForSeconds(seconds)}.`;
}

function selectScrubberMoment() {
  const seconds = Number(historyScrubber.value);
  const index = historyRecordIndexAt(seconds);
  if (index >= 0) {
    selectHistorySegment(index, seconds);
    return;
  }
  if (historyRequest) historyRequest.abort();
  selectedHistoryIndex = -1;
  selectedHistorySeconds = seconds;
  historyPlaybackStartSeconds = null;
  highlightHistorySelection(-1);
  document.querySelector("#history-title").textContent = `${clockForSeconds(seconds)} · no recording`;
  document.querySelector("#history-position").textContent = `— / ${historyData?.recordings.length || 0}`;
  document.querySelector("#history-previous").disabled = true;
  document.querySelector("#history-next").disabled = true;
  document.querySelector("#history-status").textContent = `No footage at ${clockForSeconds(seconds)}.`;
}

function selectHistorySegment(index, atSeconds = null) {
  const recordings = historyData?.recordings || [];
  const recording = recordings[index];
  if (!recording) return;
  const bounds = recordingBounds(recording);
  const requestedSeconds = atSeconds === null ? bounds.start : Math.max(bounds.start, Math.min(bounds.end - 1, atSeconds));
  selectedHistoryIndex = index;
  selectedHistorySeconds = requestedSeconds;
  setScrubberPosition(requestedSeconds);
  highlightHistorySelection(index);
  document.querySelector("#history-title").textContent = `${clockForSeconds(requestedSeconds)} · ${historyWindowLabel()}`;
  document.querySelector("#history-position").textContent = `${index + 1} / ${recordings.length}`;
  document.querySelector("#history-previous").disabled = index <= 0;
  document.querySelector("#history-next").disabled = index >= recordings.length - 1;
  const download = document.querySelector("#history-download");
  download.href = `/api/download?file=${encodeURIComponent(recording.fileName)}`;
  download.hidden = recording.active;
  buildHistoryPreview(recording, requestedSeconds);
}

async function buildHistoryPreview(recording, atSeconds) {
  if (historyRequest) historyRequest.abort();
  historyRequest = new AbortController();
  const request = historyRequest;
  const status = document.querySelector("#history-status");
  const card = document.querySelector(".rewind-player");
  const parameters = new URLSearchParams({ file: recording.fileName });
  const bounds = recordingBounds(recording);
  const recordingDuration = Math.max(1, bounds.end - bounds.start);
  let estimatedBytes = recording.sizeBytes;
  if (historyWindow.value !== "full") {
    const duration = Number(historyWindow.value);
    parameters.set("at", isoForDaySeconds(historyData.date, atSeconds));
    parameters.set("duration", String(duration));
    estimatedBytes = recording.sizeBytes * (Math.min(duration, bounds.end - atSeconds) / recordingDuration);
  }
  historyPlaybackStartSeconds = atSeconds;
  card.setAttribute("aria-busy", "true");
  const url = `/api/history/preview?${parameters}`;
  const startedAt = performance.now();
  let playbackStarted = false;
  status.textContent = `Streaming about ${formatSize(estimatedBytes)}…`;
  try {
    await loadVideoStream(historyVideo, url, request.signal);
    if (historyRequest !== request) return;
    playbackStarted = true;
    document.querySelector("#history-placeholder").hidden = true;
    await historyVideo.play().catch(() => {});
    status.textContent = `Ready in ${loadTimeLabel(startedAt)} · cached when complete.`;
  } catch (error) {
    if (!(error instanceof DOMException && error.name === "AbortError")) status.textContent = errorMessage(error);
  } finally {
    if (historyRequest === request) {
      if (!playbackStarted) historyRequest = null;
      card.setAttribute("aria-busy", "false");
    }
  }
}

function fillTimelapseForm(config) {
  document.querySelector("#timelapse-enabled").checked = config.enabled;
  document.querySelector("#timelapse-interval-seconds").value = config.intervalSeconds;
  document.querySelector("#timelapse-start").value = config.startTime;
  document.querySelector("#timelapse-end").value = config.endTime;
  document.querySelector("#timelapse-daily-start").value = config.dailyStart;
  document.querySelector("#timelapse-daily-end").value = config.dailyEnd;
  formIsDirty = false;
  document.querySelector("#settings-status").textContent = "Review before applying.";
}

function renderTimelapse(payload, forceFormRefresh) {
  const config = payload.config;
  const state = document.querySelector("#timelapse-state");
  setStatus(state, config.active ? "Capturing" : config.enabled ? "Scheduled" : "Disabled", config.enabled ? "violet-status" : "pending");
  const progress = document.querySelector("#timelapse-progress");
  progress.value = config.progressPercent;
  progress.textContent = `${config.progressPercent}%`;
  document.querySelector("#timelapse-progress-label").textContent = `${config.progressPercent.toFixed(1)}% complete`;
  document.querySelector("#timelapse-captures").textContent = `~${config.estimatedCaptures.toLocaleString()} of ${config.expectedCaptures.toLocaleString()} captures`;
  document.querySelector("#timelapse-dates").textContent = `${displayDate(config.startTime)} → ${displayDate(config.endTime)}`;
  document.querySelector("#timelapse-interval").textContent = `One frame every ${formatInterval(config.intervalSeconds)} · daily ${config.dailyStart}–${config.dailyEnd}`;
  if (!formIsDirty || forceFormRefresh) fillTimelapseForm(config);
  renderTimelapseFiles(payload.recordings);
}

function renderTimelapseFiles(recordings) {
  const tbody = document.querySelector("#timelapse-list");
  const latestButton = document.querySelector("#preview-latest");
  tbody.replaceChildren();
  latestButton.disabled = recordings.length === 0;
  for (const recording of recordings) {
    const row = document.createElement("tr");
    if (recording.active) row.className = "active-file";
    const stateCell = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `file-state${recording.active ? " active" : ""}`;
    badge.textContent = recording.active ? "● In progress" : "✓ Complete";
    stateCell.append(badge);
    const throughCell = document.createElement("td");
    throughCell.textContent = recording.endTime || "—";
    const sizeCell = document.createElement("td");
    sizeCell.textContent = formatSize(recording.sizeBytes);
    const actionCell = document.createElement("td");
    actionCell.className = "row-actions";
    const preview = document.createElement("button");
    preview.type = "button";
    preview.className = "compact violet-button";
    preview.innerHTML = '<span class="nf" aria-hidden="true"></span> Preview';
    preview.addEventListener("click", () => buildPreview(recording));
    actionCell.append(preview);
    if (!recording.active) {
      const download = document.createElement("a");
      download.href = `/api/download?file=${encodeURIComponent(recording.fileName)}`;
      download.textContent = "Download MKV";
      actionCell.append(download);
    }
    row.append(stateCell, throughCell, sizeCell, actionCell);
    tbody.append(row);
  }
  document.querySelector("#timelapse-file-status").textContent = `${recordings.length} time-lapse file${recordings.length === 1 ? "" : "s"}.`;
}

async function loadTimelapse(forceFormRefresh = false) {
  const panel = document.querySelector("#panel-timelapse");
  panel.setAttribute("aria-busy", "true");
  setStatus(document.querySelector("#timelapse-state"), "Loading schedule", "pending");
  try {
    const payload = await getJson("/api/timelapse");
    timelapseData = payload;
    timelapseLoaded = true;
    renderTimelapse(payload, forceFormRefresh);
  } finally {
    panel.setAttribute("aria-busy", "false");
  }
}

function visibleCameraFiles() {
  const query = document.querySelector("#files-search").value.trim().toLocaleLowerCase();
  const kind = document.querySelector("#files-kind").value;
  const sort = document.querySelector("#files-sort").value;
  const files = [...(filesData?.files || [])].filter((file) => {
    const matchesKind = kind === "all" || file.kind === kind;
    const haystack = `${file.fileName} ${file.downloadName} ${file.beginTime} ${file.endTime} ${file.kind}`.toLocaleLowerCase();
    return matchesKind && (!query || haystack.includes(query));
  });
  files.sort((left, right) => {
    if (sort === "oldest") return cameraDate(left.beginTime) - cameraDate(right.beginTime);
    if (sort === "largest") return right.sizeBytes - left.sizeBytes;
    return cameraDate(right.beginTime) - cameraDate(left.beginTime);
  });
  return files;
}

function fileTypeBadge(file) {
  const badge = document.createElement("span");
  badge.className = `file-kind ${file.kind}`;
  const icon = document.createElement("span");
  icon.className = "nf";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = file.kind === "timelapse" ? "󰔚" : "";
  const label = document.createElement("span");
  label.textContent = file.kind === "timelapse" ? "Time-lapse" : "Recording";
  badge.append(icon, label);
  return badge;
}

async function previewCameraFile(file) {
  const status = document.querySelector("#files-status");
  status.hidden = false;
  status.textContent = "Opening preview…";
  try {
    if (file.kind === "timelapse") {
      if (!timelapseLoaded) await loadTimelapse();
      activateTab("timelapse", false, true);
      await buildPreview(file);
      return;
    }
    const historyDate = file.beginTime.slice(0, 10);
    document.querySelector("#history-date").value = historyDate;
    historyLoaded = false;
    await loadHistory();
    const index = historyData?.recordings.findIndex((item) => item.fileName === file.fileName) ?? -1;
    if (index < 0) throw new Error("This recording is no longer present in the camera index.");
    activateTab("rewind", false, true);
    selectHistorySegment(index, recordingBounds(historyData.recordings[index]).start);
  } catch (error) {
    status.textContent = errorMessage(error);
  }
}

function renderFiles() {
  const files = visibleCameraFiles();
  const tbody = document.querySelector("#files-list");
  const status = document.querySelector("#files-status");
  tbody.replaceChildren();
  for (const file of files) {
    const row = document.createElement("tr");
    if (file.active) row.className = "active-file";
    row.title = file.fileName;
    const typeCell = document.createElement("td");
    typeCell.append(fileTypeBadge(file));
    const startedCell = document.createElement("td");
    startedCell.textContent = displayDate(file.beginTime);
    const durationCell = document.createElement("td");
    durationCell.textContent = formatFileDuration((cameraDate(file.endTime) - cameraDate(file.beginTime)) / 1000);
    const sizeCell = document.createElement("td");
    sizeCell.textContent = formatSize(file.sizeBytes);
    const stateCell = document.createElement("td");
    const state = document.createElement("span");
    state.className = `file-state${file.active ? " active" : ""}`;
    state.textContent = file.active ? "Recording" : "Closed";
    stateCell.append(state);
    const actionCell = document.createElement("td");
    actionCell.className = "row-actions";
    const preview = document.createElement("button");
    preview.type = "button";
    preview.className = "compact";
    preview.title = `Preview ${file.downloadName}`;
    preview.innerHTML = '<span class="nf" aria-hidden="true"></span> Preview';
    preview.addEventListener("click", () => void previewCameraFile(file));
    actionCell.append(preview);
    if (file.downloadable) {
      const download = document.createElement("a");
      download.className = "download-button";
      download.href = `/api/download?file=${encodeURIComponent(file.fileName)}`;
      download.download = file.downloadName;
      download.innerHTML = '<span class="nf" aria-hidden="true"></span> Download';
      actionCell.append(download);
    }
    row.append(typeCell, startedCell, durationCell, sizeCell, stateCell, actionCell);
    tbody.append(row);
  }
  const recordings = files.filter((file) => file.kind === "recording").length;
  const timelapses = files.length - recordings;
  document.querySelector("#files-count").textContent = `${files.length} file${files.length === 1 ? "" : "s"}`;
  document.querySelector("#files-breakdown").textContent = `${recordings} recordings · ${timelapses} time-lapses`;
  document.querySelector("#files-size").textContent = formatSize(files.reduce((total, file) => total + file.sizeBytes, 0));
  status.hidden = files.length > 0;
  status.textContent = filesData?.files.length ? "No files match these filters." : "No files found for this day.";
}

async function loadFiles() {
  const panel = document.querySelector("#panel-files");
  const status = document.querySelector("#files-status");
  panel.setAttribute("aria-busy", "true");
  status.hidden = false;
  status.textContent = "Loading camera files…";
  try {
    filesData = await getJson(`/api/files?date=${encodeURIComponent(document.querySelector("#files-date").value)}`);
    filesLoaded = true;
    renderFiles();
  } finally {
    panel.setAttribute("aria-busy", "false");
  }
}

function readTimelapseForm() {
  const config = {
    enabled: document.querySelector("#timelapse-enabled").checked,
    intervalSeconds: document.querySelector("#timelapse-interval-seconds").valueAsNumber,
    startTime: document.querySelector("#timelapse-start").value,
    endTime: document.querySelector("#timelapse-end").value,
    dailyStart: document.querySelector("#timelapse-daily-start").value,
    dailyEnd: document.querySelector("#timelapse-daily-end").value,
  };
  if (new Date(config.endTime) <= new Date(config.startTime)) throw new Error("End must be after start.");
  if (config.dailyEnd <= config.dailyStart) throw new Error("Daily end must be after daily start.");
  return config;
}

function addSummaryItem(summary, label, value) {
  const term = document.createElement("dt");
  term.textContent = label;
  const detail = document.createElement("dd");
  detail.textContent = value;
  summary.append(term, detail);
}

function showChangeReview(config) {
  const summary = document.querySelector("#change-summary");
  summary.replaceChildren();
  addSummaryItem(summary, "Recording", config.enabled ? "Enabled" : "Disabled");
  addSummaryItem(summary, "Interval", formatInterval(config.intervalSeconds));
  addSummaryItem(summary, "Schedule", `${displayDate(config.startTime)} → ${displayDate(config.endTime)}`);
  addSummaryItem(summary, "Daily window", `${config.dailyStart} → ${config.dailyEnd}`);
  confirmDialog.showModal();
}

async function applySettings() {
  if (!pendingConfig || !timelapseData) return;
  const button = document.querySelector("#apply-settings");
  const status = document.querySelector("#settings-status");
  button.disabled = true;
  button.textContent = "Applying…";
  status.textContent = "Applying and verifying…";
  try {
    const response = await fetch("/api/timelapse", { method: "POST", headers: { "Content-Type": "application/json", "X-GrowCam-Request": "1" }, body: JSON.stringify({ expectedRevision: timelapseData.config.revision, config: pendingConfig }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(`${payload.error || `Request failed (${response.status})`}${payload.rollbackVerified === true ? " The previous schedule was restored." : ""}`);
    confirmDialog.close();
    pendingConfig = null;
    formIsDirty = false;
    await loadTimelapse(true);
    status.textContent = "Schedule applied.";
  } catch (error) {
    status.textContent = errorMessage(error);
  } finally {
    button.disabled = false;
    button.innerHTML = '<span class="nf" aria-hidden="true"></span> Apply to camera';
  }
}

async function buildPreview(recording) {
  if (previewRequest) previewRequest.abort();
  previewRequest = new AbortController();
  const request = previewRequest;
  const status = document.querySelector("#preview-status");
  const card = document.querySelector(".preview-card");
  const latestButton = document.querySelector("#preview-latest");
  const fileButtons = document.querySelectorAll("#timelapse-list button");
  currentPreviewRecording = recording;
  latestButton.disabled = true;
  fileButtons.forEach((button) => { button.disabled = true; });
  document.querySelector("#preview-title").textContent = recording.active ? "Opening progress" : "Opening preview";
  status.textContent = `Streaming ${formatSize(recording.sizeBytes)}…`;
  card.setAttribute("aria-busy", "true");
  const url = `/api/timelapse/preview?file=${encodeURIComponent(recording.fileName)}`;
  const save = document.querySelector("#save-preview");
  save.href = `${url}&download=1`;
  save.download = `growcam-timelapse-preview-${recording.beginTime.slice(0, 10)}.mp4`;
  save.hidden = false;
  const startedAt = performance.now();
  let playbackStarted = false;
  try {
    await loadVideoStream(previewVideo, url, request.signal);
    if (previewRequest !== request) return;
    playbackStarted = true;
    document.querySelector("#preview-placeholder").hidden = true;
    document.querySelector("#preview-title").textContent = recording.active ? "Progress so far" : "Timelapse preview";
    await previewVideo.play().catch(() => {});
    status.textContent = `Ready in ${loadTimeLabel(startedAt)} · ${displayDate(recording.beginTime)} → ${displayDate(recording.endTime)}.`;
  } catch (error) {
    if (!(error instanceof DOMException && error.name === "AbortError")) {
      document.querySelector("#preview-title").textContent = "Preview unavailable";
      status.textContent = errorMessage(error);
    }
  } finally {
    if (previewRequest === request) {
      if (!playbackStarted) previewRequest = null;
      card.removeAttribute("aria-busy");
      latestButton.disabled = timelapseData?.recordings.length === 0;
      fileButtons.forEach((button) => { button.disabled = false; });
    }
  }
}

async function refresh() {
  setStatus(connection, "Connecting", "pending");
  try {
    await loadInfo();
    setStatus(connection, "Connected locally");
    appReady = true;
    const activeTab = tabButtons.find((button) => button.getAttribute("aria-selected") === "true");
    await ensureTabData(activeTab?.dataset.tab || "live");
  } catch (error) {
    setStatus(connection, "Connection error", "error");
    document.querySelector("#history-status").textContent = errorMessage(error);
  }
}

timelapseForm.addEventListener("input", () => {
  formIsDirty = true;
  document.querySelector("#settings-status").textContent = "Unsaved schedule changes.";
});
timelapseForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!timelapseForm.reportValidity()) return;
  try {
    pendingConfig = readTimelapseForm();
    showChangeReview(pendingConfig);
  } catch (error) {
    document.querySelector("#settings-status").textContent = error.message;
  }
});

document.querySelector("#history-date").value = localDateValue();
document.querySelector("#history-date").max = localDateValue();
document.querySelector("#history-date").addEventListener("change", () => {
  historyLoaded = false;
  void ensureTabData("rewind");
});
document.querySelector("#history-today").addEventListener("click", () => {
  document.querySelector("#history-date").value = localDateValue();
  historyLoaded = false;
  void ensureTabData("rewind");
});
document.querySelector("#files-date").value = localDateValue();
document.querySelector("#files-date").max = localDateValue();
document.querySelector("#files-date").addEventListener("change", () => {
  filesLoaded = false;
  void ensureTabData("files");
});
document.querySelector("#files-refresh").addEventListener("click", () => {
  filesLoaded = false;
  void ensureTabData("files");
});
document.querySelector("#files-search").addEventListener("input", renderFiles);
document.querySelector("#files-kind").addEventListener("change", renderFiles);
document.querySelector("#files-sort").addEventListener("change", renderFiles);
document.querySelector("#history-previous").addEventListener("click", () => {
  const index = selectedHistoryIndex - 1;
  const recording = historyData?.recordings[index];
  if (recording) selectHistorySegment(index, recordingBounds(recording).start);
});
document.querySelector("#history-next").addEventListener("click", () => {
  const index = selectedHistoryIndex + 1;
  const recording = historyData?.recordings[index];
  if (recording) selectHistorySegment(index, recordingBounds(recording).start);
});
historyScrubber.addEventListener("input", previewScrubberMoment);
historyScrubber.addEventListener("change", selectScrubberMoment);
historyWindow.addEventListener("change", () => {
  if (selectedHistoryIndex >= 0 && selectedHistorySeconds !== null) selectHistorySegment(selectedHistoryIndex, selectedHistorySeconds);
});
historyVideo.addEventListener("timeupdate", () => {
  if (historyPlaybackStartSeconds === null) return;
  setScrubberPosition(historyPlaybackStartSeconds + historyVideo.currentTime);
});
historyVideo.addEventListener("ended", () => {
  if (document.querySelector("#panel-rewind").hidden || !document.querySelector("#history-continue").checked || selectedHistoryIndex < 0 || historyPlaybackStartSeconds === null) return;
  const recordings = historyData?.recordings || [];
  if (historyWindow.value === "full") {
    const nextIndex = selectedHistoryIndex + 1;
    const recording = recordings[nextIndex];
    if (recording) selectHistorySegment(nextIndex, recordingBounds(recording).start);
    return;
  }
  const selectedRecording = recordings[selectedHistoryIndex];
  if (!selectedRecording) return;
  const nextSeconds = Math.min(historyPlaybackStartSeconds + Number(historyWindow.value), recordingBounds(selectedRecording).end);
  const nextPoint = nextHistoryPointAtOrAfter(nextSeconds);
  if (nextPoint) selectHistorySegment(nextPoint.index, nextPoint.seconds);
});
document.querySelector("#apply-settings").addEventListener("click", applySettings);
document.querySelector("#refresh-timelapse").addEventListener("click", async () => {
  timelapseLoaded = false;
  try {
    await loadTimelapse(true);
  } catch (error) {
    setStatus(document.querySelector("#timelapse-state"), "Camera unavailable", "error");
    document.querySelector("#timelapse-file-status").textContent = errorMessage(error);
  }
});
document.querySelector("#preview-latest").addEventListener("click", async () => {
  try {
    await loadTimelapse();
    const latest = timelapseData?.recordings[0];
    if (latest) await buildPreview(latest);
  } catch (error) {
    document.querySelector("#preview-title").textContent = "Preview unavailable";
    document.querySelector("#preview-status").textContent = errorMessage(error);
  }
});
document.querySelector("#timelapse-speed").addEventListener("change", (event) => {
  previewVideo.defaultPlaybackRate = Number(event.target.value);
  previewVideo.playbackRate = Number(event.target.value);
});
previewVideo.addEventListener("ended", () => {
  if (!currentPreviewRecording) return;
  document.querySelector("#preview-status").textContent = `${formatDuration(previewVideo.duration)} video covering ${displayDate(currentPreviewRecording.beginTime)} → ${displayDate(currentPreviewRecording.endTime)} · complete preview cached locally.`;
});

for (const button of tabButtons) {
  button.addEventListener("click", () => activateTab(button.dataset.tab, false, true));
  button.addEventListener("keydown", (event) => {
    const currentIndex = tabButtons.indexOf(button);
    let nextIndex = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % tabButtons.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabButtons.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    activateTab(tabButtons[nextIndex].dataset.tab, true, true);
  });
}

window.addEventListener("hashchange", () => activateTab(window.location.hash.slice(1) || "live"));

window.addEventListener("beforeunload", () => {
  if (previewRequest) previewRequest.abort();
  if (historyRequest) historyRequest.abort();
});

liveFeed.addEventListener("load", () => {
  livePlaceholder.hidden = true;
});
liveFeed.addEventListener("error", () => {
  liveMessage.textContent = "Live feed unavailable. Check the camera connection and FFmpeg.";
  livePlaceholder.hidden = false;
});

activateTab(window.location.hash.slice(1) || "live");
refresh();
