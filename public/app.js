const statusEl = document.getElementById("status");
const resolveForm = document.getElementById("resolve-form");
const resolveResultsEl = document.getElementById("resolve-results");
const player = document.getElementById("player");
const nowPlayingEl = document.getElementById("now-playing");
const favoritesEl = document.getElementById("favorites");
const historyEl = document.getElementById("history");
const uploadsEl = document.getElementById("uploads");
const torrentMetaEl = document.getElementById("torrent-meta");
const bufferFillEl = document.getElementById("buffer-fill");
const bufferPercentEl = document.getElementById("buffer-percent");

const magnetForm = document.getElementById("magnet-form");
const torrentUploadForm = document.getElementById("torrent-upload-form");

const state = {
  currentItem: null,
  currentTorrentId: null,
  resolveController: null,
  bufferPercent: 0,
};

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.borderColor = isError ? "rgba(255,111,111,0.7)" : "rgba(143,173,202,0.3)";
  statusEl.style.color = isError ? "#ffb3b3" : "#e6edf3";
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function setBufferPercent(value) {
  const normalized = clamp(Number(value || 0), 0, 100);
  state.bufferPercent = normalized;
  bufferFillEl.style.width = `${normalized}%`;
  bufferPercentEl.textContent = `${Math.round(normalized)}%`;
}

function resetBufferPercent() {
  setBufferPercent(0);
}

function updateBufferFromPlayer() {
  if (!Number.isFinite(player.duration) || player.duration <= 0) {
    return;
  }

  if (player.buffered.length === 0) {
    return;
  }

  const bufferedEnd = player.buffered.end(player.buffered.length - 1);
  const percentage = clamp((bufferedEnd / player.duration) * 100, 0, 100);
  if (percentage > state.bufferPercent) {
    setBufferPercent(percentage);
  }
}

player.addEventListener("progress", updateBufferFromPlayer);
player.addEventListener("loadedmetadata", updateBufferFromPlayer);
player.addEventListener("timeupdate", updateBufferFromPlayer);
player.addEventListener("waiting", () => setStatus("Playback waiting for more data..."));
player.addEventListener("playing", () => setStatus("Playback running"));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || JSON.stringify(payload);
    } catch {
      const text = await response.text();
      if (text) detail = text;
    }
    throw new Error(detail);
  }

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function createItemCard(title, subtitle) {
  const item = document.createElement("div");
  item.className = "list-item";

  const heading = document.createElement("h3");
  heading.textContent = title || "Untitled";
  item.appendChild(heading);

  if (subtitle) {
    const text = document.createElement("p");
    text.textContent = subtitle;
    item.appendChild(text);
  }

  return item;
}

function formatStreamLabel(stream) {
  const quality = stream.quality || "source";
  const ext = stream.ext || "";
  const kind = stream.is_hls ? "HLS" : "direct";
  return `${quality} ${ext} ${kind}`.replace(/\s+/g, " ").trim();
}

function hostFromUrl(url) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function pickBestStream(item) {
  if (!item?.streams?.length) {
    return null;
  }

  const scored = item.streams.map((stream) => {
    let score = 0;
    if (stream.ext === "mp4") score += 60;
    if (!stream.is_hls) score += 20;

    const qualityText = `${stream.quality || ""}`.toLowerCase();
    const qualityMatch = qualityText.match(/(\d{3,4})p/);
    if (qualityMatch) {
      score += Number(qualityMatch[1]) / 10;
    }

    return { stream, score };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored[0].stream;
}

async function requestStreamToken(streamUrl, headers = {}) {
  return api("/api/media/token", {
    method: "POST",
    body: JSON.stringify({ url: streamUrl, headers }),
  });
}

async function prebufferToken(token) {
  const maxBytes = window.matchMedia("(max-width: 860px)").matches
    ? 16 * 1024 * 1024
    : 40 * 1024 * 1024;

  setBufferPercent(12);
  const result = await api("/api/media/prebuffer", {
    method: "POST",
    body: JSON.stringify({ token, max_bytes: maxBytes }),
  });

  if (result?.cached_bytes) {
    // Reflect requested UX: force full prebuffer bar once startup cache is primed.
    setBufferPercent(100);
  }

  return result;
}

async function playResolvedStream(item, stream) {
  resetBufferPercent();
  setStatus("Requesting secure playback token...");
  const tokenData = await requestStreamToken(stream.url, stream.headers || {});

  try {
    setStatus("Prebuffering startup range...");
    await prebufferToken(tokenData.token);
  } catch (error) {
    setStatus(`Prebuffer fallback: ${error.message}`);
  }

  player.src = tokenData.stream_url;
  player.load();

  try {
    await player.play();
  } catch {
    // Browsers may block autoplay until user interaction.
  }

  state.currentItem = {
    title: item.title,
    source_url: item.webpage_url,
    playback_url: tokenData.stream_url,
    thumbnail_url: item.thumbnail || null,
  };

  nowPlayingEl.textContent = `${item.title} (${stream.quality || stream.ext || "source"})`;
  setStatus("Playback started");
}

function buildResolveCard(item, index) {
  const card = document.createElement("article");
  card.className = "media-card";

  const head = document.createElement("div");
  head.className = "media-head";

  const thumb = document.createElement("img");
  thumb.className = "media-thumb";
  thumb.alt = item.title || `Media ${index + 1}`;
  thumb.loading = "lazy";
  thumb.referrerPolicy = "no-referrer";
  thumb.src = item.thumbnail || "";
  thumb.onerror = () => {
    thumb.removeAttribute("src");
  };

  const info = document.createElement("div");
  info.className = "media-info";

  const titleEl = document.createElement("h3");
  titleEl.textContent = item.title || "Untitled";

  const sourceEl = document.createElement("p");
  sourceEl.className = "media-source";
  sourceEl.textContent = hostFromUrl(item.webpage_url || "Unknown source");

  const streamCount = document.createElement("p");
  streamCount.className = "media-source";
  streamCount.textContent = `${item.streams?.length || 0} playable stream(s)`;

  info.append(titleEl, sourceEl, streamCount);
  head.append(thumb, info);
  card.appendChild(head);

  const streamList = document.createElement("div");
  streamList.className = "stream-list";

  if (!item.streams || item.streams.length === 0) {
    const empty = document.createElement("p");
    empty.className = "media-source";
    empty.textContent = "No playable stream found in this entry.";
    streamList.appendChild(empty);
  } else {
    const best = pickBestStream(item);
    const topStreams = item.streams.slice(0, 8);

    topStreams.forEach((stream) => {
      const row = document.createElement("div");
      row.className = "stream-row";

      const chip = document.createElement("span");
      chip.className = "stream-chip";
      chip.textContent = formatStreamLabel(stream);

      const actions = document.createElement("div");
      actions.className = "stream-actions";

      const playBtn = document.createElement("button");
      playBtn.type = "button";
      playBtn.textContent = stream === best ? "Play Best" : "Play";
      if (stream === best) {
        playBtn.className = "secondary";
      }

      playBtn.addEventListener("click", async () => {
        try {
          setStatus("Preparing playback...");
          await playResolvedStream(item, stream);
          await refreshHistory();
        } catch (error) {
          setStatus(`Playback failed: ${error.message}`, true);
        }
      });

      actions.append(playBtn);
      row.append(chip, actions);
      streamList.appendChild(row);
    });
  }

  card.appendChild(streamList);
  return card;
}

function renderResolveResults(items) {
  resolveResultsEl.innerHTML = "";

  if (!items.length) {
    resolveResultsEl.textContent = "No streams discovered for this query.";
    return;
  }

  items.forEach((item, index) => {
    resolveResultsEl.appendChild(buildResolveCard(item, index));
  });
}

resolveForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.getElementById("query").value.trim();
  if (!query) return;

  if (state.resolveController) {
    state.resolveController.abort();
  }
  state.resolveController = new AbortController();

  const startedAt = performance.now();

  try {
    setStatus("Resolving media links...");
    const payload = await api("/api/media/resolve", {
      method: "POST",
      body: JSON.stringify({ query }),
      signal: state.resolveController.signal,
    });

    renderResolveResults(payload.items || []);
    const elapsedMs = Math.round(performance.now() - startedAt);
    setStatus(`Resolved ${payload.items?.length || 0} item(s) in ${elapsedMs}ms`);
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }
    setStatus(`Resolve failed: ${error.message}`, true);
  }
});

async function saveHistory() {
  if (!state.currentItem) {
    setStatus("No active playback to save", true);
    return;
  }

  try {
    await api("/api/library/history", {
      method: "POST",
      body: JSON.stringify({
        title: state.currentItem.title,
        source_url: state.currentItem.source_url,
        playback_url: state.currentItem.playback_url,
        position_seconds: Number(player.currentTime || 0),
        duration_seconds: Number.isFinite(player.duration) ? Number(player.duration) : null,
      }),
    });
    setStatus("Playback progress saved");
    await refreshHistory();
  } catch (error) {
    setStatus(`Could not save history: ${error.message}`, true);
  }
}

async function addFavorite() {
  if (!state.currentItem) {
    setStatus("No media selected to favorite", true);
    return;
  }

  try {
    await api("/api/library/favorites", {
      method: "POST",
      body: JSON.stringify({
        title: state.currentItem.title,
        source_url: state.currentItem.source_url,
        thumbnail_url: state.currentItem.thumbnail_url,
      }),
    });
    setStatus("Added to favorites");
    await refreshFavorites();
  } catch (error) {
    setStatus(`Could not favorite media: ${error.message}`, true);
  }
}

document.getElementById("btn-save-history").addEventListener("click", saveHistory);
document.getElementById("btn-add-favorite").addEventListener("click", addFavorite);

async function quickResolveAndPlay(sourceUrl) {
  const payload = await api("/api/media/resolve", {
    method: "POST",
    body: JSON.stringify({ query: sourceUrl }),
  });

  const firstItem = (payload.items || [])[0];
  const firstStream = pickBestStream(firstItem) || firstItem?.streams?.[0];
  if (!firstItem || !firstStream) {
    throw new Error("No playable stream found from source URL");
  }

  await playResolvedStream(firstItem, firstStream);
}

async function refreshFavorites() {
  try {
    const favorites = await api("/api/library/favorites");
    favoritesEl.innerHTML = "";

    if (!favorites.length) {
      favoritesEl.textContent = "No favorites yet.";
      return;
    }

    favorites.forEach((item) => {
      const card = createItemCard(item.title, item.source_url);
      const row = document.createElement("div");
      row.className = "row";

      const playBtn = document.createElement("button");
      playBtn.type = "button";
      playBtn.textContent = "Resolve & Play";
      playBtn.addEventListener("click", async () => {
        try {
          setStatus("Resolving favorite...");
          await quickResolveAndPlay(item.source_url);
        } catch (error) {
          setStatus(`Favorite playback failed: ${error.message}`, true);
        }
      });

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.textContent = "Remove";
      removeBtn.className = "danger";
      removeBtn.addEventListener("click", async () => {
        try {
          await api(`/api/library/favorites/${item.id}`, { method: "DELETE" });
          await refreshFavorites();
          setStatus("Favorite removed");
        } catch (error) {
          setStatus(`Could not remove favorite: ${error.message}`, true);
        }
      });

      row.append(playBtn, removeBtn);
      card.appendChild(row);
      favoritesEl.appendChild(card);
    });
  } catch (error) {
    favoritesEl.textContent = `Failed to load favorites: ${error.message}`;
  }
}

async function refreshHistory() {
  try {
    const historyItems = await api("/api/library/history");
    historyEl.innerHTML = "";

    if (!historyItems.length) {
      historyEl.textContent = "No history yet.";
      return;
    }

    historyItems.forEach((item) => {
      const subtitle = `${item.source_url} | ${Math.round(item.position_seconds || 0)}s`;
      const card = createItemCard(item.title, subtitle);
      const row = document.createElement("div");
      row.className = "row";

      const playBtn = document.createElement("button");
      playBtn.type = "button";
      playBtn.textContent = "Play Saved";
      playBtn.addEventListener("click", async () => {
        try {
          if (!item.playback_url) {
            await quickResolveAndPlay(item.source_url);
            return;
          }

          resetBufferPercent();
          player.src = item.playback_url;
          player.load();
          await player.play().catch(() => {});
          state.currentItem = {
            title: item.title,
            source_url: item.source_url,
            playback_url: item.playback_url,
            thumbnail_url: null,
          };
          nowPlayingEl.textContent = `${item.title} (history)`;
          setStatus("Playing history entry");
        } catch (error) {
          setStatus(`History playback failed: ${error.message}`, true);
        }
      });

      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "danger";
      deleteBtn.textContent = "Delete";
      deleteBtn.addEventListener("click", async () => {
        try {
          await api(`/api/library/history/${item.id}`, { method: "DELETE" });
          await refreshHistory();
          setStatus("History item deleted");
        } catch (error) {
          setStatus(`Could not delete history entry: ${error.message}`, true);
        }
      });

      row.append(playBtn, deleteBtn);
      card.appendChild(row);
      historyEl.appendChild(card);
    });
  } catch (error) {
    historyEl.textContent = `Failed to load history: ${error.message}`;
  }
}

document.getElementById("btn-refresh-history").addEventListener("click", refreshHistory);
document.getElementById("btn-clear-history").addEventListener("click", async () => {
  try {
    await api("/api/library/history", { method: "DELETE" });
    await refreshHistory();
    setStatus("History cleared");
  } catch (error) {
    setStatus(`Could not clear history: ${error.message}`, true);
  }
});

async function refreshUploads() {
  try {
    const uploads = await api("/api/library/uploads");
    uploadsEl.innerHTML = "";

    if (!uploads.length) {
      uploadsEl.textContent = "No uploads recorded.";
      return;
    }

    uploads.forEach((item) => {
      const subtitle = `${item.filename} (${Math.round(item.size_bytes / 1024)} KB)`;
      const card = createItemCard("Upload", subtitle);

      const code = document.createElement("code");
      code.textContent = item.sha256;
      card.appendChild(code);

      uploadsEl.appendChild(card);
    });
  } catch (error) {
    uploadsEl.textContent = `Failed to load uploads: ${error.message}`;
  }
}

function updateTorrentMeta(obj) {
  torrentMetaEl.textContent = [
    `ID: ${obj.id}`,
    `Status: ${obj.status || "unknown"}`,
    `Progress: ${Number(obj.progress || 0).toFixed(2)}%`,
    `Streamable: ${obj.streamable ? "yes" : "no"}`,
  ].join(" | ");

  state.currentTorrentId = obj.id;
}

magnetForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const magnet = document.getElementById("magnet").value.trim();
  if (!magnet) {
    setStatus("Magnet link is required", true);
    return;
  }

  try {
    setStatus("Submitting magnet to AllDebrid...");
    const result = await api("/api/torrents/magnet", {
      method: "POST",
      body: JSON.stringify({ magnet }),
    });
    updateTorrentMeta(result);
    setStatus(`Magnet accepted with id ${result.id}`);
  } catch (error) {
    setStatus(`Magnet upload failed: ${error.message}`, true);
  }
});

torrentUploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = document.getElementById("torrent-file");
  const file = fileInput.files?.[0];
  if (!file) {
    setStatus("Choose a .torrent file first", true);
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    setStatus("Uploading .torrent to AllDebrid...");
    const result = await api("/api/torrents/upload", {
      method: "POST",
      body: formData,
    });
    updateTorrentMeta(result);
    await refreshUploads();
    setStatus(`Torrent uploaded with id ${result.id}`);
  } catch (error) {
    setStatus(`Torrent upload failed: ${error.message}`, true);
  }
});

document.getElementById("btn-status").addEventListener("click", async () => {
  if (!state.currentTorrentId) {
    setStatus("No active torrent id available", true);
    return;
  }

  try {
    setStatus("Polling torrent status...");
    const result = await api(`/api/torrents/${encodeURIComponent(state.currentTorrentId)}/status`);
    updateTorrentMeta(result);
    setStatus("Torrent status updated");
  } catch (error) {
    setStatus(`Status polling failed: ${error.message}`, true);
  }
});

document.getElementById("btn-init-stream").addEventListener("click", async () => {
  if (!state.currentTorrentId) {
    setStatus("No active torrent id available", true);
    return;
  }

  try {
    setStatus("Initializing streamable torrent playback...");
    const result = await api(`/api/torrents/${encodeURIComponent(state.currentTorrentId)}/stream`, {
      method: "POST",
      body: JSON.stringify({}),
    });

    updateTorrentMeta(result);

    if (result.stream_url) {
      resetBufferPercent();
      player.src = result.stream_url;
      player.load();
      await player.play().catch(() => {});
      state.currentItem = {
        title: result.filename || `Torrent ${result.id}`,
        source_url: `magnet:${result.id}`,
        playback_url: result.stream_url,
        thumbnail_url: null,
      };
      nowPlayingEl.textContent = `Torrent playback: ${state.currentItem.title}`;
      setBufferPercent(100);
    }

    setStatus("Torrent stream initialized");
  } catch (error) {
    setStatus(`Stream init failed: ${error.message}`, true);
  }
});

(async function boot() {
  setStatus("Loading library data...");
  await Promise.all([refreshFavorites(), refreshHistory(), refreshUploads()]);
  setStatus("Ready");
})();
