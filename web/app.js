(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  const state = {
    socket: null,
    connected: false,
    pingTimer: 0,
    room: null,
    mode: "call",
    sttMode: "auto",
    callActive: false,
    callIdleTimer: 0,
    recognition: null,
    recognitionRunning: false,
    recognitionRestartTimer: 0,
    recognitionFailCount: 0,
    mediaStream: null,
    recorder: null,
    recording: false,
    botSpeaking: false,
    speakingWatchdogTimer: 0,
    audioQueue: [],
    currentAudio: null,
    currentAudioUrl: "",
    browserUtterance: null,
    draftNode: null,
    objectVideoUrl: "",
    videoSourceLabel: "",
    videoQualityLabel: "",
    dashAudioEnabled: false,
    lastWatchSpeechText: "",
    lastWatchSpeechAt: 0,
    lastFrameSentAt: 0,
    frameTimer: 0,
    frameCaptureBusy: false,
    lastSceneSignature: null,
    signatureCanvas: null,
    signatureContext: null,
    openingSent: false,
    openingTimer: 0,
    lastPlayerStateSentAt: 0,
    videoVolumeBeforeDuck: null,
    volumeAnimationFrame: 0,
    pseudoFullscreen: false,
    fullscreenSpeechToken: 0,
    fullscreenTypingTimer: 0,
    fullscreenHoldTimer: 0,
    fullscreenSafetyTimer: 0,
    fullscreenTypingDone: false,
    fullscreenSpeechStarted: false,
    fullscreenSpeechFinished: false,
    frameWarningShown: false,
    resolvingMedia: false,
    remoteCorsFallbackTried: false,
    toastTimer: 0,
    resumeToken: "",
    reconnectAttempts: 0,
    reconnectTimer: 0,
    pendingResumeTime: 0,
    videoSeekFeedbackTimer: 0,
    videoRateHoldTimer: 0,
    videoRateHoldActive: false,
    videoRateHoldSource: null,
    videoRateBeforeHold: 1,
    videoSurfaceSuppressClick: false,
  };

  function icons() {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  function showToast(message, duration = 2800) {
    const toast = $("#toast");
    window.clearTimeout(state.toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    state.toastTimer = window.setTimeout(() => { toast.hidden = true; }, duration);
  }

  function setSettingsOpen(open, restoreFocus = false) {
    const panel = $("#settingsPanel");
    const button = $("#settingsButton");
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(Boolean(open)));
    if (open) $("#closeSettings").focus();
    else if (restoreFocus) button.focus();
  }

  function setConnection(text, connected = state.connected) {
    $("#connectionStatus").textContent = text;
    $("#sendMessage").disabled = !connected;
  }

  function setRoomStatus(status, text) {
    $("#callStatus").textContent = text || "";
    $("#watchStatus").textContent = text || "";
    $("#avatarStage").dataset.state = status === "speaking" ? "speaking" : (state.callActive ? "listening" : "idle");
  }

  function isWatchFullscreen() {
    const stage = $("#videoStage");
    return document.fullscreenElement === stage || document.webkitFullscreenElement === stage || stage.classList.contains("pseudo-fullscreen");
  }

  function updateFullscreenButton() {
    const active = isWatchFullscreen();
    const button = $("#toggleFullscreen");
    button.title = active ? "退出全屏" : "进入全屏";
    button.setAttribute("aria-label", active ? "退出全屏" : "进入全屏");
    button.innerHTML = `<i data-lucide="${active ? "minimize" : "maximize"}"></i>`;
    icons();
  }

  function setPseudoFullscreen(active) {
    state.pseudoFullscreen = Boolean(active);
    $("#videoStage").classList.toggle("pseudo-fullscreen", state.pseudoFullscreen);
    document.body.classList.toggle("watch-pseudo-fullscreen", state.pseudoFullscreen);
    if (!state.pseudoFullscreen) hideFullscreenSpeech(true);
    updateFullscreenButton();
  }

  async function toggleWatchFullscreen() {
    const stage = $("#videoStage");
    if (state.pseudoFullscreen) {
      setPseudoFullscreen(false);
      return;
    }
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      try {
        if (document.exitFullscreen) await document.exitFullscreen();
        else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
      } catch { /* noop */ }
      return;
    }
    try {
      if (stage.requestFullscreen) await stage.requestFullscreen({ navigationUI: "hide" });
      else if (stage.webkitRequestFullscreen) stage.webkitRequestFullscreen();
      else setPseudoFullscreen(true);
    } catch {
      setPseudoFullscreen(true);
    }
  }

  function clearFullscreenSpeechTimers() {
    window.clearTimeout(state.fullscreenTypingTimer);
    window.clearTimeout(state.fullscreenHoldTimer);
    window.clearTimeout(state.fullscreenSafetyTimer);
  }

  function showFullscreenSpeech(text) {
    const content = String(text || "").trim();
    if (!content || state.mode !== "watch" || !isWatchFullscreen()) return;
    const overlay = $("#fullscreenCompanion");
    const output = $("#fullscreenSpeechText");
    const token = state.fullscreenSpeechToken + 1;
    state.fullscreenSpeechToken = token;
    clearFullscreenSpeechTimers();
    state.fullscreenTypingDone = false;
    state.fullscreenSpeechStarted = false;
    state.fullscreenSpeechFinished = false;
    overlay.hidden = false;
    overlay.classList.remove("avatar-visible", "bubble-visible", "typing-done", "leaving");
    output.textContent = "";
    window.requestAnimationFrame(() => {
      if (state.fullscreenSpeechToken !== token) return;
      overlay.classList.add("avatar-visible");
      window.setTimeout(() => {
        if (state.fullscreenSpeechToken !== token) return;
        overlay.classList.add("bubble-visible");
        typeFullscreenSpeech(content, token);
      }, 190);
    });
    state.fullscreenSafetyTimer = window.setTimeout(() => {
      if (state.fullscreenSpeechToken !== token) return;
      state.fullscreenSpeechFinished = true;
      maybeFinishFullscreenSpeech(token);
    }, 90000);
  }

  function typeFullscreenSpeech(text, token) {
    const characters = Array.from(text);
    const output = $("#fullscreenSpeechText");
    let index = 0;
    const baseDelay = characters.length > 80 ? 22 : (characters.length > 40 ? 28 : 36);
    const typeNext = () => {
      if (state.fullscreenSpeechToken !== token) return;
      if (index >= characters.length) {
        state.fullscreenTypingDone = true;
        $("#fullscreenCompanion").classList.add("typing-done");
        maybeFinishFullscreenSpeech(token);
        return;
      }
      const character = characters[index];
      output.textContent += character;
      index += 1;
      const punctuationPause = /[，。！？；、,.!?]/.test(character) ? 85 : 0;
      state.fullscreenTypingTimer = window.setTimeout(typeNext, baseDelay + punctuationPause);
    };
    typeNext();
  }

  function markFullscreenSpeechStarted() {
    const overlay = $("#fullscreenCompanion");
    if (overlay.hidden) return;
    state.fullscreenSpeechStarted = true;
    state.fullscreenSpeechFinished = false;
  }

  function markFullscreenSpeechFinished() {
    const overlay = $("#fullscreenCompanion");
    if (overlay.hidden) return;
    state.fullscreenSpeechFinished = true;
    maybeFinishFullscreenSpeech(state.fullscreenSpeechToken);
  }

  function maybeFinishFullscreenSpeech(token) {
    if (state.fullscreenSpeechToken !== token || !state.fullscreenTypingDone || !state.fullscreenSpeechFinished) return;
    window.clearTimeout(state.fullscreenHoldTimer);
    state.fullscreenHoldTimer = window.setTimeout(() => hideFullscreenSpeech(false, token), 2000);
  }

  function hideFullscreenSpeech(immediate = false, token = state.fullscreenSpeechToken) {
    if (state.fullscreenSpeechToken !== token) return;
    const overlay = $("#fullscreenCompanion");
    clearFullscreenSpeechTimers();
    state.fullscreenSpeechToken += 1;
    if (overlay.hidden) return;
    if (immediate) {
      overlay.hidden = true;
      overlay.classList.remove("avatar-visible", "bubble-visible", "typing-done", "leaving");
      $("#fullscreenSpeechText").textContent = "";
      return;
    }
    overlay.classList.add("leaving");
    window.setTimeout(() => {
      if (!overlay.classList.contains("leaving")) return;
      overlay.hidden = true;
      overlay.classList.remove("avatar-visible", "bubble-visible", "typing-done", "leaving");
      $("#fullscreenSpeechText").textContent = "";
    }, 560);
  }

  function ticketFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("ticket") || "";
    if (fromUrl) sessionStorage.setItem("together_room_ticket", fromUrl);
    const mode = params.get("mode");
    if (mode === "watch" || mode === "call") state.mode = mode;
    if (fromUrl) {
      params.delete("ticket");
      const clean = `${window.location.pathname}${params.toString() ? `?${params}` : ""}`;
      window.history.replaceState({}, "", clean);
    }
    return fromUrl || sessionStorage.getItem("together_room_ticket") || "";
  }

  function connect() {
    const ticket = ticketFromLocation();
    const resumeToken = state.resumeToken || sessionStorage.getItem("together_resume_token") || "";
    if (!resumeToken && !ticket) {
      setConnection("房间链接缺少凭证", false);
      showToast("请从插件拓展页或 QQ 指令重新打开房间", 5000);
      return;
    }
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const query = resumeToken
      ? `resume=${encodeURIComponent(resumeToken)}`
      : `ticket=${encodeURIComponent(ticket)}`;
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws?${query}`);
    state.socket = socket;
    setConnection(resumeToken ? "正在恢复房间" : "正在连接", false);

    socket.addEventListener("open", () => setConnection("正在确认房间", false));
    socket.addEventListener("message", (event) => {
      try { handleServerMessage(JSON.parse(event.data)); }
      catch (error) { console.error("Invalid room payload", error); }
    });
    socket.addEventListener("close", () => {
      if (state.socket !== socket) return;
      window.clearInterval(state.pingTimer);
      const wasConnected = state.connected;
      state.connected = false;
      if (!wasConnected) {
        // 握手失败：resume 凭证可能已失效，清除后回退票据重试
        if (state.resumeToken || sessionStorage.getItem("together_resume_token")) {
          state.resumeToken = "";
          sessionStorage.removeItem("together_resume_token");
          scheduleReconnect();
          return;
        }
        setConnection("无法连接房间", false);
        showToast("请从插件拓展页或 QQ 指令重新打开房间", 5000);
        return;
      }
      $("#watchVideo").pause();
      stopCall(false);
      stopAudio();
      setConnection("房间已断开，正在重连", false);
      scheduleReconnect();
    });
    socket.addEventListener("error", () => {
      if (!state.connected) setConnection("无法连接房间", false);
    });
  }

  function scheduleReconnect() {
    window.clearTimeout(state.reconnectTimer);
    if (state.reconnectAttempts >= 8) {
      setConnection("房间已断开", false);
      showToast("房间连接已关闭，请重新从入口打开", 5000);
      return;
    }
    const delay = Math.min(8000, 1000 * (2 ** state.reconnectAttempts));
    state.reconnectAttempts += 1;
    state.reconnectTimer = window.setTimeout(connect, delay);
  }

  function send(payload) {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
      showToast("房间尚未连接");
      return false;
    }
    state.socket.send(JSON.stringify(payload));
    return true;
  }

  function clearCallIdleTimer() {
    window.clearTimeout(state.callIdleTimer);
    state.callIdleTimer = 0;
  }

  function scheduleCallIdleTimer() {
    clearCallIdleTimer();
    if (!state.connected || !state.callActive || state.mode !== "call" || state.botSpeaking) return;
    if (!state.room?.call?.proactive_enabled) return;
    const idleSeconds = Math.max(60, Math.min(900, Number(state.room.call.idle_seconds) || 120));
    state.callIdleTimer = window.setTimeout(() => {
      if (state.callActive && state.mode === "call" && !state.botSpeaking) {
        send({ type: "call_idle" });
        scheduleCallIdleTimer();
      }
    }, idleSeconds * 1000);
  }

  function noteCallActivity() {
    if (state.callActive && state.mode === "call") scheduleCallIdleTimer();
  }

  function handleServerMessage(message) {
    switch (message.type) {
      case "ready":
        state.connected = true;
        state.reconnectAttempts = 0;
        state.room = message.room || {};
        if (message.resume_token) {
          state.resumeToken = message.resume_token;
          sessionStorage.setItem("together_resume_token", message.resume_token);
        }
        configureRoom();
        if (message.resumed) showToast("已恢复房间");
        setConnection("已连接", true);
        window.clearInterval(state.pingTimer);
        state.pingTimer = window.setInterval(() => {
          if (state.socket?.readyState === WebSocket.OPEN) {
            state.socket.send(JSON.stringify({ type: "ping" }));
          }
        }, 45000);
        break;
      case "status":
        if (state.botSpeaking && message.state !== "speaking") break;
        setRoomStatus(message.state, message.text);
        break;
      case "mode":
        setMode(message.mode, false);
        break;
      case "media_resolving":
        state.resolvingMedia = true;
        $("#videoTitle").textContent = "正在解析视频";
        $("#watchStatus").textContent = "正在解析";
        setVideoPlaceholder(message.message || "正在解析视频链接", true);
        break;
      case "media_ready":
        state.resolvingMedia = false;
        $("#watchStatus").textContent = "正在加载";
        state.pendingResumeTime = Math.max(0, Number(message.resume_time) || 0);
        loadVideo(
          message.url,
          message.title || "B 站视频",
          false,
          message.quality_label || "",
          message.playback_mode === "dash" ? message.audio_url : "",
        );
        break;
      case "media_error":
        state.resolvingMedia = false;
        $("#videoTitle").textContent = "尚未选择视频";
        $("#watchStatus").textContent = "等待视频";
        setVideoPlaceholder("选择一段视频", true);
        showToast(message.message || "视频链接解析失败", 5000);
        break;
      case "user_text":
        clearDraft();
        addMessage("user", message.text);
        break;
      case "bot_delta":
        appendDraft(message.text);
        break;
      case "bot_text":
        clearDraft();
        addMessage(
          "bot",
          message.text,
          message.source === "watch_comment" ? "观影" : (message.source === "call_proactive" ? "主动" : ""),
        );
        showFullscreenSpeech(message.text);
        scheduleCallIdleTimer();
        break;
      case "audio":
        clearDraft();
        if (isDuplicateWatchSpeech(message)) break;
        enqueueAudio(message);
        break;
      case "tts_fallback":
        clearDraft();
        if (isDuplicateWatchSpeech(message)) break;
        if (state.room?.tts?.browser_fallback) {
          speakInBrowser(message.text, message.language, message.display_text, message.source);
        }
        else {
          revealSpeechMessage(message);
          markFullscreenSpeechStarted();
          markFullscreenSpeechFinished();
        }
        break;
      case "stop_audio":
        clearDraft();
        stopAudio(false);
        break;
      case "notice":
        showToast(message.message || "房间状态发生变化", 4200);
        break;
      case "error":
        clearDraft();
        setRoomStatus(state.callActive ? "listening" : "idle", state.callActive ? "正在听" : "等待接通");
        showToast(message.message || "房间处理失败", 4500);
        break;
      default:
        break;
    }
  }

  function configureRoom() {
    const room = state.room || {};
    const botName = room.bot_name || "Bot";
    $("#brandName").textContent = botName;
    $("#callName").textContent = botName;
    $("#brandAvatar").src = room.avatar_url || "/avatar";
    $("#callAvatar").src = room.avatar_url || "/avatar";
    $("#fullscreenAvatar").src = room.avatar_url || "/avatar";
    state.sttMode = room.stt?.mode || "auto";
    setActiveSttButton(state.sttMode);
    $("#chatCapability").textContent = room.chat?.available ? (room.chat.label || "可用") : "未配置";
    $("#visionCapability").textContent = room.vision?.available ? (room.vision.label || "可用") : "未配置";
    $("#sttCapability").textContent = room.stt?.server_available
      ? (room.stt.server_label || "AstrBot STT")
      : (SpeechRecognition ? "浏览器免配置" : "仅文字可用");
    $("#ttsCapability").textContent = room.tts?.server_available
      ? (room.tts.server_label || "可用")
      : (room.tts?.browser_fallback ? "浏览器回退" : "未配置");
    const browserButton = $('[data-stt-mode="browser"]');
    const astrbotButton = $('[data-stt-mode="astrbot"]');
    browserButton.disabled = !SpeechRecognition;
    browserButton.title = SpeechRecognition ? "使用浏览器语音识别" : "当前浏览器不支持 Web Speech 语音识别";
    astrbotButton.disabled = !room.stt?.server_available;
    astrbotButton.title = room.stt?.server_available ? "使用 AstrBot STT Provider" : "AstrBot 尚未配置 STT Provider";
    $("#autoComment").checked = Boolean(room.watch?.auto_comment);
    setMode(state.mode || room.mode || "call");
    resetSceneMonitor();
  }

  function setMode(mode, notify = true) {
    const next = mode === "watch" ? "watch" : "call";
    const previous = state.mode;
    if (previous !== next && next === "watch" && state.callActive) stopCall(false);
    if (previous !== next && previous === "watch") $("#watchVideo").pause();
    if (previous !== next && state.botSpeaking) stopAudio();
    state.mode = next;
    document.body.dataset.mode = next;
    $$("[data-mode-tab]").forEach((button) => button.classList.toggle("active", button.dataset.modeTab === next));
    $("#callView").classList.toggle("active", next === "call");
    $("#watchView").classList.toggle("active", next === "watch");
    if (notify) send({ type: "set_mode", mode: next });
    updatePlayerState();
  }

  function transcriptNodes() {
    return [$("#callTranscript"), $("#watchTranscript")];
  }

  function addMessage(role, text, label = "") {
    const content = String(text || "").trim();
    if (!content) return;
    transcriptNodes().forEach((container) => {
      const item = document.createElement("div");
      item.className = `message ${role}`;
      if (label) {
        const small = document.createElement("small");
        small.textContent = label;
        item.appendChild(small);
      }
      const span = document.createElement("span");
      span.textContent = content;
      item.appendChild(span);
      container.appendChild(item);
      while (container.children.length > 40) container.firstElementChild?.remove();
      container.scrollTop = container.scrollHeight;
    });
  }

  function appendDraft(text) {
    if (!state.draftNode) {
      state.draftNode = [];
      transcriptNodes().forEach((container) => {
        const item = document.createElement("div");
        item.className = "message bot draft";
        item.textContent = "";
        container.appendChild(item);
        state.draftNode.push(item);
      });
    }
    state.draftNode.forEach((node) => {
      node.textContent += String(text || "");
      node.parentElement.scrollTop = node.parentElement.scrollHeight;
    });
  }

  function clearDraft() {
    if (!state.draftNode) return;
    state.draftNode.forEach((node) => node.remove());
    state.draftNode = null;
  }

  function resolvedSttMode() {
    if (state.sttMode === "browser") return "browser";
    if (state.sttMode === "astrbot") return "astrbot";
    if (SpeechRecognition) return "browser";
    return state.room?.stt?.server_available ? "astrbot" : "none";
  }

  function setActiveSttButton(mode) {
    $$("[data-stt-mode]").forEach((button) => button.classList.toggle("active", button.dataset.sttMode === mode));
  }

  async function startCall() {
    if (!state.connected || state.callActive) return;
    const mode = resolvedSttMode();
    if (mode === "none") {
      showToast("当前浏览器不支持语音识别，且 AstrBot STT 未配置");
      return;
    }
    if (mode === "astrbot" && !state.room?.stt?.server_available) {
      showToast("AstrBot STT 尚未配置，请切换浏览器识别");
      return;
    }
    try {
      if (mode === "browser") {
        await requestMicrophonePermission();
        createRecognition();
      } else {
        state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      }
    } catch (error) {
      showToast(error?.message || "无法获得麦克风权限", 4200);
      return;
    }
    state.callActive = true;
    send({ type: "call_state", active: true });
    $("#callToggle").classList.add("active");
    $("#callToggle").title = "挂断";
    $("#callToggle").setAttribute("aria-label", "挂断");
    $("#callToggle").innerHTML = '<i data-lucide="phone-off"></i>';
    const pushMode = mode === "astrbot";
    $("#holdToTalk").hidden = !pushMode;
    $("#holdHint").hidden = !pushMode;
    icons();
    setRoomStatus("listening", pushMode ? "等待你按住麦克风" : "正在听");
    if (mode === "browser") startRecognition();
    scheduleCallIdleTimer();
  }

  function stopCall(notify = true) {
    const wasActive = state.callActive;
    state.callActive = false;
    clearCallIdleTimer();
    window.clearTimeout(state.recognitionRestartTimer);
    if (state.recognition) {
      try { state.recognition.abort(); } catch { /* noop */ }
    }
    state.recognition = null;
    state.recognitionRunning = false;
    stopRecording(true);
    if (state.mediaStream) state.mediaStream.getTracks().forEach((track) => track.stop());
    state.mediaStream = null;
    const button = $("#callToggle");
    button.classList.remove("active");
    button.title = "开始通话";
    button.setAttribute("aria-label", "开始通话");
    button.innerHTML = '<i data-lucide="phone"></i>';
    $("#holdToTalk").hidden = true;
    $("#holdHint").hidden = true;
    setRoomStatus("idle", "等待接通");
    if (wasActive && state.connected) send({ type: "call_state", active: false });
    if (notify) send({ type: "interrupt" });
    icons();
  }

  async function requestMicrophonePermission() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前环境不支持麦克风访问");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((track) => track.stop());
  }

  function createRecognition() {
    if (!SpeechRecognition) throw new Error("当前浏览器不支持免配置语音识别，请使用 Edge 或 Chrome");
    const recognition = new SpeechRecognition();
    recognition.lang = state.room?.stt?.browser_language || "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 3;
    recognition.addEventListener("start", () => {
      state.recognitionRunning = true;
      state.recognitionFailCount = 0;
      if (!state.botSpeaking) setRoomStatus("listening", "正在听");
    });
    recognition.addEventListener("result", (event) => {
      let interim = "";
      let finalText = "";
      const finalAlternatives = ["", "", ""];
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const text = result?.[0]?.transcript || "";
        if (result.isFinal) {
          finalText += text;
          for (let alternativeIndex = 0; alternativeIndex < Math.min(3, result.length); alternativeIndex += 1) {
            finalAlternatives[alternativeIndex] += result[alternativeIndex]?.transcript || "";
          }
        }
        else interim += text;
      }
      if (interim) setRoomStatus("listening", interim.slice(0, 80));
      if (interim || finalText.trim()) noteCallActivity();
      if (finalText.trim()) {
        const alternatives = finalAlternatives.map((item) => item.trim()).filter(Boolean);
        sendUserText(finalText.trim(), "browser_stt", alternatives);
      }
    });
    recognition.addEventListener("error", (event) => {
      if (!["no-speech", "aborted"].includes(event.error)) {
        state.recognitionFailCount = Math.min(state.recognitionFailCount + 1, 5);
        // 持续失败时退避重启，toast 只报前两次避免刷屏
        if (state.recognitionFailCount <= 2) showToast(`浏览器语音识别：${event.error}`);
      }
      if (["not-allowed", "service-not-allowed"].includes(event.error)) stopCall(false);
    });
    recognition.addEventListener("end", () => {
      state.recognitionRunning = false;
      scheduleRecognitionRestart();
    });
    state.recognition = recognition;
  }

  function startRecognition() {
    if (!state.callActive || state.botSpeaking || state.recognitionRunning || !state.recognition) return;
    try { state.recognition.start(); }
    catch {
      state.recognitionFailCount = Math.min(state.recognitionFailCount + 1, 5);
      scheduleRecognitionRestart();
    }
  }

  function scheduleRecognitionRestart() {
    window.clearTimeout(state.recognitionRestartTimer);
    if (!state.callActive || state.botSpeaking || resolvedSttMode() !== "browser") return;
    const delay = Math.min(5000, 280 * (2 ** state.recognitionFailCount));
    state.recognitionRestartTimer = window.setTimeout(startRecognition, delay);
  }

  function pauseRecognitionForBot() {
    state.botSpeaking = true;
    clearCallIdleTimer();
    // 看门狗：音频或朗读事件丢失时防止麦克风永久停摆
    window.clearTimeout(state.speakingWatchdogTimer);
    state.speakingWatchdogTimer = window.setTimeout(() => {
      if (!state.botSpeaking) return;
      stopAudio();
      resumeRecognitionAfterBot();
    }, 90000);
    if (state.recognitionRunning && state.recognition) {
      try { state.recognition.abort(); } catch { /* noop */ }
    }
    duckVideoForBot();
    setRoomStatus("speaking", "正在说话");
  }

  function resumeRecognitionAfterBot() {
    window.clearTimeout(state.speakingWatchdogTimer);
    state.botSpeaking = false;
    restoreVideoVolume();
    if (state.mode === "watch") {
      const video = $("#watchVideo");
      setRoomStatus("watching", video.ended ? "已经看完" : (video.paused ? "已经暂停" : "一起看着"));
    } else {
      setRoomStatus(state.callActive ? "listening" : "idle", state.callActive ? "正在听" : "等待接通");
    }
    scheduleRecognitionRestart();
    scheduleCallIdleTimer();
  }

  function animateVideoVolume(target, duration = 180) {
    const video = $("#watchVideo");
    window.cancelAnimationFrame(state.volumeAnimationFrame);
    const start = video.volume;
    const startedAt = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      video.volume = Math.max(0, Math.min(1, start + (target - start) * progress));
      if (progress < 1) state.volumeAnimationFrame = window.requestAnimationFrame(step);
    };
    state.volumeAnimationFrame = window.requestAnimationFrame(step);
  }

  function duckVideoForBot() {
    const video = $("#watchVideo");
    if (state.mode !== "watch" || !state.room?.watch?.duck_video_volume || video.muted || video.volume <= 0) return;
    if (state.videoVolumeBeforeDuck === null) state.videoVolumeBeforeDuck = video.volume;
    const ratio = Math.max(.05, Math.min(.8, Number(state.room?.watch?.duck_volume_ratio || .28)));
    animateVideoVolume(state.videoVolumeBeforeDuck * ratio);
  }

  function restoreVideoVolume() {
    if (state.videoVolumeBeforeDuck === null) return;
    const target = state.videoVolumeBeforeDuck;
    state.videoVolumeBeforeDuck = null;
    animateVideoVolume(target, 240);
  }

  async function startRecording() {
    if (!state.callActive || state.recording || resolvedSttMode() !== "astrbot") return;
    noteCallActivity();
    if (!state.mediaStream) {
      try { state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (error) { showToast(error?.message || "无法使用麦克风"); return; }
    }
    const preferred = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
      .find((type) => window.MediaRecorder?.isTypeSupported?.(type));
    try {
      const chunks = [];
      const recorder = preferred ? new MediaRecorder(state.mediaStream, { mimeType: preferred }) : new MediaRecorder(state.mediaStream);
      recorder.addEventListener("dataavailable", (event) => { if (event.data?.size) chunks.push(event.data); });
      recorder.addEventListener("stop", async () => {
        state.recording = false;
        $("#holdToTalk").classList.remove("recording");
        if (recorder.__discard) return;
        if (!chunks.length) return;
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        // base64 膨胀 4/3 后需低于服务端 16MiB 消息上限，留足信封余量
        if (blob.size > 10 * 1024 * 1024) { showToast("这段语音太长，请分开说"); return; }
        const data = await blobToBase64(blob);
        send({ type: "audio_utterance", mime: blob.type || "audio/webm", data });
      });
      state.recorder = recorder;
      state.recording = true;
      $("#holdToTalk").classList.add("recording");
      setRoomStatus("listening", "正在录音");
      recorder.start(250);
    } catch (error) {
      state.recording = false;
      showToast(error?.message || "无法开始录音");
    }
  }

  function stopRecording(discard = false) {
    if (!state.recorder) return;
    const recorder = state.recorder;
    recorder.__discard = Boolean(discard);
    if (state.recorder.state !== "inactive") {
      try { state.recorder.stop(); } catch { /* noop */ }
    }
    state.recorder = null;
  }

  async function blobToBase64(blob) {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    const size = 0x8000;
    for (let index = 0; index < bytes.length; index += size) {
      binary += String.fromCharCode(...bytes.subarray(index, index + size));
    }
    return btoa(binary);
  }

  async function sendUserText(text, source = "text", alternatives = []) {
    const value = String(text || "").trim();
    if (!value) return;
    const frame = state.mode === "watch" ? await captureFrameData(720, .76) : "";
    if (state.botSpeaking) stopAudio();
    noteCallActivity();
    if (send({ type: "user_text", text: value, source, alternatives, state: playerState(), frame })) {
      $("#messageInput").value = "";
    }
  }

  function revealSpeechMessage(message) {
    if (!message || message.revealed) return;
    message.revealed = true;
    const visible = String(message.display_text || message.text || "").trim();
    if (!visible) return;
    addMessage("bot", visible, message.source === "watch_comment" ? "观影" : "");
    showFullscreenSpeech(visible);
  }

  function isDuplicateWatchSpeech(message) {
    if (message?.source !== "watch_comment") return false;
    const text = String(message.display_text || message.text || "").trim();
    if (!text) return false;
    const now = Date.now();
    const duplicate = state.lastWatchSpeechText === text && now - state.lastWatchSpeechAt < 12000;
    if (!duplicate) {
      state.lastWatchSpeechText = text;
      state.lastWatchSpeechAt = now;
    }
    return duplicate;
  }

  function enqueueAudio(message) {
    state.audioQueue.push({
      data: message.data,
      mime: message.mime || "audio/wav",
      text: message.text,
      display_text: message.display_text,
      source: message.source,
      revealed: false,
    });
    if (!state.currentAudio) playNextAudio();
  }

  function playNextAudio() {
    if (!state.audioQueue.length) {
      state.currentAudio = null;
      markFullscreenSpeechFinished();
      resumeRecognitionAfterBot();
      return;
    }
    pauseRecognitionForBot();
    const item = state.audioQueue.shift();
    try {
      const binary = atob(item.data || "");
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      const url = URL.createObjectURL(new Blob([bytes], { type: item.mime }));
      const audio = new Audio(url);
      state.currentAudio = audio;
      state.currentAudioUrl = url;
      const finish = () => finishCurrentAudio(audio, url);
      const fail = () => {
        revealSpeechMessage(item);
        markFullscreenSpeechStarted();
        finish();
      };
      audio.addEventListener("playing", () => {
        revealSpeechMessage(item);
        markFullscreenSpeechStarted();
      }, { once: true });
      audio.addEventListener("ended", finish, { once: true });
      audio.addEventListener("error", fail, { once: true });
      audio.play().catch(() => {
        fail();
        showToast("浏览器阻止了音频播放，请点击页面后重试");
      });
    } catch {
      revealSpeechMessage(item);
      markFullscreenSpeechStarted();
      playNextAudio();
    }
  }

  function finishCurrentAudio(audio, url) {
    if (url) URL.revokeObjectURL(url);
    if (state.currentAudio !== audio) return;
    state.currentAudioUrl = "";
    state.currentAudio = null;
    playNextAudio();
  }

  function speakInBrowser(text, language = "", displayText = "", source = "") {
    const message = { text, display_text: displayText, source, revealed: false };
    if (!window.speechSynthesis || !String(text || "").trim()) {
      revealSpeechMessage(message);
      markFullscreenSpeechStarted();
      markFullscreenSpeechFinished();
      return;
    }
    stopAudio(true);
    pauseRecognitionForBot();
    const utterance = new SpeechSynthesisUtterance(String(text));
    utterance.lang = language || state.room?.tts?.browser_language || "zh-CN";
    utterance.rate = 1.03;
    const finish = () => {
      if (state.browserUtterance !== utterance) return;
      revealSpeechMessage(message);
      state.browserUtterance = null;
      markFullscreenSpeechFinished();
      resumeRecognitionAfterBot();
    };
    utterance.addEventListener("start", () => {
      revealSpeechMessage(message);
      markFullscreenSpeechStarted();
    }, { once: true });
    utterance.addEventListener("end", finish, { once: true });
    utterance.addEventListener("error", finish, { once: true });
    state.browserUtterance = utterance;
    window.speechSynthesis.speak(utterance);
  }

  function stopAudio(preserveFullscreenSpeech = false) {
    state.audioQueue.length = 0;
    const audio = state.currentAudio;
    const audioUrl = state.currentAudioUrl;
    state.currentAudio = null;
    state.currentAudioUrl = "";
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    state.browserUtterance = null;
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    resumeRecognitionAfterBot();
    if (!preserveFullscreenSpeech) hideFullscreenSpeech(true);
  }

  function openLocalVideo(file) {
    if (!file) return;
    if (state.objectVideoUrl) URL.revokeObjectURL(state.objectVideoUrl);
    state.objectVideoUrl = URL.createObjectURL(file);
    state.videoSourceLabel = file.name || "本地视频";
    loadVideo(state.objectVideoUrl, state.videoSourceLabel, false);
  }

  function setVideoPlaceholder(text, visible) {
    const empty = $("#videoEmpty");
    const label = empty.querySelector("strong");
    if (label) label.textContent = text || "选择一段视频";
    empty.hidden = !visible;
  }

  function videoHasSource() {
    const video = $("#watchVideo");
    return Boolean(video.currentSrc || video.getAttribute("src"));
  }

  function dashAudio() {
    return state.dashAudioEnabled ? $("#watchAudio") : null;
  }

  function resetDashAudio(url = "") {
    const audio = $("#watchAudio");
    audio.pause();
    audio.removeAttribute("src");
    state.dashAudioEnabled = Boolean(url);
    if (url) audio.src = url;
    audio.load();
  }

  function syncDashAudio({ seek = false, play = false } = {}) {
    const audio = dashAudio();
    if (!audio) return;
    const video = $("#watchVideo");
    audio.playbackRate = video.playbackRate || 1;
    audio.volume = video.volume;
    audio.muted = video.muted;
    if (Number.isFinite(video.currentTime) && Number.isFinite(audio.duration)) {
      const drift = Math.abs((audio.currentTime || 0) - video.currentTime);
      if (seek || drift > 0.3) {
        try { audio.currentTime = Math.min(video.currentTime, Math.max(0, audio.duration - 0.05)); }
        catch { /* audio metadata is not ready yet */ }
      }
    }
    if (video.paused || video.ended) audio.pause();
    else if (play && audio.paused) audio.play().catch(() => {});
  }

  function setVideoControlIcon(button, icon, label) {
    if (!button || button.dataset.icon === icon) return;
    button.dataset.icon = icon;
    button.innerHTML = `<i data-lucide="${icon}"></i>`;
    if (label) {
      button.title = label;
      button.setAttribute("aria-label", label);
    }
    icons();
  }

  function updateVideoControls() {
    const video = $("#watchVideo");
    const hasSource = videoHasSource();
    const duration = Number.isFinite(video.duration) ? Math.max(0, video.duration) : 0;
    const current = Number.isFinite(video.currentTime) ? Math.max(0, video.currentTime) : 0;
    const playing = hasSource && !video.paused && !video.ended;
    $("#videoStage").classList.toggle("is-playing", playing);
    setVideoControlIcon($("#videoPlayPause"), playing ? "pause" : "play", playing ? "暂停" : "播放");
    setVideoControlIcon($("#videoCenterToggle"), "play", "播放");
    setVideoControlIcon(
      $("#videoMute"),
      video.muted || video.volume <= 0 ? "volume-x" : (video.volume < .5 ? "volume-1" : "volume-2"),
      video.muted || video.volume <= 0 ? "取消静音" : "静音",
    );
    const progress = $("#videoProgress");
    progress.disabled = !hasSource || duration <= 0;
    const progressRatio = duration > 0 ? Math.max(0, Math.min(1, current / duration)) : 0;
    progress.value = String(Math.round(progressRatio * 1000));
    progress.style.setProperty("--video-progress", `${progressRatio * 100}%`);
    $("#videoTime").textContent = `${formatTime(current)} / ${formatTime(duration)}`;
    const visibleVolume = video.muted ? 0 : video.volume;
    $("#videoVolume").value = String(visibleVolume);
    $("#videoVolume").style.setProperty("--video-volume", `${visibleVolume * 100}%`);
    if (!state.videoRateHoldActive) $("#videoRate").value = String(video.playbackRate || 1);
    ["videoPlayPause", "videoSeekBack", "videoSeekForward", "videoMute"].forEach((id) => {
      $(`#${id}`).disabled = !hasSource;
    });
    $("#videoCenterToggle").disabled = !hasSource;
  }

  async function toggleVideoPlayback() {
    const video = $("#watchVideo");
    const audio = dashAudio();
    if (!videoHasSource()) return;
    if (!video.paused && !video.ended) {
      video.pause();
      audio?.pause();
      return;
    }
    try {
      syncDashAudio({ seek: true });
      const starts = [video.play()];
      if (audio) starts.push(audio.play());
      await Promise.all(starts);
    } catch (error) {
      showToast(error?.message || "视频暂时无法播放");
    }
  }

  function showVideoSeekFeedback(text, direction, duration = 700) {
    const feedback = $("#videoSeekFeedback");
    window.clearTimeout(state.videoSeekFeedbackTimer);
    feedback.textContent = text;
    feedback.dataset.direction = direction;
    feedback.hidden = false;
    if (duration > 0) {
      state.videoSeekFeedbackTimer = window.setTimeout(() => { feedback.hidden = true; }, duration);
    }
  }

  function hideVideoSeekFeedback() {
    window.clearTimeout(state.videoSeekFeedbackTimer);
    $("#videoSeekFeedback").hidden = true;
  }

  function seekVideoBy(seconds) {
    const video = $("#watchVideo");
    if (!videoHasSource() || !Number.isFinite(video.duration)) return;
    video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + seconds));
    showVideoSeekFeedback(`${seconds > 0 ? "+" : ""}${seconds} 秒`, seconds < 0 ? "back" : "forward");
    updateVideoControls();
  }

  function activateTemporaryVideoRate(source) {
    const video = $("#watchVideo");
    if (video.paused || video.ended || !videoHasSource()) return false;
    state.videoRateHoldActive = true;
    state.videoRateHoldSource = source;
    state.videoRateBeforeHold = video.playbackRate || 1;
    video.playbackRate = 3;
    source?.classList?.add("holding");
    showVideoSeekFeedback("3x", "rate", 0);
    return true;
  }

  function beginTemporaryVideoRate(source) {
    window.clearTimeout(state.videoRateHoldTimer);
    if ($("#watchVideo").paused) return;
    state.videoRateHoldTimer = window.setTimeout(() => activateTemporaryVideoRate(source), 360);
  }

  function endTemporaryVideoRate() {
    window.clearTimeout(state.videoRateHoldTimer);
    state.videoRateHoldTimer = 0;
    if (!state.videoRateHoldActive) return false;
    const video = $("#watchVideo");
    video.playbackRate = state.videoRateBeforeHold || 1;
    state.videoRateHoldSource?.classList?.remove("holding");
    state.videoRateHoldActive = false;
    state.videoRateHoldSource = null;
    hideVideoSeekFeedback();
    updateVideoControls();
    return true;
  }

  function isBilibiliPageUrl(url) {
    try {
      const host = new URL(url).hostname.toLowerCase().replace(/\.$/, "");
      return host === "bilibili.com" || host.endsWith(".bilibili.com") || host === "b23.tv" || host.endsWith(".b23.tv");
    } catch {
      return false;
    }
  }

  function loadVideo(url, title, remote, qualityLabel = "", audioUrl = "") {
    const video = $("#watchVideo");
    video.pause();
    resetDashAudio(audioUrl);
    video.removeAttribute("src");
    if (remote) video.crossOrigin = "anonymous";
    else video.removeAttribute("crossorigin");
    video.src = url;
    video.load();
    updateVideoControls();
    state.videoSourceLabel = title || "视频";
    state.videoQualityLabel = String(qualityLabel || "");
    state.lastWatchSpeechText = "";
    state.lastWatchSpeechAt = 0;
    state.remoteCorsFallbackTried = false;
    $("#videoTitle").textContent = state.videoSourceLabel;
    setVideoPlaceholder("正在加载视频", true);
    state.frameWarningShown = false;
    state.lastFrameSentAt = Date.now();
    state.lastSceneSignature = null;
    state.openingSent = false;
    window.clearTimeout(state.openingTimer);
    updatePlayerState("loaded");
  }

  function playerState() {
    const video = $("#watchVideo");
    return {
      title: state.videoSourceLabel || "",
      source: state.objectVideoUrl ? "local" : (video.currentSrc || ""),
      current_time: Number.isFinite(video.currentTime) ? video.currentTime : 0,
      duration: Number.isFinite(video.duration) ? video.duration : 0,
      paused: video.paused,
      playback_rate: video.playbackRate || 1,
    };
  }

  function updatePlayerState(eventName = "") {
    const current = playerState();
    const quality = state.videoQualityLabel ? ` · ${state.videoQualityLabel}` : "";
    $("#videoMeta").textContent = `${formatTime(current.current_time)} / ${formatTime(current.duration)}${quality}`;
    if (state.connected) send({ type: "player_state", state: current, event: eventName });
    state.lastPlayerStateSentAt = Date.now();
  }

  function formatTime(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }

  function resetSceneMonitor() {
    window.clearInterval(state.frameTimer);
    state.frameTimer = window.setInterval(() => {
      const video = $("#watchVideo");
      if (state.mode !== "watch" || !$("#autoComment").checked || video.paused || video.readyState < 2) return;
      // 页面不可见时暂停画面观察，避免后台持续抓帧耗电耗流量
      if (document.visibilityState === "hidden" || state.frameCaptureBusy) return;
      const signature = sceneSignature(video);
      if (!signature) return;
      if (!state.lastSceneSignature) {
        state.lastSceneSignature = signature;
        return;
      }
      let difference = 0;
      for (let index = 0; index < signature.length; index += 1) {
        difference += Math.abs(signature[index] - state.lastSceneSignature[index]);
      }
      difference /= signature.length * 255;
      state.lastSceneSignature = signature;
      const elapsed = Date.now() - state.lastFrameSentAt;
      const sceneInterval = Math.max(8, Number(state.room?.watch?.scene_min_interval_seconds || 18)) * 1000;
      const heartbeat = Math.max(sceneInterval, Number(state.room?.watch?.comment_interval_seconds || 60) * 1000);
      if (difference >= .16 && elapsed >= sceneInterval) {
        captureAndSendFrame("scene_change", difference);
      } else if (elapsed >= heartbeat) {
        captureAndSendFrame("heartbeat", difference);
      }
    }, 1600);
  }

  function signatureCanvas() {
    // 场景签名使用独立的 32x18 画布，与大图抓帧画布分离，避免反复重建缓冲与上下文属性冲突
    if (!state.signatureCanvas) {
      const canvas = document.createElement("canvas");
      canvas.width = 32;
      canvas.height = 18;
      state.signatureCanvas = canvas;
      state.signatureContext = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
    }
    return state.signatureContext;
  }

  function sceneSignature(video) {
    try {
      const context = signatureCanvas();
      if (!context) return null;
      context.drawImage(video, 0, 0, 32, 18);
      const pixels = context.getImageData(0, 0, 32, 18).data;
      const signature = new Uint8Array(32 * 18);
      for (let source = 0, target = 0; source < pixels.length; source += 4, target += 1) {
        signature[target] = Math.round(pixels[source] * .299 + pixels[source + 1] * .587 + pixels[source + 2] * .114);
      }
      return signature;
    } catch {
      return null;
    }
  }

  function captureFrameData(maxWidth = 640, quality = .74) {
    // 异步 toBlob 编码，避免大图 toDataURL 阻塞主线程
    return new Promise((resolve) => {
      const video = $("#watchVideo");
      if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) { resolve(""); return; }
      const canvas = $("#frameCanvas");
      const scale = Math.min(1, maxWidth / video.videoWidth);
      const width = Math.max(1, Math.round(video.videoWidth * scale));
      const height = Math.max(1, Math.round(video.videoHeight * scale));
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      try {
        const context = canvas.getContext("2d", { alpha: false });
        context.drawImage(video, 0, 0, width, height);
        canvas.toBlob((blob) => {
          if (!blob) { resolve(""); return; }
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = () => resolve("");
          reader.readAsDataURL(blob);
        }, "image/jpeg", quality);
      } catch {
        resolve("");
      }
    });
  }

  async function captureAndSendFrame(trigger = "heartbeat", sceneScore = 0) {
    const manual = trigger === "manual";
    if (!manual && (!$("#autoComment").checked || state.botSpeaking)) return;
    if (state.frameCaptureBusy) return;
    if (manual && state.botSpeaking) {
      stopAudio();
      send({ type: "interrupt" });
    }
    const video = $("#watchVideo");
    state.frameCaptureBusy = true;
    let image = "";
    try {
      image = await captureFrameData();
    } finally {
      state.frameCaptureBusy = false;
    }
    if (!image) {
      if (!state.frameWarningShown || manual) {
        showToast(manual ? "当前还没有可读取的视频画面" : "该视频源不允许读取画面，播放和聊天仍可继续", 4200);
      }
      state.frameWarningShown = true;
      state.lastFrameSentAt = Date.now();
      return;
    }
    state.frameWarningShown = false;
    state.lastFrameSentAt = Date.now();
    const sent = send({
      type: "watch_frame",
      image,
      state: playerState(),
      trigger,
      captured_at: Number.isFinite(video.currentTime) ? video.currentTime : 0,
      scene_score: Number(sceneScore) || 0,
      manual,
    });
    if (manual && sent) showToast("正在一起看这幕");
  }

  function bindEvents() {
    $$("[data-mode-tab]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.modeTab)));
    $("#settingsButton").addEventListener("click", () => setSettingsOpen($("#settingsPanel").hidden));
    $("#closeSettings").addEventListener("click", () => setSettingsOpen(false, true));
    $$("[data-stt-mode]").forEach((button) => button.addEventListener("click", () => {
      const wasActive = state.callActive;
      if (wasActive) stopCall(true);
      state.sttMode = button.dataset.sttMode;
      setActiveSttButton(state.sttMode);
      if (wasActive) startCall();
    }));
    $("#callToggle").addEventListener("click", () => state.callActive ? stopCall(true) : startCall());
    $("#interruptButton").addEventListener("click", () => { send({ type: "interrupt" }); stopAudio(); });
    $("#toggleFullscreen").addEventListener("click", toggleWatchFullscreen);
    const onFullscreenChange = () => {
      if (!isWatchFullscreen()) hideFullscreenSpeech(true);
      updateFullscreenButton();
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("webkitfullscreenchange", onFullscreenChange);
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("#settingsPanel").hidden) setSettingsOpen(false, true);
      else if (state.pseudoFullscreen) setPseudoFullscreen(false);
    });

    const hold = $("#holdToTalk");
    hold.addEventListener("pointerdown", (event) => { event.preventDefault(); hold.setPointerCapture?.(event.pointerId); startRecording(); });
    hold.addEventListener("pointerup", (event) => { event.preventDefault(); stopRecording(); });
    hold.addEventListener("pointercancel", () => stopRecording(true));
    hold.addEventListener("keydown", (event) => {
      if ([" ", "Enter"].includes(event.key) && !event.repeat) { event.preventDefault(); startRecording(); }
    });
    hold.addEventListener("keyup", (event) => {
      if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); stopRecording(); }
    });

    $("#messageForm").addEventListener("submit", (event) => { event.preventDefault(); sendUserText($("#messageInput").value); });
    $("#openLocalVideo").addEventListener("click", () => $("#localVideoInput").click());
    $("#localVideoInput").addEventListener("change", (event) => openLocalVideo(event.target.files?.[0]));
    $("#openVideoUrl").addEventListener("click", () => $("#videoUrlDialog").showModal());
    $("#observeFrame").addEventListener("click", () => captureAndSendFrame("manual"));
    $("#videoUrlForm").addEventListener("submit", (event) => {
      if (event.submitter?.value !== "default") return;
      event.preventDefault();
      const raw = $("#videoUrlInput").value.trim();
      try {
        const parsed = new URL(raw);
        if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
        if (state.objectVideoUrl) { URL.revokeObjectURL(state.objectVideoUrl); state.objectVideoUrl = ""; }
        if (isBilibiliPageUrl(parsed.toString())) {
          // 上一次解析仍在进行时不重复提交，避免服务端解析任务互相取消
          if (state.resolvingMedia) { showToast("上一个视频仍在解析，请稍候"); return; }
          if (!send({ type: "resolve_media", url: parsed.toString() })) return;
          state.resolvingMedia = true;
          $("#videoTitle").textContent = "正在解析视频";
          $("#watchStatus").textContent = "正在解析";
          setVideoPlaceholder("正在解析 B 站视频", true);
        } else {
          loadVideo(parsed.toString(), decodeURIComponent(parsed.pathname.split("/").pop() || parsed.hostname), true);
        }
        $("#videoUrlDialog").close();
      } catch {
        showToast("请输入有效的 HTTP 或 HTTPS 视频地址");
      }
    });

    const video = $("#watchVideo");
    $("#videoPlayPause").addEventListener("click", toggleVideoPlayback);
    $("#videoCenterToggle").addEventListener("click", toggleVideoPlayback);
    $("#videoSeekBack").addEventListener("click", () => seekVideoBy(-10));
    const seekForward = $("#videoSeekForward");
    seekForward.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      seekForward.setPointerCapture?.(event.pointerId);
      beginTemporaryVideoRate(seekForward);
    });
    seekForward.addEventListener("pointerup", (event) => {
      event.preventDefault();
      const held = endTemporaryVideoRate();
      if (!held) seekVideoBy(10);
    });
    seekForward.addEventListener("pointercancel", endTemporaryVideoRate);
    seekForward.addEventListener("contextmenu", (event) => event.preventDefault());
    seekForward.addEventListener("click", (event) => {
      if (event.detail === 0) seekVideoBy(10);
    });
    video.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || video.paused) return;
      video.setPointerCapture?.(event.pointerId);
      beginTemporaryVideoRate(video);
    });
    video.addEventListener("pointerup", () => {
      if (endTemporaryVideoRate()) state.videoSurfaceSuppressClick = true;
    });
    video.addEventListener("pointercancel", endTemporaryVideoRate);
    video.addEventListener("lostpointercapture", endTemporaryVideoRate);
    video.addEventListener("pointerleave", () => {
      if (endTemporaryVideoRate()) state.videoSurfaceSuppressClick = true;
      else window.clearTimeout(state.videoRateHoldTimer);
    });
    video.addEventListener("click", () => {
      if (state.videoSurfaceSuppressClick) {
        state.videoSurfaceSuppressClick = false;
        return;
      }
      toggleVideoPlayback();
    });
    video.addEventListener("contextmenu", (event) => event.preventDefault());
    $("#videoProgress").addEventListener("input", (event) => {
      if (!Number.isFinite(video.duration) || video.duration <= 0) return;
      video.currentTime = (Number(event.target.value) / 1000) * video.duration;
      syncDashAudio({ seek: true });
      updateVideoControls();
    });
    $("#videoMute").addEventListener("click", () => {
      video.muted = !video.muted;
      updateVideoControls();
    });
    $("#videoVolume").addEventListener("input", (event) => {
      video.volume = Math.max(0, Math.min(1, Number(event.target.value)));
      video.muted = video.volume === 0;
      updateVideoControls();
    });
    $("#videoRate").addEventListener("change", (event) => {
      video.playbackRate = Math.max(.5, Math.min(2, Number(event.target.value) || 1));
      updateVideoControls();
    });
    $("#videoStage").addEventListener("keydown", (event) => {
      if (["INPUT", "SELECT", "BUTTON"].includes(event.target.tagName)) return;
      const actions = {
        Space: toggleVideoPlayback,
        KeyK: toggleVideoPlayback,
        ArrowLeft: () => seekVideoBy(-5),
        KeyJ: () => seekVideoBy(-10),
        ArrowRight: () => seekVideoBy(5),
        KeyL: () => seekVideoBy(10),
        KeyM: () => { video.muted = !video.muted; updateVideoControls(); },
        KeyF: toggleWatchFullscreen,
      };
      const action = actions[event.code];
      if (!action) return;
      event.preventDefault();
      action();
    });
    video.addEventListener("loadeddata", () => {
      state.resolvingMedia = false;
      state.lastSceneSignature = null;
      setVideoPlaceholder("", false);
      $("#watchStatus").textContent = video.paused ? "已经就绪" : "一起看着";
      updatePlayerState("loaded");
      updateVideoControls();
    });
    video.addEventListener("error", () => {
      if (!video.currentSrc && !video.getAttribute("src")) return;
      if (video.crossOrigin === "anonymous" && !state.remoteCorsFallbackTried) {
        const source = video.currentSrc || video.src;
        state.remoteCorsFallbackTried = true;
        video.pause();
        video.removeAttribute("src");
        video.removeAttribute("crossorigin");
        video.src = source;
        video.load();
        setVideoPlaceholder("正在切换到仅播放模式", true);
        $("#watchStatus").textContent = "正在兼容视频源";
        showToast("该视频源不允许读取画面，已改为仅播放模式", 4200);
        return;
      }
      state.resolvingMedia = false;
      setVideoPlaceholder("视频加载失败", true);
      $("#watchStatus").textContent = "加载失败";
      showToast("视频没有成功加载，链接可能已失效或不是可播放的媒体", 5000);
    });
    $("#watchAudio").addEventListener("loadedmetadata", () => syncDashAudio({ seek: true }));
    $("#watchAudio").addEventListener("error", () => {
      if (state.dashAudioEnabled) showToast("高清音频轨加载失败，请重新解析视频", 5000);
    });
    ["play", "pause", "seeking", "seeked", "ratechange", "volumechange", "durationchange", "loadedmetadata", "timeupdate", "ended"].forEach((name) => {
      video.addEventListener(name, () => {
        const now = Date.now();
        if (name === "loadedmetadata" && state.pendingResumeTime > 0) {
          // 断线恢复：回到断开前的播放进度
          video.currentTime = Math.min(state.pendingResumeTime, Math.max(0, (video.duration || 0) - 1));
          state.pendingResumeTime = 0;
        }
        if (name === "play") syncDashAudio({ seek: true, play: true });
        else if (name === "seeking" || name === "seeked" || name === "loadedmetadata") syncDashAudio({ seek: true });
        else syncDashAudio();
        if (name !== "timeupdate" || now - state.lastPlayerStateSentAt >= 2000) updatePlayerState(name);
        updateVideoControls();
        if (name === "play") {
          state.lastFrameSentAt = now;
          if (!state.openingSent && video.currentTime < 12) {
            state.openingSent = true;
            window.clearTimeout(state.openingTimer);
            state.openingTimer = window.setTimeout(() => {
              if (!video.paused && !video.ended) captureAndSendFrame("opening");
            }, 2400);
          }
        }
        if (name === "seeked") state.lastSceneSignature = null;
        if (name === "pause" || name === "ended") endTemporaryVideoRate();
        if (name === "ended") {
          if ($("#autoComment").checked) captureAndSendFrame("ending");
        }
      });
    });
    window.addEventListener("blur", endTemporaryVideoRate);
    window.addEventListener("beforeunload", () => {
      stopCall(false);
      stopAudio();
      window.clearInterval(state.frameTimer);
      window.clearInterval(state.pingTimer);
      window.clearTimeout(state.openingTimer);
      window.cancelAnimationFrame(state.volumeAnimationFrame);
      if (state.objectVideoUrl) URL.revokeObjectURL(state.objectVideoUrl);
      if (state.socket?.readyState === WebSocket.OPEN) state.socket.close();
    });
  }

  bindEvents();
  icons();
  updateVideoControls();
  setMode(state.mode, false);
  connect();
})();
