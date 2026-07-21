(() => {
  "use strict";

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const statusNode = document.getElementById("serviceStatus");
  const badgeNode = document.getElementById("presenceBadge");
  const linkResult = document.getElementById("linkResult");
  const roomUrl = document.getElementById("roomUrl");
  const launchMessage = document.getElementById("launchMessage");
  const toast = document.getElementById("toast");
  const configForm = document.getElementById("configForm");
  const configState = document.getElementById("configState");
  const saveConfigButton = document.getElementById("saveConfig");
  const tunnelToggle = document.getElementById("tunnelToggle");
  const tunnelStatus = document.getElementById("tunnelStatus");
  const tunnelUrl = document.getElementById("tunnelUrl");
  let configBaseline = "";
  let toastTimer = 0;
  let tunnelPollTimer = 0;

  function icons() {
    if (window.lucide?.createIcons) window.lucide.createIcons();
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 2600);
  }

  async function bridge() {
    // 宿主注入可能较慢，最长等待约 6 秒
    for (let index = 0; index < 60; index += 1) {
      const candidate = window.AstrBotPluginPage;
      if (candidate?.apiGet && candidate?.apiPost) {
        if (candidate.ready) await candidate.ready();
        return candidate;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    throw new Error("请从 AstrBot 插件拓展页打开此页面");
  }

  async function requestEndpoint(method, path, body = {}) {
    const api = await bridge();
    const endpoint = `page/${String(path || "").replace(/^\/+/, "")}`;
    const result = method === "GET"
      ? await api.apiGet(endpoint)
      : await api.apiPost(endpoint, body);
    if (result?.status === "error") throw new Error(result.message || "请求失败");
    return result?.data ?? result;
  }

  function setProvider(id, capability, fallback) {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = capability?.available ? (capability.label || "可用") : fallback;
  }

  function setTunnelButton(icon, label, { running = false, disabled = false } = {}) {
    tunnelToggle.dataset.running = String(running);
    tunnelToggle.disabled = disabled;
    tunnelToggle.innerHTML = `<i data-lucide="${icon}"></i><span>${label}</span>`;
    icons();
  }

  function applyTunnelStatus(tunnel = {}) {
    window.clearTimeout(tunnelPollTimer);
    const fixedUrl = String(tunnel.fixed_public_url || "").trim();
    const quickUrl = String(tunnel.url || "").trim();
    if (fixedUrl) {
      tunnelStatus.textContent = "固定公网地址已生效";
      tunnelUrl.textContent = fixedUrl;
      tunnelUrl.hidden = false;
      setTunnelButton("badge-check", "无需临时穿透", { disabled: true });
      return;
    }
    if (tunnel.running && quickUrl) {
      tunnelStatus.textContent = tunnel.ready
        ? "临时公网访问已生效，新房间链接将使用此地址"
        : (tunnel.error || "临时地址已分配，正在等待公网生效…");
      tunnelUrl.textContent = quickUrl;
      tunnelUrl.hidden = false;
      setTunnelButton("unplug", "停止公网访问", { running: true });
      if (!tunnel.ready) {
        tunnelPollTimer = window.setTimeout(loadStatus, 1800);
      }
      return;
    }
    tunnelUrl.textContent = "";
    tunnelUrl.hidden = true;
    if (tunnel.installed) {
      tunnelStatus.textContent = tunnel.error || "按需生成临时 HTTPS 地址，适合手机访问";
      setTunnelButton("radio", "启动临时穿透");
    } else {
      tunnelStatus.textContent = "未检测到 cloudflared，请先安装客户端";
      setTunnelButton("circle-alert", "缺少 cloudflared", { disabled: true });
    }
  }

  async function loadStatus() {
    try {
      const data = await requestEndpoint("GET", "status");
      const running = Boolean(data?.running);
      const chatReady = Boolean(data?.capabilities?.chat?.available);
      const ready = running && chatReady;
      badgeNode.textContent = ready ? "可进入" : (running ? "待配置" : "未启动");
      badgeNode.className = `presence ${ready ? "online" : "offline"}`;
      statusNode.textContent = !running
        ? "房间服务当前不可用"
        : (chatReady ? `房间服务运行于 ${data.base_url}` : "请先选择通话与观影对话模型");
      setProvider("chatProvider", data?.capabilities?.chat, "未配置（必选）");
      setProvider("visionProvider", data?.capabilities?.vision, "未配置");
      setProvider("sttProvider", data?.capabilities?.stt, "浏览器回退");
      setProvider("ttsProvider", data?.capabilities?.tts, "浏览器回退");
      applyTunnelStatus(data?.tunnel || {});
      document.getElementById("watchCapability").textContent = data?.capabilities?.vision?.available
        ? `画面理解：${data.capabilities.vision.label || "可用"}`
        : "需配置支持图片的视觉模型";
      document.getElementById("callCapability").textContent = data?.capabilities?.stt?.available
        ? "浏览器识别 / AstrBot STT"
        : (SpeechRecognition ? "Edge / Chrome 免配置识别" : "可用文字通话，语音需配置 STT");
      if (!data?.capabilities?.stt?.available) {
        document.getElementById("sttProvider").textContent = SpeechRecognition ? "浏览器免配置" : "未配置";
      }
      document.querySelectorAll("[data-mode]").forEach((button) => { button.disabled = !ready; });
    } catch (error) {
      badgeNode.textContent = "连接失败";
      badgeNode.className = "presence offline";
      statusNode.textContent = error?.message || "无法读取房间状态";
      document.querySelectorAll("[data-mode]").forEach((button) => { button.disabled = true; });
    }
  }

  function selectConfigTab(name) {
    document.querySelectorAll("[data-config-tab]").forEach((button) => {
      const active = button.dataset.configTab === name;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    document.querySelectorAll("[data-config-view]").forEach((view) => {
      const active = view.dataset.configView === name;
      view.hidden = !active;
      view.classList.toggle("is-active", active);
    });
  }

  function setSttChoice(value) {
    const normalized = ["auto", "browser", "astrbot"].includes(value) ? value : "auto";
    configForm.elements.namedItem("speech.stt_mode").value = normalized;
    document.querySelectorAll("[data-stt-choice]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.sttChoice === normalized);
      button.setAttribute("aria-pressed", String(button.dataset.sttChoice === normalized));
    });
  }

  function configValues() {
    const values = {};
    configForm.querySelectorAll("[name]").forEach((control) => {
      if (control.type === "checkbox") values[control.name] = control.checked;
      else if (control.type === "range") values[control.name] = Number(control.value);
      else values[control.name] = control.value;
    });
    return values;
  }

  function updateRangeOutputs() {
    const units = {
      "watch.comment_interval_seconds": "秒",
      "watch.scene_min_interval_seconds": "秒",
      "watch.memory_refresh_seconds": "秒",
      "watch.duck_volume_percent": "%",
      "conversation.history_turns": "轮",
    };
    configForm.querySelectorAll('input[type="range"][name]').forEach((input) => {
      const output = configForm.querySelector(`[data-output-for="${input.name}"]`);
      if (output) output.textContent = `${input.value} ${units[input.name] || ""}`.trim();
    });
  }

  function updateConfigDirtyState() {
    updateRangeOutputs();
    if (!configBaseline) return;
    const dirty = JSON.stringify(configValues()) !== configBaseline;
    saveConfigButton.disabled = !dirty;
    configState.textContent = dirty ? "有未保存更改" : "已保存";
  }

  function fillProviderSelect(select, items, selectedValue) {
    const labels = {
      chat: "请选择对话模型（必选）",
      vision: "可选：复用多模态对话模型或自动视觉模型",
      stt: "跟随 AstrBot 默认 STT",
      tts: "跟随 AstrBot 默认 TTS",
    };
    select.replaceChildren();
    const fallback = document.createElement("option");
    fallback.value = "";
    fallback.textContent = labels[select.dataset.providerKind] || "跟随 AstrBot 默认";
    fallback.disabled = select.dataset.providerKind === "chat";
    select.appendChild(fallback);
    (Array.isArray(items) ? items : []).forEach((item) => {
      const option = document.createElement("option");
      option.value = String(item.id || "");
      option.textContent = item.label && item.label !== item.id ? `${item.label} · ${item.id}` : (item.label || item.id || "未命名 Provider");
      select.appendChild(option);
    });
    if (selectedValue && !Array.from(select.options).some((option) => option.value === selectedValue)) {
      const current = document.createElement("option");
      current.value = selectedValue;
      current.textContent = `${selectedValue} · 当前配置`;
      select.appendChild(current);
    }
    select.value = selectedValue || "";
  }

  function applyConfig(data) {
    const values = data?.values || {};
    const providers = data?.providers || {};
    configForm.querySelectorAll("select[data-provider-kind]").forEach((select) => {
      fillProviderSelect(select, providers[select.dataset.providerKind], String(values[select.name] || ""));
    });
    configForm.querySelectorAll("[name]").forEach((control) => {
      if (!(control.name in values) || control.dataset.providerKind) return;
      if (control.type === "checkbox") control.checked = Boolean(values[control.name]);
      else control.value = values[control.name];
    });
    setSttChoice(String(values["speech.stt_mode"] || "auto"));
    updateRangeOutputs();
    configBaseline = JSON.stringify(configValues());
    saveConfigButton.disabled = true;
    configState.textContent = "已保存";
  }

  async function loadConfig() {
    configState.textContent = "正在读取";
    saveConfigButton.disabled = true;
    try {
      applyConfig(await requestEndpoint("GET", "config"));
    } catch (error) {
      configState.textContent = "读取失败";
      showToast(error?.message || "无法读取房间设置");
    }
  }

  async function saveConfig() {
    saveConfigButton.disabled = true;
    configState.textContent = "正在保存";
    try {
      const data = await requestEndpoint("POST", "config/save", { values: configValues() });
      applyConfig({ values: data?.values || configValues(), providers: null });
      await Promise.all([loadConfig(), loadStatus()]);
      showToast(data?.message || "配置已保存");
    } catch (error) {
      configState.textContent = "保存失败";
      saveConfigButton.disabled = false;
      showToast(error?.message || "房间设置保存失败");
    }
  }

  async function toggleTunnel() {
    const stopping = tunnelToggle.dataset.running === "true";
    tunnelToggle.disabled = true;
    tunnelStatus.textContent = stopping ? "正在停止临时公网访问" : "正在建立临时 HTTPS 通道";
    setTunnelButton(stopping ? "loader-circle" : "loader-circle", stopping ? "正在停止" : "正在启动", {
      running: stopping,
      disabled: true,
    });
    try {
      const data = await requestEndpoint("POST", stopping ? "tunnel/stop" : "tunnel/start");
      await loadStatus();
      showToast(stopping
        ? "临时公网访问已停止"
        : (data?.tunnel?.ready ? "临时公网访问已生效" : "临时地址已创建，正在等待公网生效"));
    } catch (error) {
      showToast(error?.message || "临时公网访问操作失败");
      await loadStatus();
    }
  }

  async function launch(mode, button) {
    button.disabled = true;
    try {
      const data = await requestEndpoint("POST", "room/create", {
        mode,
        open_browser: true,
      });
      if (!data?.url) throw new Error("服务端没有返回房间链接");
      roomUrl.value = data.url;
      linkResult.hidden = false;
      if (data.browser_opened) {
        launchMessage.textContent = "已在系统默认浏览器打开房间。";
      } else if (data.browser_launch_available === false) {
        launchMessage.textContent = "当前不是本机访问，无法代为打开浏览器；可复制房间链接后打开。";
      } else {
        launchMessage.textContent = "未能唤起系统默认浏览器，可复制房间链接后打开。";
      }
    } catch (error) {
      showToast(error?.message || "创建房间失败");
    } finally {
      button.disabled = false;
    }
  }

  async function copyUrl(value = roomUrl.value) {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      roomUrl.focus();
      roomUrl.select();
      if (!document.execCommand("copy")) {
        showToast("复制失败，请手动选择链接复制");
        return;
      }
    }
    showToast("房间链接已复制");
  }

  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.addEventListener("click", () => launch(button.dataset.mode, button));
  });
  document.getElementById("copyRoomUrl").addEventListener("click", () => copyUrl());
  tunnelToggle.addEventListener("click", toggleTunnel);
  document.querySelectorAll("[data-config-tab]").forEach((button) => {
    button.addEventListener("click", () => selectConfigTab(button.dataset.configTab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const tabs = Array.from(document.querySelectorAll("[data-config-tab]"));
      const offset = event.key === "ArrowRight" ? 1 : -1;
      const next = tabs[(tabs.indexOf(button) + offset + tabs.length) % tabs.length];
      selectConfigTab(next.dataset.configTab);
      next.focus();
    });
  });
  document.querySelectorAll("[data-stt-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      setSttChoice(button.dataset.sttChoice);
      updateConfigDirtyState();
    });
  });
  configForm.addEventListener("input", updateConfigDirtyState);
  configForm.addEventListener("change", updateConfigDirtyState);
  configForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveConfig();
  });

  icons();
  selectConfigTab("voice");
  Promise.all([loadStatus(), loadConfig()]);
})();
