const form = document.querySelector("#search-form");
const input = document.querySelector("#query");
const button = document.querySelector("#search-button");
const status = document.querySelector("#status");
const results = document.querySelector("#results");
const count = document.querySelector("#result-count");
const queueSection = document.querySelector("#queue-section");
const queueList = document.querySelector("#queue-list");
const queueCount = document.querySelector("#queue-count");
const playAllButton = document.querySelector("#play-all");
const queueFilter = document.querySelector("#queue-filter");
const genreFilter = document.querySelector("#genre-filter");
const partyMode = document.querySelector("#party-mode");
const sessionUrlInput = document.querySelector("#session-url");
const copySessionButton = document.querySelector("#copy-session");
const sessionQr = document.querySelector("#session-qr");
const qrDialog = document.querySelector("#qr-dialog");
const qrDialogImage = document.querySelector("#qr-dialog-image");
const openQrButton = document.querySelector("#open-qr");
const closeQrButton = document.querySelector("#close-qr");
const karaokeScreen = document.querySelector("#karaoke-screen");
const karaokeBackdrop = document.querySelector("#karaoke-backdrop");
const karaokeCover = document.querySelector("#karaoke-cover");
const karaokeTitle = document.querySelector("#karaoke-title");
const karaokeArtist = document.querySelector("#karaoke-artist");
const karaokePosition = document.querySelector("#karaoke-position");
const karaokeLyrics = document.querySelector("#karaoke-lyrics");
const karaokeNote = document.querySelector("#karaoke-note");
const karaokeAudio = document.querySelector("#karaoke-audio");
const karaokeToggle = document.querySelector("#karaoke-toggle");
const karaokeSeek = document.querySelector("#karaoke-seek");
const karaokeCurrentTime = document.querySelector("#karaoke-current-time");
const karaokeDuration = document.querySelector("#karaoke-duration");
const karaokeNext = document.querySelector("#karaoke-next");
const stemMixer = document.querySelector("#stem-mixer");
const stemMixerList = document.querySelector("#stem-mixer-list");
const stemLabels = { backing_vocals: "Backing vocal", drums: "Bateria", bass: "Baixo", guitar: "Guitarra", piano: "Piano", other: "Outros instrumentos" };
const stemTracks = {};
let activePreview = null;
let pendingSelection = null;
let queueJobs = [];
let playlist = [];
let playlistIndex = 0;
let activeLyricIndex = -2;
let partyModeEnabled = false;
let controlsTimer = null;
let stemSyncTimer = null;

function showKaraokeControls() {
  karaokeScreen.classList.remove("controls-idle");
  clearTimeout(controlsTimer);
  if (!karaokeAudio.paused && !karaokeAudio.ended) {
    controlsTimer = setTimeout(() => karaokeScreen.classList.add("controls-idle"), 2800);
  }
}

function keepKaraokeControlsVisible() {
  showKaraokeControls();
}

function stopStemTracks() {
  clearInterval(stemSyncTimer);
  stemSyncTimer = null;
  Object.values(stemTracks).forEach((track) => { track.audio.pause(); track.audio.removeAttribute("src"); track.audio.load(); });
  Object.keys(stemTracks).forEach((key) => delete stemTracks[key]);
}

function setupStemMixer(job) {
  stopStemTracks();
  stemMixerList.replaceChildren();
  const available = job.instruments && Object.keys(job.instruments).length ? ["backing_vocals", "drums", "bass", "guitar", "piano", "other"] : [];
  stemMixer.hidden = !available.length;
  available.forEach((name) => {
    const audio = new Audio();
    audio.preload = "auto";
    audio.src = `/api/stem?id=${encodeURIComponent(job.id)}&stem=${encodeURIComponent(name)}`;
    audio.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(karaokeAudio.currentTime)) audio.currentTime = karaokeAudio.currentTime;
      if (!karaokeAudio.paused) audio.play().catch(() => {});
    });
    const row = document.createElement("div"); row.className = "stem-mixer-row";
    const mute = document.createElement("button"); mute.type = "button"; mute.className = "stem-mute"; mute.textContent = "MUTAR"; mute.setAttribute("aria-label", `Mutar ${stemLabels[name]}`);
    const label = document.createElement("span"); label.className = "stem-label"; label.textContent = stemLabels[name];
    const volume = document.createElement("input"); volume.type = "range"; volume.min = "0"; volume.max = "1"; volume.step = "0.01"; volume.value = name === "backing_vocals" ? "0.78" : "1"; volume.setAttribute("aria-label", `Volume de ${stemLabels[name]}`);
    audio.volume = Number(volume.value);
    mute.addEventListener("click", () => { const muted = audio.volume > 0; audio.volume = muted ? 0 : Number(volume.value) || 1; mute.classList.toggle("muted", muted); mute.textContent = muted ? "ATIVAR" : "MUTAR"; });
    volume.addEventListener("input", () => { audio.volume = Number(volume.value); mute.classList.toggle("muted", audio.volume === 0); });
    row.append(mute, label, volume); stemMixerList.append(row); stemTracks[name] = { audio, volume };
  });
  karaokeAudio.volume = available.length ? 0 : 1;
}

function syncStemTracks(play) {
  Object.values(stemTracks).forEach(({ audio }) => {
    if (Math.abs(audio.currentTime - karaokeAudio.currentTime) > 0.04) {
      try { audio.currentTime = karaokeAudio.currentTime; } catch { /* metadata ainda não carregou */ }
    }
    if (play) audio.play().catch(() => {}); else audio.pause();
  });
  clearInterval(stemSyncTimer);
  if (play && Object.keys(stemTracks).length) {
    stemSyncTimer = setInterval(() => {
      if (karaokeAudio.paused || karaokeAudio.ended) return;
      Object.values(stemTracks).forEach(({ audio }) => {
        if (Math.abs(audio.currentTime - karaokeAudio.currentTime) > 0.04) {
          try { audio.currentTime = karaokeAudio.currentTime; } catch { /* aguarda loadedmetadata */ }
        }
      });
    }, 180);
  }
}

function filteredJobs() {
  return queueJobs.filter((job) => {
    if (queueFilter.value === "ready" && !(job.status === "ready" && job.karaoke_audio)) return false;
    if (queueFilter.value === "favorite" && !job.favorite) return false;
    if (genreFilter.value !== "all" && (job.genre || "Outros") !== genreFilter.value) return false;
    return true;
  });
}

function clearPendingSelection() {
  if (!pendingSelection) return;
  pendingSelection.bar.remove();
  pendingSelection.card.classList.remove("confirming");
  pendingSelection.select.disabled = false;
  pendingSelection.select.textContent = "Selecionar";
  pendingSelection.select.setAttribute("aria-expanded", "false");
  pendingSelection.select.removeAttribute("aria-controls");
  pendingSelection = null;
}

function renderQueue(jobs) {
  queueJobs = jobs;
  const genres = [...new Set(jobs.map((job) => job.genre || "Outros"))].sort((a, b) => a.localeCompare(b));
  const previousGenre = genreFilter.value;
  genreFilter.replaceChildren(new Option("Todos", "all"), ...genres.map((genre) => new Option(genre, genre)));
  genreFilter.value = genres.includes(previousGenre) ? previousGenre : "all";
  const visible = filteredJobs();
  queueSection.hidden = jobs.length === 0;
  queueCount.textContent = `${visible.length} ${visible.length === 1 ? "música" : "músicas"}`;
  playAllButton.disabled = !jobs.some((job) => job.status === "ready" && job.karaoke_audio);
  queueList.replaceChildren();

  const labels = {
    queued: "Aguardando", downloading: "Baixando", preparing_audio: "Preparando áudio",
    separating_instrumental: "Separando instrumental",
    separating_backing: "Separando vozes",
    separating_instruments: "Separando instrumentos",
    analyzing_key: "Identificando tonalidade",
    preparing_karaoke: "Montando karaokê", fetching_metadata: "Buscando letra e capa",
    ready: "Pronta para cantar", error: "Falha no processamento",
  };
  visible.forEach((job, index) => {
    const row = document.createElement("article");
    row.className = `queue-item queue-${job.status}`;
    const artwork = document.createElement("img");
    artwork.className = "queue-artwork";
    artwork.src = job.artwork_url || `https://i.ytimg.com/vi/${encodeURIComponent(job.id)}/hqdefault.jpg`;
    artwork.alt = "";
    artwork.loading = "lazy";
    const main = document.createElement("div");
    main.className = "queue-main";
    const order = document.createElement("p");
    order.className = "queue-order";
    order.textContent = `#${String(index + 1).padStart(2, "0")} NA FILA`;
    const title = document.createElement("strong");
    title.className = "queue-title";
    title.textContent = job.title;
    const channel = document.createElement("p");
    channel.className = "queue-channel";
    channel.textContent = `${job.channel} · ${job.genre || "Outros"}`;
    main.append(order, title, channel);

    if (job.status === "downloading") {
      const progress = document.createElement("div");
      progress.className = "queue-progress";
      progress.setAttribute("role", "progressbar");
      progress.setAttribute("aria-label", `Download de ${job.title}`);
      progress.setAttribute("aria-valuemin", "0");
      progress.setAttribute("aria-valuemax", "100");
      progress.setAttribute("aria-valuenow", String(job.progress || 0));
      const fill = document.createElement("span");
      fill.style.width = `${Math.max(3, Math.min(100, job.progress || 0))}%`;
      progress.append(fill);
      main.append(progress);
    }
    if (job.status === "ready" && job.karaoke_audio) {
      if (job.key) {
        const key = document.createElement("p");
        key.className = "queue-key";
        key.textContent = `Tonalidade estimada: ${job.key.label} · Escala: ${job.key.scale.join(" · ")}`;
        main.append(key);
      } else {
        const key = document.createElement("p");
        key.className = "queue-key";
        key.textContent = "Tonalidade não identificada com segurança.";
        main.append(key);
      }
      const availability = document.createElement("p");
      availability.className = "queue-file";
      availability.textContent = job.lyrics?.synced
        ? `${job.album || "Capa encontrada"} · Letra sincronizada`
        : "Letra sincronizada indisponível para esta versão";
      main.append(availability);
    }
    if (job.status === "error" && job.error) {
      const error = document.createElement("p");
      error.className = "queue-error";
      error.textContent = job.error;
      main.append(error);
    }
    const state = document.createElement("span");
    state.className = "queue-state";
    state.textContent = job.status === "downloading" && job.progress
      ? `${job.progress}% · ${labels[job.status]}`
      : labels[job.status] || job.status;
    const side = document.createElement("div");
    side.className = "queue-side";
    side.append(state);
    const favorite = document.createElement("button");
    favorite.className = `favorite-button${job.favorite ? " is-favorite" : ""}`;
    favorite.type = "button";
    favorite.textContent = job.favorite ? "★ Favorita" : "☆ Favoritar";
    favorite.addEventListener("click", async () => {
      await updateQueueJob(job.id, { favorite: !job.favorite });
    });
    side.append(favorite);
    const genre = document.createElement("select");
    genre.className = "genre-select";
    ["Pop", "Rock", "Sertanejo", "MPB", "Pagode", "Gospel", "Rap", "Eletrônica", "Internacional", "Outros"].forEach((option) => genre.append(new Option(option, option)));
    genre.value = ["Pop", "Rock", "Sertanejo", "MPB", "Pagode", "Gospel", "Rap", "Eletrônica", "Internacional", "Outros"].includes(job.genre) ? job.genre : "Outros";
    genre.setAttribute("aria-label", `Categoria de ${job.title}`);
    genre.addEventListener("change", () => updateQueueJob(job.id, { genre: genre.value }));
    side.append(genre);
    if (job.status === "ready" && job.karaoke_audio) {
      const start = document.createElement("button");
      start.className = "queue-play-button";
      start.type = "button";
      start.textContent = "▶ Iniciar karaokê";
      start.addEventListener("click", () => startKaraoke([job]));
      side.append(start);
    }
    row.append(artwork, main, side);
    queueList.append(row);
  });
}

let latestQueueRequest = 0;
async function refreshQueue() {
  const request = ++latestQueueRequest;
  try {
    const response = await fetch("/api/queue");
    if (!response.ok) throw new Error("Falha ao consultar a fila.");
    const data = await response.json();
    if (request === latestQueueRequest) renderQueue(data.jobs);
  } catch (error) {
    console.error(error);
  }
}

async function updateQueueJob(id, changes) {
  try {
    const response = await fetch("/api/queue", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "update", id, ...changes }) });
    if (!response.ok) throw new Error("Não foi possível atualizar a música.");
    await refreshQueue();
  } catch (error) {
    status.classList.add("error");
    status.textContent = error.message;
  }
}

function stopPreview() {
  if (!activePreview) return;
  activePreview.audio.pause();
  activePreview.audio.removeAttribute("src");
  activePreview.audio.load();
  activePreview.button.textContent = "▶ Prévia";
  activePreview.card.classList.remove("previewing");
  activePreview = null;
}

function formatDuration(seconds) {
  if (seconds == null) return "";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function renderResults(items, source) {
  stopPreview();
  clearPendingSelection();
  results.replaceChildren();
  count.hidden = items.length === 0;
  count.textContent = `${items.length} ${items.length === 1 ? "resultado" : "resultados"}`;
  if (!items.length) {
    status.textContent = "Nenhum vídeo encontrado. Tente outro artista ou título.";
    return;
  }

  status.textContent = source === "url"
    ? "Vídeo encontrado. Ouça uma prévia e selecione para continuar."
    : "Ouça uma prévia e escolha a versão que você quer transformar em karaokê.";

  items.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "result-card";

    const thumbWrap = document.createElement("div");
    thumbWrap.className = "thumbnail-wrap";
    const image = document.createElement("img");
    image.src = item.thumbnail;
    image.alt = "";
    image.loading = "lazy";
    thumbWrap.append(image);
    if (item.duration != null) {
      const duration = document.createElement("span");
      duration.className = "duration";
      duration.textContent = formatDuration(item.duration);
      thumbWrap.append(duration);
    }

    const details = document.createElement("div");
    details.className = "result-details";
    const indexText = document.createElement("p");
    indexText.className = "result-index";
    indexText.textContent = `RESULTADO ${String(index + 1).padStart(2, "0")}`;
    const title = document.createElement("a");
    title.className = "result-title";
    title.href = item.url;
    title.target = "_blank";
    title.rel = "noopener noreferrer";
    title.textContent = item.title;
    const channel = document.createElement("p");
    channel.className = "result-channel";
    channel.textContent = item.channel;
    details.append(indexText, title, channel);

    const select = document.createElement("button");
    select.className = "select-button";
    select.type = "button";
    select.textContent = "Selecionar";
    select.setAttribute("aria-label", `Selecionar ${item.title}`);
    select.setAttribute("aria-expanded", "false");
    select.addEventListener("click", () => {
      clearPendingSelection();
      card.classList.add("confirming");
      select.textContent = "Selecionada ✓";
      select.setAttribute("aria-expanded", "true");
      select.disabled = true;
      const bar = document.createElement("div");
      bar.className = "confirmation-bar";
      bar.id = `confirmation-${item.id}`;
      select.setAttribute("aria-controls", bar.id);
      const prompt = document.createElement("span");
      prompt.textContent = "Adicionar esta música à fila de downloads?";
      const choices = document.createElement("div");
      choices.className = "confirmation-actions";
      const cancel = document.createElement("button");
      cancel.className = "cancel-button";
      cancel.type = "button";
      cancel.textContent = "Cancelar";
      cancel.addEventListener("click", clearPendingSelection);
      const confirm = document.createElement("button");
      confirm.className = "confirm-button";
      confirm.type = "button";
      confirm.textContent = "Confirmar";
      confirm.addEventListener("click", async () => {
        cancel.disabled = true;
        confirm.disabled = true;
        confirm.textContent = "Adicionando…";
        try {
          const response = await fetch("/api/queue", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: item.id, title: item.title, channel: item.channel }),
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Não foi possível adicionar a música.");
          if (pendingSelection?.card === card) clearPendingSelection();
          stopPreview();
          status.classList.remove("error");
          status.textContent = data.added ? "Música adicionada à fila de downloads." : "Esta música já está na fila.";
          await refreshQueue();
          queueSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
        } catch (error) {
          status.classList.add("error");
          status.textContent = error.message || "Não foi possível adicionar a música.";
          cancel.disabled = false;
          confirm.disabled = false;
          confirm.textContent = "Confirmar";
        }
      });
      choices.append(cancel, confirm);
      bar.append(prompt, choices);
      card.append(bar);
      pendingSelection = { card, select, bar };
    });

    const preview = document.createElement("button");
    preview.className = "preview-button";
    preview.type = "button";
    preview.textContent = "▶ Prévia";
    preview.setAttribute("aria-label", `Ouvir prévia de ${item.title}`);
    preview.addEventListener("click", () => {
      if (activePreview?.button === preview) {
        stopPreview();
        status.textContent = "Prévia interrompida. Escolha a versão desejada ou ouça outra.";
        return;
      }
      stopPreview();
      status.classList.remove("error");
      status.textContent = "Carregando prévia de áudio…";
      const audio = new Audio(`/api/preview?id=${encodeURIComponent(item.id)}`);
      audio.addEventListener("playing", () => {
        if (activePreview?.audio !== audio) return;
        preview.textContent = "■ Parar";
        status.textContent = "Tocando prévia de áudio. Clique em Parar para interromper.";
      });
      audio.addEventListener("ended", () => {
        if (activePreview?.audio !== audio) return;
        stopPreview();
        status.textContent = "Prévia concluída. Escolha a versão desejada ou ouça outra.";
      });
      audio.addEventListener("error", () => {
        if (activePreview?.audio !== audio) return;
        stopPreview();
        status.classList.add("error");
        status.textContent = "Não foi possível tocar esta prévia. Tente outra música.";
      });
      card.classList.add("previewing");
      preview.textContent = "Carregando… ■";
      activePreview = { audio, button: preview, card };
      audio.play().catch(() => {
        if (activePreview?.audio !== audio) return;
        stopPreview();
        status.classList.add("error");
        status.textContent = "Não foi possível tocar esta prévia. Tente outra música.";
      });
    });

    const actions = document.createElement("div");
    actions.className = "result-actions";
    actions.append(preview, select);
    card.append(thumbWrap, details, actions);
    results.append(card);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = input.value.trim();
  if (!query) return;
  button.disabled = true;
  button.textContent = "Buscando…";
  status.classList.remove("error");
  status.textContent = "Procurando no YouTube…";
  stopPreview();
  clearPendingSelection();
  results.replaceChildren();
  count.hidden = true;

  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Não foi possível concluir a busca.");
    renderResults(data.results, data.source);
  } catch (error) {
    status.classList.add("error");
    status.textContent = error.message || "Não foi possível concluir a busca.";
  } finally {
    button.disabled = false;
    button.innerHTML = 'Buscar <span aria-hidden="true">→</span>';
  }
});

function renderLyrics() {
  const job = playlist[playlistIndex];
  const lyrics = job?.lyrics;
  if (!lyrics?.lines?.length) {
    karaokeLyrics.replaceChildren();
    const message = document.createElement("p");
    message.className = "karaoke-lyric-current";
    message.textContent = "Letra sincronizada indisponível para esta música.";
    karaokeLyrics.append(message);
    karaokeNote.textContent = "O áudio de karaokê está pronto para cantar.";
    return;
  }
  if (!lyrics.synced) {
    karaokeLyrics.replaceChildren();
    karaokeLyrics.classList.add("plain-lyrics");
    lyrics.lines.forEach((line) => {
      const paragraph = document.createElement("p");
      paragraph.textContent = line.text;
      karaokeLyrics.append(paragraph);
    });
    karaokeNote.textContent = "Esta letra não tem marcações de tempo na LRCLIB.";
    return;
  }
  karaokeLyrics.classList.remove("plain-lyrics");
  karaokeNote.textContent = lyrics.approximate
    ? "Letra de outra versão na LRCLIB; a sincronização pode variar."
    : "Letra sincronizada por LRCLIB";
  updateSyncedLyrics(true);
}

function updateSyncedLyrics(force = false) {
  const lines = playlist[playlistIndex]?.lyrics?.lines;
  if (!lines?.length || !playlist[playlistIndex].lyrics.synced) return;
  const now = karaokeAudio.currentTime;
  let low = 0;
  let high = lines.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (lines[middle].time <= now) low = middle + 1;
    else high = middle;
  }
  const active = low - 1;
  if (!force && active === activeLyricIndex) return;
  activeLyricIndex = active;
  karaokeLyrics.replaceChildren();
  for (const [index, className] of [[active - 1, "karaoke-lyric-previous"], [active, "karaoke-lyric-current"], [active + 1, "karaoke-lyric-next"]]) {
    const paragraph = document.createElement("p");
    paragraph.className = className;
    paragraph.textContent = index < 0 && className === "karaoke-lyric-current"
      ? "Prepare-se para cantar…"
      : lines[index]?.text || "\u00a0";
    karaokeLyrics.append(paragraph);
  }
}

function loadKaraokeSong() {
  const job = playlist[playlistIndex];
  if (!job) return;
  activeLyricIndex = -2;
  const cover = job.artwork_url || `https://i.ytimg.com/vi/${encodeURIComponent(job.id)}/hqdefault.jpg`;
  karaokeBackdrop.style.backgroundImage = `url("${cover.replaceAll('"', '%22')}")`;
  karaokeCover.src = cover;
  karaokeCover.alt = `Capa de ${job.album || job.title}`;
  karaokeTitle.textContent = job.track || job.title;
  karaokeArtist.textContent = job.artist || job.channel;
  karaokePosition.textContent = playlist.length > 1 ? `${playlistIndex + 1} / ${playlist.length} NA FILA` : "AGORA CANTANDO";
  karaokeNext.hidden = playlistIndex >= playlist.length - 1;
  karaokeCurrentTime.textContent = "0:00";
  karaokeDuration.textContent = "0:00";
  karaokeSeek.value = "0";
  karaokeLyrics.classList.remove("plain-lyrics");
  setupStemMixer(job);
  karaokeAudio.src = `/api/karaoke?id=${encodeURIComponent(job.id)}`;
  karaokeAudio.load();
  renderLyrics();
  karaokeAudio.play().catch(() => {
    karaokeNote.textContent = "Não foi possível iniciar o áudio. Clique em Reproduzir para tentar novamente.";
    karaokeToggle.textContent = "▶ Reproduzir";
  });
}

function startKaraoke(jobs) {
  const ready = jobs.filter((job) => job.status === "ready" && job.karaoke_audio);
  if (!ready.length) return;
  stopPreview();
  playlist = ready;
  playlistIndex = 0;
  karaokeScreen.hidden = false;
  document.body.classList.add("karaoke-open");
  loadKaraokeSong();
}

function closeKaraoke() {
  clearTimeout(controlsTimer);
  karaokeScreen.classList.remove("controls-idle");
  karaokeAudio.pause();
  karaokeAudio.removeAttribute("src");
  karaokeAudio.load();
  stopStemTracks();
  stemMixer.hidden = true;
  karaokeScreen.hidden = true;
  document.body.classList.remove("karaoke-open");
  playlist = [];
}

function nextKaraokeSong() {
  if (playlistIndex + 1 < playlist.length) {
    playlistIndex += 1;
    loadKaraokeSong();
  } else {
    karaokeToggle.textContent = "▶ Recomeçar";
    karaokeNote.textContent = "Fila concluída.";
  }
}

playAllButton.addEventListener("click", () => {
  let ready = filteredJobs().filter((job) => job.status === "ready" && job.karaoke_audio);
  if (partyModeEnabled) ready = [...ready].sort(() => Math.random() - 0.5);
  startKaraoke(ready);
});
queueFilter.addEventListener("change", () => renderQueue(queueJobs));
genreFilter.addEventListener("change", () => renderQueue(queueJobs));
partyMode.addEventListener("click", () => {
  partyModeEnabled = !partyModeEnabled;
  partyMode.classList.toggle("active", partyModeEnabled);
  partyMode.textContent = partyModeEnabled ? "🎲 Festa ligada" : "🎲 Modo festa";
});
document.querySelector("#karaoke-close").addEventListener("click", closeKaraoke);
document.querySelector("#karaoke-fullscreen").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await karaokeScreen.requestFullscreen();
  } catch {
    karaokeNote.textContent = "Tela cheia não está disponível neste navegador.";
  }
});
karaokeNext.addEventListener("click", nextKaraokeSong);
karaokeToggle.addEventListener("click", () => {
  if (karaokeAudio.paused) {
    if (karaokeAudio.ended) karaokeAudio.currentTime = 0;
    karaokeAudio.play().catch(() => { karaokeNote.textContent = "Não foi possível reproduzir este áudio."; });
  } else {
    karaokeAudio.pause();
  }
});
karaokeAudio.addEventListener("play", () => { karaokeToggle.textContent = "Ⅱ Pausar"; syncStemTracks(true); });
karaokeAudio.addEventListener("play", showKaraokeControls);
karaokeAudio.addEventListener("pause", () => { karaokeToggle.textContent = "▶ Reproduzir"; syncStemTracks(false); showKaraokeControls(); });
karaokeAudio.addEventListener("loadedmetadata", () => { karaokeDuration.textContent = formatDuration(karaokeAudio.duration); });
karaokeAudio.addEventListener("timeupdate", () => {
  karaokeCurrentTime.textContent = formatDuration(karaokeAudio.currentTime);
  if (Number.isFinite(karaokeAudio.duration) && karaokeAudio.duration > 0) {
    karaokeSeek.value = String(Math.round(karaokeAudio.currentTime / karaokeAudio.duration * 1000));
  }
  Object.values(stemTracks).forEach(({ audio }) => { if (Math.abs(audio.currentTime - karaokeAudio.currentTime) > 0.04) audio.currentTime = karaokeAudio.currentTime; });
  updateSyncedLyrics();
});
karaokeAudio.addEventListener("ended", nextKaraokeSong);
karaokeAudio.addEventListener("error", () => {
  if (!karaokeScreen.hidden) karaokeNote.textContent = "Falha ao carregar o áudio do karaokê.";
});
karaokeSeek.addEventListener("input", () => {
  keepKaraokeControlsVisible();
  if (Number.isFinite(karaokeAudio.duration) && karaokeAudio.duration > 0) {
    karaokeAudio.currentTime = Number(karaokeSeek.value) / 1000 * karaokeAudio.duration;
    Object.values(stemTracks).forEach(({ audio }) => { audio.currentTime = karaokeAudio.currentTime; });
  }
});
karaokeScreen.addEventListener("pointermove", keepKaraokeControlsVisible);
karaokeScreen.addEventListener("touchstart", keepKaraokeControlsVisible, { passive: true });
karaokeScreen.addEventListener("keydown", keepKaraokeControlsVisible);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !karaokeScreen.hidden) closeKaraoke();
});

async function loadSessionInvite() {
  try {
    const response = await fetch("/api/session");
    if (!response.ok) throw new Error("Sessão indisponível");
    const data = await response.json();
    sessionUrlInput.value = data.url;
    sessionQr.src = "/api/session/qr";
  } catch (error) {
    console.error(error);
  }
}

copySessionButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(sessionUrlInput.value);
    copySessionButton.textContent = "Copiado ✓";
    setTimeout(() => { copySessionButton.textContent = "Copiar link"; }, 1600);
  } catch {
    sessionUrlInput.select();
    document.execCommand("copy");
    copySessionButton.textContent = "Copiado ✓";
  }
});

refreshQueue();
loadSessionInvite();
setInterval(refreshQueue, 2500);

openQrButton.addEventListener("click", () => {
  qrDialogImage.src = sessionQr.src;
  if (typeof qrDialog.showModal === "function") qrDialog.showModal();
  else qrDialog.setAttribute("open", "");
});
closeQrButton.addEventListener("click", () => qrDialog.close());
qrDialog.addEventListener("click", (event) => {
  if (event.target === qrDialog) qrDialog.close();
});
