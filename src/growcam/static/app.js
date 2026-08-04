const connection = document.querySelector("#connection");
const timelapseForm = document.querySelector("#timelapse-form");
const appSettingsForm = document.querySelector("#app-settings-form");
const confirmDialog = document.querySelector("#confirm-dialog");
const cacheClearDialog = document.querySelector("#cache-clear-dialog");
const previewVideo = document.querySelector("#timelapse-preview");
const historyVideo = document.querySelector("#history-player");
const historyScrubber = document.querySelector("#history-scrubber");
const historyWindow = document.querySelector("#history-window");
const liveFeed = document.querySelector("#live-feed");
const liveAudio = document.querySelector("#live-audio");
const livePlaceholder = document.querySelector("#live-placeholder");
const liveMessage = document.querySelector("#live-message");
const liveQualityButtons = [...document.querySelectorAll("[data-live-quality]")];
const cameraControlRetry = document.querySelector("#camera-control-retry");
const tabButtons = [...document.querySelectorAll(".app-tab")];
const cameraControlTabs = new Set(["rewind", "timelapse", "files"]);

let timelapseData = null;
let historyData = null;
let filesData = null;
let appSettingsData = null;
let selectedHistoryIndex = -1;
let selectedHistorySeconds = null;
let historyPlaybackStartSeconds = null;
let pendingConfig = null;
let formIsDirty = false;
let appSettingsDirty = false;
let currentPreviewRecording = null;
let previewRequest = null;
let historyRequest = null;
let appReady = false;
let historyLoaded = false;
let timelapseLoaded = false;
let filesLoaded = false;
let historyLoadPromise = null;
let timelapseLoadPromise = null;
let filesLoadPromise = null;
let historyPreviewOpening = false;
let cameraControlAvailable = false;
let cameraControlState = null;
let liveQuality = storedLiveQuality();
let liveRestartTimer = null;
let livePaused = false;

const nativeHevcSupported = browserSupportsNativeHevc();
const timelapseStreamToCacheScale = 2 / 25;

function browserSupportsNativeHevc() {
  const probe = document.createElement("video");
  // Rewind previews are assigned directly to <video src>; MediaSource support
  // and support for an arbitrary profile-level string are not sufficient. Some
  // Chromium builds report a specific HEVC level as playable while rejecting
  // the camera's actual stream. Require generic hvc1 decoding support instead.
  return probe.canPlayType('video/mp4; codecs="hvc1"') !== "";
}

function hexMebibytes(value) {
  return typeof value === "string" && value.startsWith("0x") ? Number.parseInt(value, 16) : 0;
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function formatCacheSize(bytes) {
  return bytes === 0 ? "0 B" : formatSize(bytes);
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

function displayTime(value, includeSeconds = false) {
  if (!value) return "—";
  const options = { hour: "numeric", minute: "2-digit" };
  if (includeSeconds) options.second = "2-digit";
  return cameraDate(value).toLocaleTimeString([], options);
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

function storedLiveQuality() {
  try {
    return window.localStorage.getItem("growcam-live-quality") === "sd" ? "sd" : "fhd";
  } catch {
    return "fhd";
  }
}

function rememberLiveQuality() {
  try {
    window.localStorage.setItem("growcam-live-quality", liveQuality);
  } catch {
    // Private browsing or a locked-down browser may disable local storage.
  }
}

function updateLiveQualityButtons(disabled = false) {
  for (const button of liveQualityButtons) {
    const selected = button.dataset.liveQuality === liveQuality;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
    button.disabled = disabled;
  }
}

function liveStreamUrl() {
  return `${liveFeed.dataset.src}?quality=${encodeURIComponent(liveQuality)}`;
}

class HttpError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.payload = payload;
  }
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) {
    throw new HttpError(payload.error || `Request failed (${response.status})`, response.status, payload);
  }
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

function delayWithSignal(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const handleAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("The media load was aborted.", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", handleAbort, { once: true });
    if (signal.aborted) handleAbort();
  });
}

async function previewCacheState(url, signal) {
  const probeUrl = new URL(url, window.location.href);
  probeUrl.searchParams.set("cacheOnly", "1");
  const response = await fetch(probeUrl, {
    cache: "no-store",
    headers: { Range: "bytes=0-0" },
    signal,
  });
  if (response.status === 206) {
    await response.arrayBuffer();
    return "ready";
  }
  if (response.status === 202) {
    return "building";
  }
  if (response.status === 404) {
    return "missing";
  }
  if (!response.ok) throw new Error(`Preview cache check failed (${response.status}).`);
  const payload = await response.json();
  return payload.building ? "building" : "missing";
}

async function promoteCachedVideo(video, url, request, isCurrent, timestampScale, onReady) {
  while (!request.signal.aborted && isCurrent()) {
    const state = await previewCacheState(url, request.signal);
    if (state === "ready") {
      const resumeAt = Math.max(0, video.currentTime * timestampScale);
      const shouldResume = !video.paused && !video.ended;
      await loadVideoStream(video, url, request.signal);
      if (!isCurrent()) return;
      if (Number.isFinite(video.duration) && video.duration > 0) {
        video.currentTime = Math.min(resumeAt, Math.max(0, video.duration - 0.05));
      }
      if (shouldResume) await video.play().catch(() => {});
      onReady();
      return;
    }
    if (state === "missing") return;
    await delayWithSignal(750, request.signal);
  }
}

function loadTimeLabel(startedAt) {
  const seconds = (performance.now() - startedAt) / 1000;
  return seconds < 10 ? `${seconds.toFixed(1)} seconds` : `${Math.round(seconds)} seconds`;
}

function renderCameraInfo(info) {
  const system = info.system || {};
  const partition = info.storage?.[0]?.Partition?.[0] || {};
  const totalMib = hexMebibytes(partition.TotalSpace);
  const freeMib = hexMebibytes(partition.RemainSpace);
  // The C4 exposes the recording allocation as two-thirds of the card. Its
  // hidden one-third time-lapse reserve is therefore half that reported size.
  const estimatedTimelapseMib = totalMib / 2;
  document.querySelector("#device").textContent = `${info.login?.device_type || "IPC"} · ${system.DeviceModel || "GrowCam"}`;
  document.querySelector("#firmware").textContent = system.SoftWareVersion || "Firmware unknown";
  document.querySelector("#storage-total").textContent = formatSize(totalMib * 1024 ** 2);
  document.querySelector("#storage-free").textContent = formatSize(freeMib * 1024 ** 2);
  document.querySelector("#storage-percent").textContent = totalMib ? `${((freeMib / totalMib) * 100).toFixed(1)}% available` : "Capacity unavailable";
  document.querySelector("#storage-window").textContent = `${partition.OldStartTime || "—"} → ${partition.NewEndTime || "—"}`;
  document.querySelector("#timelapse-storage-estimate").textContent = formatSize(estimatedTimelapseMib * 1024 ** 2);
  document.querySelector("#timelapse-storage-note").textContent = totalMib
    ? "Estimated 1/3 card allocation · free space unavailable"
    : "Estimate unavailable";
  cameraControlAvailable = true;
  cameraControlState = info.cameraControl || { status: "available", available: true };
  updateCameraControlTabIndicators(false);
  updateCameraControlRetry();
  document.querySelector("#camera-control-note").hidden = true;
}

async function loadInfo() {
  renderCameraInfo(await getJson("/api/info"));
}

function cameraControlStateFromError(error) {
  const state = error instanceof HttpError ? error.payload?.cameraControl : null;
  return state && typeof state === "object" ? state : null;
}

function updateCameraControlTabIndicators(unavailable) {
  for (const button of tabButtons) {
    if (!cameraControlTabs.has(button.dataset.tab)) continue;
    button.classList.toggle("control-unavailable", unavailable);
    if (unavailable) {
      button.title = "Camera controls are paused; this tab will not contact the camera.";
    } else {
      button.removeAttribute("title");
    }
  }
}

function updateCameraControlRetry() {
  const locked = cameraControlState?.locked === true;
  const retryAllowed = !locked && cameraControlState?.retryAllowed === true;
  const retryExhausted = cameraControlState?.circuitOpen === true && !locked && !retryAllowed;
  cameraControlRetry.disabled = !retryAllowed;
  cameraControlRetry.innerHTML = locked
    ? '<span class="nf" aria-hidden="true"></span> Retry disabled after Ret=205'
    : retryExhausted
      ? '<span class="nf" aria-hidden="true">󰌾</span> Retry disabled for this run'
      : '<span class="nf" aria-hidden="true">󰑓</span> Retry camera controls';
  if (locked) {
    cameraControlRetry.title = "This server will not retry a locked camera account. Restart GrowCam after unlocking it.";
  } else if (retryExhausted) {
    cameraControlRetry.title = "GrowCam will not make another login attempt in this server run.";
  } else {
    cameraControlRetry.removeAttribute("title");
  }
}

function renderProtectedTabUnavailable(name) {
  let guidance = "Camera controls are paused. Use Retry camera controls above when you are ready.";
  if (cameraControlState?.locked) {
    guidance = "Camera reported an account lock. Unlock or reset it, then restart GrowCam.";
  } else if (cameraControlState?.retryAllowed === false) {
    guidance = "No more logins will be attempted in this server run. Verify access, then restart GrowCam.";
  }
  if (name === "rewind") document.querySelector("#history-status").textContent = guidance;
  if (name === "timelapse") {
    document.querySelector("#panel-timelapse").setAttribute("aria-busy", "false");
    setStatus(document.querySelector("#timelapse-state"), "Controls paused", "warning");
    document.querySelector("#timelapse-file-status").textContent = guidance;
  }
  if (name === "files") {
    document.querySelector("#panel-files").setAttribute("aria-busy", "false");
    document.querySelector("#files-status").textContent = guidance;
  }
}

function renderCameraControlUnavailable(error) {
  cameraControlAvailable = false;
  cameraControlState = cameraControlStateFromError(error) || {
    status: "blocked",
    available: false,
    circuitOpen: true,
    retryAllowed: true,
    locked: false,
  };
  historyLoaded = false;
  timelapseLoaded = false;
  filesLoaded = false;
  document.querySelector("#device").textContent = "Live stream only";
  document.querySelector("#firmware").textContent = "Camera control login unavailable";
  document.querySelector("#storage-total").textContent = "Unavailable";
  document.querySelector("#storage-free").textContent = "Unavailable";
  document.querySelector("#storage-percent").textContent = "DVRIP access required";
  document.querySelector("#storage-window").textContent = "DVRIP access required";
  document.querySelector("#timelapse-storage-estimate").textContent = "Unavailable";
  document.querySelector("#timelapse-storage-note").textContent = "DVRIP access required";

  const note = document.querySelector("#camera-control-note");
  const detail = String(cameraControlState.message || errorMessage(error)).replace(/[.\s]+$/, "");
  let nextStep = "Automatic DVRIP attempts are paused. Use the explicit retry button when you want to try once more.";
  if (cameraControlState.locked) {
    nextStep = "This server will not attempt another login after Ret=205. Unlock or reset the camera, then restart GrowCam.";
  } else if (cameraControlState.retryAllowed === false) {
    nextStep = "This server will not make another login attempt. Verify camera access, then restart GrowCam.";
  }
  document.querySelector("#camera-control-error").textContent = `${detail}. ${nextStep} Live video remains independent over RTSP.`;
  updateCameraControlTabIndicators(true);
  updateCameraControlRetry();
  for (const name of cameraControlTabs) renderProtectedTabUnavailable(name);
  note.hidden = false;
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
  const end = endDate > beginDate ? 86400 : Math.max(start, rawEnd);
  return { start, end };
}

function historyRecordIndexAt(seconds) {
  const recordings = historyData?.recordings || [];
  return recordings.findIndex((recording) => {
    const bounds = recordingBounds(recording);
    return seconds >= bounds.start && seconds < bounds.end;
  });
}

function nextHistoryPointAfter(index, seconds) {
  const recordings = historyData?.recordings || [];
  for (let nextIndex = index; nextIndex < recordings.length; nextIndex += 1) {
    const bounds = recordingBounds(recordings[nextIndex]);
    if (seconds < bounds.end) {
      return { index: nextIndex, seconds: Math.max(seconds, bounds.start) };
    }
  }
  return null;
}

function historyWindowLabel(availableSeconds = null) {
  if (historyWindow.value === "full") return "full recording block";
  const configuredSeconds = Number(historyWindow.value);
  const previewSeconds = availableSeconds === null
    ? configuredSeconds
    : Math.min(configuredSeconds, Math.max(1, availableSeconds));
  if (previewSeconds < 60) return `${Math.round(previewSeconds)}-second preview`;
  return `${previewSeconds / 60}-minute preview`;
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

function cancelLiveRestart() {
  if (liveRestartTimer === null) return;
  window.clearTimeout(liveRestartTimer);
  liveRestartTimer = null;
}

function startLiveFeed() {
  if (livePaused || document.querySelector("#panel-live").hidden || liveFeed.hasAttribute("src")) return;
  liveMessage.textContent = `Starting ${liveQuality.toUpperCase()} live video…`;
  document.querySelector("#live-resolution").textContent = `${liveQuality.toUpperCase()} · connecting`;
  livePlaceholder.hidden = false;
  liveFeed.src = liveStreamUrl();
  updateLiveQualityButtons(false);
}

function updateLivePauseState() {
  const button = document.querySelector("#live-pause-toggle");
  button.innerHTML = livePaused
    ? '<span class="nf" aria-hidden="true"></span> Resume live'
    : '<span class="nf" aria-hidden="true"></span> Pause live';
  button.setAttribute("aria-pressed", String(livePaused));
  document.querySelector("#live-state-label").classList.toggle("paused", livePaused);
  document.querySelector("#live-state-text").textContent = livePaused ? "PAUSED" : "LIVE";
  document.querySelector("#live-audio-toggle").disabled = livePaused;
}

function stopLiveFeed(message) {
  cancelLiveRestart();
  liveFeed.removeAttribute("src");
  stopLiveAudio();
  liveMessage.textContent = message;
  document.querySelector("#live-resolution").textContent = `${liveQuality.toUpperCase()} · paused`;
  livePlaceholder.hidden = false;
  updateLiveQualityButtons(false);
}

function restartLiveFeed() {
  cancelLiveRestart();
  liveFeed.removeAttribute("src");
  if (livePaused) {
    liveMessage.textContent = "Live video paused.";
    document.querySelector("#live-resolution").textContent = `${liveQuality.toUpperCase()} · paused`;
    livePlaceholder.hidden = false;
    updateLiveQualityButtons(false);
    return;
  }
  liveMessage.textContent = `Switching to ${liveQuality.toUpperCase()}…`;
  livePlaceholder.hidden = false;
  updateLiveQualityButtons(true);
  if (document.querySelector("#panel-live").hidden) {
    updateLiveQualityButtons(false);
    return;
  }
  // Let the browser cancel the old MJPEG response so its FFmpeg/RTSP process
  // exits before the selected camera stream is opened.
  liveRestartTimer = window.setTimeout(() => {
    liveRestartTimer = null;
    startLiveFeed();
  }, 350);
}

function selectLiveQuality(quality) {
  if (quality === liveQuality || !["sd", "fhd"].includes(quality)) return;
  liveQuality = quality;
  rememberLiveQuality();
  updateLiveQualityButtons(true);
  restartLiveFeed();
}

function setLiveFeedActive(active) {
  if (active) {
    if (livePaused) {
      liveMessage.textContent = "Live video paused.";
      livePlaceholder.hidden = false;
      return;
    }
    startLiveFeed();
    return;
  }
  stopLiveFeed("Live video paused while this tab is inactive.");
}

function toggleLiveFeed() {
  livePaused = !livePaused;
  if (livePaused) {
    stopLiveFeed("Live video paused.");
  } else {
    startLiveFeed();
  }
  updateLivePauseState();
}

function updateLiveAudioState(active, label) {
  const state = document.querySelector("#live-audio-state");
  const button = document.querySelector("#live-audio-toggle");
  state.textContent = `· ${label}`;
  state.className = active ? "active" : "";
  button.innerHTML = active
    ? '<span class="nf" aria-hidden="true">󰝟</span> Disable audio'
    : '<span class="nf" aria-hidden="true">󰝟</span> Enable audio';
  button.setAttribute("aria-pressed", String(active));
}

function stopLiveAudio() {
  liveAudio.pause();
  liveAudio.removeAttribute("src");
  liveAudio.load();
  updateLiveAudioState(false, "audio off");
}

async function toggleLiveAudio() {
  if (livePaused) return;
  if (liveAudio.hasAttribute("src")) {
    stopLiveAudio();
    return;
  }
  liveAudio.src = liveAudio.dataset.src;
  liveAudio.load();
  updateLiveAudioState(true, "connecting audio…");
  try {
    await liveAudio.play();
  } catch (error) {
    stopLiveAudio();
    document.querySelector("#live-audio-state").textContent = `· ${errorMessage(error)}`;
  }
}

async function ensureTabData(name) {
  if (cameraControlTabs.has(name) && !cameraControlAvailable) {
    renderProtectedTabUnavailable(name);
    return;
  }
  try {
    if (name === "rewind" && !historyLoaded) await loadHistory();
    if (name === "timelapse" && !timelapseLoaded) await loadTimelapse();
    if (name === "files" && !filesLoaded) await loadFiles();
    if (name === "settings") await loadAppSettings();
  } catch (error) {
    if (cameraControlTabs.has(name) && cameraControlStateFromError(error)) {
      renderCameraControlUnavailable(error);
      return;
    }
    if (name === "rewind") document.querySelector("#history-status").textContent = errorMessage(error);
    if (name === "timelapse") {
      setStatus(document.querySelector("#timelapse-state"), "Camera unavailable", "error");
      document.querySelector("#timelapse-file-status").textContent = errorMessage(error);
    }
    if (name === "files") document.querySelector("#files-status").textContent = errorMessage(error);
    if (name === "settings") {
      setStatus(document.querySelector("#app-settings-state"), "Settings unavailable", "error");
      document.querySelector("#app-settings-status").textContent = errorMessage(error);
    }
  }
}

function renderCacheStats(cache) {
  const maximumBytes = Math.max(1, Number(cache.maximumBytes) || 1);
  const currentBytes = Math.max(0, Number(cache.currentBytes) || 0);
  const usage = document.querySelector("#cache-usage");
  usage.max = maximumBytes;
  usage.value = Math.min(currentBytes, maximumBytes);
  usage.textContent = `${Math.min(100, (currentBytes / maximumBytes) * 100).toFixed(1)}%`;
  document.querySelector("#cache-used").textContent = formatCacheSize(currentBytes);
  document.querySelector("#cache-limit").textContent = `of ${formatSize(maximumBytes)}`;
  document.querySelector("#cache-entry-count").textContent = `${cache.entryCount} / ${cache.maximumEntries}`;
  const busy = document.querySelector("#cache-busy");
  busy.textContent = cache.busy ? "Building" : "Ready";
  busy.className = `file-state${cache.busy ? " active" : ""}`;
  document.querySelector("#show-clear-cache").disabled = cache.busy || cache.entryCount === 0;
}

function renderAppSettings(payload, forceFormRefresh = false) {
  appSettingsData = payload;
  const settings = payload.settings;
  if (!appSettingsDirty || forceFormRefresh) {
    const gibibytes = settings.cacheMaxBytes / 1024 ** 3;
    document.querySelector("#cache-size-gib").value = String(Number(gibibytes.toFixed(3)));
    document.querySelector("#cache-max-entries").value = String(settings.cacheMaxEntries);
    document.querySelector("#rewind-window-setting").value = String(settings.rewindPreviewSeconds);
    document.querySelector("#continue-playback-setting").checked = settings.continuePlayback;
    document.querySelector("#preview-video-codec-setting").value = settings.previewVideoCodec;
    appSettingsDirty = false;
  }
  historyWindow.value = String(settings.rewindPreviewSeconds);
  document.querySelector("#history-continue").checked = settings.continuePlayback;
  const codecStatus = document.querySelector("#native-hevc-status");
  codecStatus.textContent = nativeHevcSupported
    ? "Native HEVC is available; Auto skips the expensive video transcode."
    : "Native HEVC is unavailable here; Auto uses compatible H.264.";
  codecStatus.className = nativeHevcSupported ? "codec-available" : "codec-unavailable";
  renderCacheStats(payload.cache);
  setStatus(document.querySelector("#app-settings-state"), payload.persistent ? "Saved locally" : "Session defaults");
  document.querySelector("#app-settings-status").textContent = payload.persistent
    ? `Saved revision ${settings.revision}.`
    : "Persistence is disabled for this server session.";
}

async function loadAppSettings(forceFormRefresh = false) {
  const panel = document.querySelector("#panel-settings");
  panel.setAttribute("aria-busy", "true");
  try {
    const payload = await getJson("/api/settings");
    renderAppSettings(payload, forceFormRefresh);
  } finally {
    panel.setAttribute("aria-busy", "false");
  }
}

function readAppSettingsForm() {
  const cacheGibibytes = document.querySelector("#cache-size-gib").valueAsNumber;
  const cacheMaxBytes = Math.round(cacheGibibytes * 1024 ** 3);
  if (!Number.isSafeInteger(cacheMaxBytes)) throw new Error("Cache size is outside the supported range.");
  return {
    cacheMaxBytes,
    cacheMaxEntries: document.querySelector("#cache-max-entries").valueAsNumber,
    rewindPreviewSeconds: Number(document.querySelector("#rewind-window-setting").value),
    continuePlayback: document.querySelector("#continue-playback-setting").checked,
    previewVideoCodec: document.querySelector("#preview-video-codec-setting").value,
  };
}

function resolvedPreviewVideoCodec() {
  const preference = appSettingsData?.settings.previewVideoCodec || "auto";
  if (preference !== "auto") return preference;
  return nativeHevcSupported ? "hevc" : "h264";
}

async function saveAppSettings() {
  if (!appSettingsData) return;
  const button = document.querySelector("#save-app-settings");
  const status = document.querySelector("#app-settings-status");
  button.disabled = true;
  button.textContent = "Saving…";
  status.textContent = "Saving preferences and applying cache limits…";
  try {
    const payload = await getJson("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-GrowCam-Request": "1" },
      body: JSON.stringify({
        expectedRevision: appSettingsData.settings.revision,
        settings: readAppSettingsForm(),
      }),
    });
    appSettingsDirty = false;
    renderAppSettings(payload, true);
    status.textContent = "Settings saved and active.";
  } catch (error) {
    status.textContent = errorMessage(error);
  } finally {
    button.disabled = false;
    button.innerHTML = '<span class="nf" aria-hidden="true"></span> Save settings';
  }
}

async function clearMediaCache() {
  const button = document.querySelector("#clear-cache");
  const status = document.querySelector("#app-settings-status");
  button.disabled = true;
  button.textContent = "Clearing…";
  try {
    const payload = await getJson("/api/cache/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-GrowCam-Request": "1" },
      body: "{}",
    });
    if (appSettingsData) appSettingsData.cache = payload.cache;
    renderCacheStats(payload.cache);
    cacheClearDialog.close();
    status.textContent = "Generated preview cache cleared.";
  } catch (error) {
    status.textContent = errorMessage(error);
    cacheClearDialog.close();
  } finally {
    button.disabled = false;
    button.innerHTML = '<span class="nf" aria-hidden="true"></span> Clear cache';
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
    const bounds = recordingBounds(recording);
    const start = (bounds.start / 86400) * 100;
    const duration = bounds.end - bounds.start;
    const width = (duration / 86400) * 100;
    const includeSeconds = duration < 60;
    const beginTime = displayTime(recording.beginTime, includeSeconds);
    const endTime = displayTime(recording.endTime, includeSeconds);
    const segment = document.createElement("button");
    segment.type = "button";
    segment.className = `timeline-segment${recording.active ? " active" : ""}`;
    segment.style.left = `${start}%`;
    segment.style.width = `${Math.max(width, 0.001)}%`;
    segment.disabled = duration <= 0;
    segment.title = `${beginTime}–${endTime} · ${formatSize(recording.sizeBytes)}`;
    segment.setAttribute("aria-label", `${duration > 0 ? "Play" : "Unplayable"} recording ${index + 1}, ${beginTime} to ${endTime}`);
    segment.addEventListener("click", () => selectHistorySegment(index, bounds.start));
    timeline.append(segment);

    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "segment-button";
    button.disabled = duration <= 0;
    const icon = document.createElement("span");
    icon.className = "nf";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "";
    const begin = document.createElement("strong");
    begin.textContent = beginTime;
    const endLabel = document.createElement("span");
    endLabel.textContent = `→ ${endTime}`;
    const size = document.createElement("small");
    size.textContent = formatSize(recording.sizeBytes);
    button.append(icon, begin, endLabel, size);
    button.addEventListener("click", () => selectHistorySegment(index, bounds.start));
    item.append(button);
    list.append(item);
  });

  const status = document.querySelector("#history-status");
  const unplayable = recordings.filter((recording) => {
    const bounds = recordingBounds(recording);
    return bounds.end <= bounds.start;
  }).length;
  status.textContent = recordings.length
    ? `${recordings.length - unplayable} playable block${recordings.length - unplayable === 1 ? "" : "s"}${unplayable ? ` · ${unplayable} zero-duration camera artifact${unplayable === 1 ? "" : "s"} skipped` : ""} · choose a time.`
    : "No recordings found for this day.";
  document.querySelector("#history-position").textContent = `— / ${recordings.length}`;
  document.querySelector("#history-previous").disabled = true;
  document.querySelector("#history-next").disabled = true;
  setScrubberPosition(recordings.length ? recordingBounds(recordings[0]).start : 0);
}

function loadHistory() {
  if (historyLoadPromise) return historyLoadPromise;
  historyLoadPromise = loadHistoryOnce().finally(() => { historyLoadPromise = null; });
  return historyLoadPromise;
}

async function loadHistoryOnce() {
  const dateInput = document.querySelector("#history-date");
  const todayButton = document.querySelector("#history-today");
  const status = document.querySelector("#history-status");
  const timelineCard = document.querySelector(".timeline-card");
  dateInput.disabled = true;
  todayButton.disabled = true;
  timelineCard.setAttribute("aria-busy", "true");
  status.textContent = "Loading recordings…";
  try {
    const payload = await getJson(`/api/history?date=${encodeURIComponent(dateInput.value)}`);
    renderHistory(payload);
    historyLoaded = true;
  } finally {
    dateInput.disabled = false;
    todayButton.disabled = false;
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
  if (historyPreviewOpening) {
    document.querySelector("#history-status").textContent = "A preview is already opening; wait for it to finish.";
    return;
  }
  const recordings = historyData?.recordings || [];
  const recording = recordings[index];
  if (!recording) return;
  const bounds = recordingBounds(recording);
  const requestedSeconds = atSeconds === null ? bounds.start : Math.max(bounds.start, Math.min(bounds.end - 1, atSeconds));
  selectedHistoryIndex = index;
  selectedHistorySeconds = requestedSeconds;
  setScrubberPosition(requestedSeconds);
  highlightHistorySelection(index);
  document.querySelector("#history-title").textContent = `${clockForSeconds(requestedSeconds)} · ${historyWindowLabel(bounds.end - requestedSeconds)}`;
  document.querySelector("#history-position").textContent = `${index + 1} / ${recordings.length}`;
  document.querySelector("#history-previous").disabled = index <= 0;
  document.querySelector("#history-next").disabled = index >= recordings.length - 1;
  const download = document.querySelector("#history-download");
  download.href = `/api/download?file=${encodeURIComponent(recording.fileName)}`;
  download.hidden = recording.active;
  buildHistoryPreview(recording, requestedSeconds);
}

async function buildHistoryPreview(recording, atSeconds) {
  historyPreviewOpening = true;
  if (historyRequest) historyRequest.abort();
  historyRequest = new AbortController();
  const request = historyRequest;
  const status = document.querySelector("#history-status");
  const card = document.querySelector(".rewind-player");
  const parameters = new URLSearchParams({ file: recording.fileName });
  const requestedVideoCodec = resolvedPreviewVideoCodec();
  parameters.set("videoCodec", requestedVideoCodec);
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
  const startedAt = performance.now();
  let playbackStarted = false;
  let usedCompatibleFallback = false;
  let previewUrl = `/api/history/preview?${parameters}`;
  let cacheState = "missing";
  status.textContent = `Streaming about ${formatSize(estimatedBytes)}…`;
  try {
    try {
      cacheState = await previewCacheState(previewUrl, request.signal);
      await loadVideoStream(historyVideo, previewUrl, request.signal);
    } catch (error) {
      const preference = appSettingsData?.settings.previewVideoCodec || "auto";
      if (request.signal.aborted || requestedVideoCodec !== "hevc" || preference !== "auto") throw error;
      usedCompatibleFallback = true;
      stopMediaLoad(historyVideo);
      parameters.set("videoCodec", "h264");
      previewUrl = `/api/history/preview?${parameters}`;
      status.textContent = "Native HEVC did not play; retrying compatible H.264…";
      cacheState = await previewCacheState(previewUrl, request.signal);
      await loadVideoStream(historyVideo, previewUrl, request.signal);
    }
    if (historyRequest !== request) return;
    playbackStarted = true;
    document.querySelector("#history-placeholder").hidden = true;
    await historyVideo.play().catch(() => {});
    status.textContent = `${usedCompatibleFallback ? "Compatible H.264 fallback · " : ""}Ready in ${loadTimeLabel(startedAt)} · cached when complete.`;
    if (cacheState === "missing") {
      void promoteCachedVideo(
        historyVideo,
        previewUrl,
        request,
        () => historyRequest === request,
        1,
        () => { status.textContent = "Indexed cache ready · full seek bar and audio available."; },
      ).catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError") && historyRequest === request) {
          status.textContent = `Preview is playing, but its seek index is unavailable: ${errorMessage(error)}`;
        }
      });
    }
  } catch (error) {
    if (!(error instanceof DOMException && error.name === "AbortError")) status.textContent = errorMessage(error);
  } finally {
    historyPreviewOpening = false;
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

function loadTimelapse(forceFormRefresh = false) {
  if (timelapseLoadPromise) return timelapseLoadPromise;
  timelapseLoadPromise = loadTimelapseOnce(forceFormRefresh).finally(() => { timelapseLoadPromise = null; });
  return timelapseLoadPromise;
}

async function loadTimelapseOnce(forceFormRefresh) {
  const panel = document.querySelector("#panel-timelapse");
  const refreshButton = document.querySelector("#refresh-timelapse");
  refreshButton.disabled = true;
  panel.setAttribute("aria-busy", "true");
  setStatus(document.querySelector("#timelapse-state"), "Loading schedule", "pending");
  try {
    const payload = await getJson("/api/timelapse");
    timelapseData = payload;
    timelapseLoaded = true;
    renderTimelapse(payload, forceFormRefresh);
  } finally {
    refreshButton.disabled = false;
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

function loadFiles() {
  if (filesLoadPromise) return filesLoadPromise;
  filesLoadPromise = loadFilesOnce().finally(() => { filesLoadPromise = null; });
  return filesLoadPromise;
}

async function loadFilesOnce() {
  const panel = document.querySelector("#panel-files");
  const status = document.querySelector("#files-status");
  const dateInput = document.querySelector("#files-date");
  const refreshButton = document.querySelector("#files-refresh");
  dateInput.disabled = true;
  refreshButton.disabled = true;
  panel.setAttribute("aria-busy", "true");
  status.hidden = false;
  status.textContent = "Loading camera files…";
  try {
    filesData = await getJson(`/api/files?date=${encodeURIComponent(dateInput.value)}`);
    filesLoaded = true;
    renderFiles();
  } finally {
    dateInput.disabled = false;
    refreshButton.disabled = false;
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
  let cacheState = "missing";
  try {
    cacheState = await previewCacheState(url, request.signal);
    await loadVideoStream(previewVideo, url, request.signal);
    if (previewRequest !== request) return;
    playbackStarted = true;
    document.querySelector("#preview-placeholder").hidden = true;
    document.querySelector("#preview-title").textContent = recording.active ? "Progress so far" : "Timelapse preview";
    await previewVideo.play().catch(() => {});
    status.textContent = `Ready in ${loadTimeLabel(startedAt)} · playing every stored capture from ${displayDate(recording.beginTime)} → ${displayDate(recording.endTime)}.`;
    if (cacheState === "missing") {
      void promoteCachedVideo(
        previewVideo,
        url,
        request,
        () => previewRequest === request,
        timelapseStreamToCacheScale,
        () => { status.textContent = "Complete 25 fps preview cached · smooth replay and seeking ready."; },
      ).catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError") && previewRequest === request) {
          status.textContent = `Preview is playing, but its accelerated cache is unavailable: ${errorMessage(error)}`;
        }
      });
    }
  } catch (error) {
    if (!(error instanceof DOMException && error.name === "AbortError")) {
      document.querySelector("#preview-title").textContent = "Preview unavailable";
      status.textContent = errorMessage(error);
    }
  } finally {
    if (previewRequest === request) {
      if (!playbackStarted) previewRequest = null;
      card.removeAttribute("aria-busy");
      latestButton.disabled = !timelapseData?.recordings.length;
      fileButtons.forEach((button) => { button.disabled = false; });
    }
  }
}

async function refresh() {
  setStatus(connection, "Connecting", "pending");
  const [infoResult, settingsResult] = await Promise.allSettled([loadInfo(), loadAppSettings()]);

  if (infoResult.status === "rejected") {
    renderCameraControlUnavailable(infoResult.reason);
    setStatus(connection, "Live feed only", "warning");
  } else {
    setStatus(connection, "Camera connected");
  }

  if (settingsResult.status === "rejected") {
    setStatus(document.querySelector("#app-settings-state"), "Settings unavailable", "error");
    document.querySelector("#app-settings-status").textContent = errorMessage(settingsResult.reason);
  }

  appReady = true;
  const activeTab = tabButtons.find((button) => button.getAttribute("aria-selected") === "true");
  const activeTabName = activeTab?.dataset.tab || "live";
  if (activeTabName !== "settings") await ensureTabData(activeTabName);
}

async function retryCameraControls() {
  cameraControlRetry.disabled = true;
  cameraControlRetry.innerHTML = '<span class="nf" aria-hidden="true"></span> Trying one login…';
  setStatus(connection, "Retrying camera controls", "pending");
  try {
    const info = await getJson("/api/camera-control/retry", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-GrowCam-Request": "1",
      },
      body: "{}",
    });
    historyLoaded = false;
    timelapseLoaded = false;
    filesLoaded = false;
    renderCameraInfo(info);
    setStatus(connection, "Camera connected");
    const activeTab = tabButtons.find((button) => button.getAttribute("aria-selected") === "true");
    const activeTabName = activeTab?.dataset.tab || "live";
    if (cameraControlTabs.has(activeTabName)) await ensureTabData(activeTabName);
  } catch (error) {
    renderCameraControlUnavailable(error);
    setStatus(connection, "Live feed only", "warning");
  } finally {
    updateCameraControlRetry();
  }
}

timelapseForm.addEventListener("input", () => {
  formIsDirty = true;
  document.querySelector("#settings-status").textContent = "Unsaved schedule changes.";
});

appSettingsForm.addEventListener("input", () => {
  appSettingsDirty = true;
  document.querySelector("#app-settings-status").textContent = "Unsaved application settings.";
});
appSettingsForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!appSettingsForm.reportValidity()) return;
  void saveAppSettings();
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
  const nextPoint = nextHistoryPointAfter(selectedHistoryIndex, nextSeconds);
  if (nextPoint) selectHistorySegment(nextPoint.index, nextPoint.seconds);
});
document.querySelector("#apply-settings").addEventListener("click", applySettings);
document.querySelector("#live-pause-toggle").addEventListener("click", toggleLiveFeed);
document.querySelector("#live-audio-toggle").addEventListener("click", () => void toggleLiveAudio());
document.querySelector("#show-clear-cache").addEventListener("click", () => cacheClearDialog.showModal());
document.querySelector("#clear-cache").addEventListener("click", () => void clearMediaCache());
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
  const button = document.querySelector("#preview-latest");
  if (button.disabled) return;
  button.disabled = true;
  try {
    await loadTimelapse();
    const latest = timelapseData?.recordings[0];
    if (latest) await buildPreview(latest);
  } catch (error) {
    document.querySelector("#preview-title").textContent = "Preview unavailable";
    document.querySelector("#preview-status").textContent = errorMessage(error);
  } finally {
    button.disabled = !timelapseData?.recordings.length;
  }
});
document.querySelector("#timelapse-speed").addEventListener("change", (event) => {
  previewVideo.defaultPlaybackRate = Number(event.target.value);
  previewVideo.playbackRate = Number(event.target.value);
});
previewVideo.addEventListener("ended", () => {
  if (!currentPreviewRecording) return;
  const decodedFrames = previewVideo.getVideoPlaybackQuality?.().totalVideoFrames;
  const frameCount = Number.isFinite(decodedFrames) && decodedFrames > 0
    ? `${decodedFrames.toLocaleString()} captured frame${decodedFrames === 1 ? "" : "s"} · `
    : "";
  document.querySelector("#preview-status").textContent = `${frameCount}${formatDuration(previewVideo.duration)} compressed video covering ${displayDate(currentPreviewRecording.beginTime)} → ${displayDate(currentPreviewRecording.endTime)} · complete preview cached locally.`;
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

for (const button of liveQualityButtons) {
  button.addEventListener("click", () => selectLiveQuality(button.dataset.liveQuality));
}

cameraControlRetry.addEventListener("click", () => void retryCameraControls());

window.addEventListener("hashchange", () => activateTab(window.location.hash.slice(1) || "live"));

window.addEventListener("pagehide", () => {
  if (previewRequest) previewRequest.abort();
  if (historyRequest) historyRequest.abort();
});

liveFeed.addEventListener("load", () => {
  if (livePaused || !liveFeed.hasAttribute("src")) return;
  const resolution = liveFeed.naturalWidth && liveFeed.naturalHeight
    ? `${liveFeed.naturalWidth} × ${liveFeed.naturalHeight}`
    : "connected";
  document.querySelector("#live-resolution").textContent = `${liveQuality.toUpperCase()} · ${resolution}`;
  livePlaceholder.hidden = true;
  updateLiveQualityButtons(false);
});
liveFeed.addEventListener("error", () => {
  if (!liveFeed.hasAttribute("src") || liveRestartTimer !== null) return;
  document.querySelector("#live-resolution").textContent = "unavailable";
  liveMessage.textContent = "Live feed unavailable. Check the camera connection and FFmpeg.";
  livePlaceholder.hidden = false;
  updateLiveQualityButtons(false);
});
liveAudio.addEventListener("playing", () => updateLiveAudioState(true, "audio live"));
liveAudio.addEventListener("error", () => {
  stopLiveAudio();
  document.querySelector("#live-audio-state").textContent = "· audio unavailable";
});

updateLiveQualityButtons();
updateLivePauseState();
activateTab(window.location.hash.slice(1) || "live");
refresh();
