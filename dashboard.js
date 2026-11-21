(() => {
  const version = 2;
  const previous = window.__vrchatGestureToggle;
  if (previous?.version === version) return "already installed";
  // frame_dock.py checks the CEF target URL. Steam sets a different document
  // location inside that target, which does not contain the overlay key.
  previous?.dispose();

  const api = "http://127.0.0.1:27656";
  const languageKey = "vrchatGestureDock.language";
  const copy = {
    en: {
      label: "Gestures", unavailable: "Not ready", switching: "Switching", title: "VRChat gestures",
      connecting: "Connecting to the gesture service on your PC.",
      check: "Check that SteamVR and VRChat Gesture Dock are running on your streaming PC.",
      retry: "Could not switch gestures. Check the connection and try again.",
      describe: (current, next) => `VRChat gestures · Using ${current}. Click to switch to ${next}.`
    },
    "zh-CN": {
      label: "手势", unavailable: "未就绪", switching: "切换中", title: "VRChat 手势",
      connecting: "正在连接电脑上的 VRChat 手势服务。",
      check: "请确认串流电脑上的 SteamVR 和 VRChat Gesture Dock 正在运行。",
      retry: "手势切换失败，请检查连接后重试。",
      describe: (current, next) => `VRChat 手势 · 当前 ${current}，点击切换到 ${next}。`
    }
  };

  function validLanguage(value) {
    return value === "en" || value === "zh-CN";
  }

  function headsetLanguage() {
    const steam = new URL(document.baseURI).searchParams.get("LANGUAGE");
    const locale = steam || document.documentElement.lang || navigator.language;
    return /^(?:schinese$|tchinese$|zh(?:[-_]|$))/i.test(locale) ? "zh-CN" : "en";
  }

  let savedLanguage;
  try { savedLanguage = localStorage.getItem(languageKey); } catch { /* Storage may be disabled in CEF. */ }
  let language = validLanguage(savedLanguage) ? savedLanguage : headsetLanguage();
  const style = document.createElement("style");
  style.textContent = `
    #vrchat-gesture-toggle {
      box-sizing: border-box; display: inline-flex; flex-direction: column;
      align-items: center; justify-content: center; align-self: center;
      flex: 0 0 auto; padding: 0 2px; margin: 0; border: 0;
      font-family: inherit; color: inherit; line-height: 1.08;
      cursor: pointer; white-space: nowrap;
    }
    #vrchat-gesture-toggle .vrchat-gesture-label { font-size: .76em; }
    #vrchat-gesture-toggle .vrchat-gesture-mode { font-size: .82em; font-weight: 700; }
    #vrchat-gesture-toggle:lang(en) { min-width: 64px; }
    #vrchat-gesture-toggle:lang(en) span { font-size: min(.8em, 13px); }
    #vrchat-gesture-toggle[data-mode="quest"] .vrchat-gesture-mode { color: #86d8ff; }
    #vrchat-gesture-toggle[data-mode="unavailable"] .vrchat-gesture-mode { opacity: .75; }
    #vrchat-gesture-toggle:hover, #vrchat-gesture-toggle:focus-visible {
      outline: 2px solid currentColor; outline-offset: -2px;
    }
  `;
  (document.head || document.documentElement).append(style);

  const button = document.createElement("button");
  button.type = "button";
  button.id = "vrchat-gesture-toggle";
  const label = document.createElement("span");
  label.className = "vrchat-gesture-label";
  const indicator = document.createElement("span");
  indicator.className = "vrchat-gesture-mode";
  indicator.setAttribute("aria-live", "polite");
  button.append(label, indicator);

  let anchor;
  let slot;
  let mode = "unavailable";
  let detail;
  let reason = "connecting";
  let checking;
  let switching;
  let disposed = false;

  function draw() {
    const text = copy[language];
    const names = { frame: "Frame", quest: "Quest", unavailable: text.unavailable };
    const next = mode === "quest" ? "Frame" : "Quest";
    const caption = switching ? text.switching : names[mode];
    button.lang = language;
    if (label.textContent !== text.label) label.textContent = text.label;
    button.dataset.mode = mode;
    button.setAttribute("aria-busy", String(Boolean(switching)));
    if (mode === "unavailable") button.removeAttribute("aria-pressed");
    else button.setAttribute("aria-pressed", String(mode === "quest"));
    if (indicator.textContent !== caption) indicator.textContent = caption;
    button.title = mode === "unavailable"
      ? text.title + " · " + (detail || text[reason])
      : switching ? text.title + " · " + text.switching : text.describe(names[mode], next);
    button.setAttribute("aria-label", button.title);
  }

  function isRoomButton(element) {
    const fiberKey = Object.keys(element).find(key => key.startsWith("__reactFiber"));
    let fiber = element[fiberKey];
    for (let depth = 0; fiber && depth < 16; depth++, fiber = fiber.return) {
      const action = fiber.memoizedProps?.action;
      if (action?.invocation === 2 && action.icon?.enum === 2 && action.icon_active?.enum === 1) {
        return true;
      }
    }
    return false;
  }

  function mount() {
    if (anchor?.isConnected && slot?.contains(anchor) && button.parentElement === slot.parentElement) return;
    anchor = document.querySelector("[data-sf-passthrough-state]") ||
      Array.from(document.querySelectorAll('[role="button"]')).find(isRoomButton);
    if (!anchor) {
      button.remove();
      return;
    }
    // Find the row of controls even when Steam wraps each one in a tooltip container.
    slot = anchor;
    for (let depth = 0, node = anchor; node.parentElement && depth < 5; depth++, node = node.parentElement) {
      const hasPeer = Array.from(node.parentElement.children).some(sibling =>
        sibling !== node && sibling !== button &&
        (sibling.matches('button,[role="button"]') || sibling.querySelector('button,[role="button"]')));
      if (hasPeer) {
        slot = node;
        break;
      }
    }
    button.className = anchor.className;
    const native = getComputedStyle(anchor);
    button.style.width = native.width;
    button.style.height = native.height;
    slot.after(button);
  }

  async function request(path, controller, options) {
    const timeout = setTimeout(() => controller.abort(), options ? 15000 : 6000);
    try {
      const response = await fetch(api + path, {
        cache: "no-store", credentials: "omit", signal: controller.signal, ...options
      });
      const data = await response.json();
      if (!response.ok || !["frame", "quest", "unavailable"].includes(data.mode)) {
        throw new Error("Invalid gesture service response");
      }
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  function update(state, fallbackReason = "check") {
    if (validLanguage(state.language)) {
      language = state.language;
      if (language !== savedLanguage) {
        savedLanguage = language;
        try { localStorage.setItem(languageKey, language); } catch { /* The in-memory choice still applies. */ }
      }
    }
    mode = state.mode;
    reason = fallbackReason;
    // Older bridges only return Chinese diagnostics and omit the language field.
    detail = (validLanguage(state.language) || language === "zh-CN") ? state.detail : undefined;
  }

  async function refresh() {
    if (checking || switching || disposed) return;
    const controller = new AbortController();
    checking = controller;
    try {
      const state = await request("/state", controller);
      if (checking === controller && !disposed) update(state);
    } catch {
      if (checking === controller && !disposed) update({ mode: "unavailable" });
    } finally {
      if (checking === controller) checking = undefined;
      if (!disposed) draw();
    }
  }

  button.addEventListener("click", async event => {
    event.preventDefault();
    event.stopPropagation();
    if (switching || disposed) return;
    if (mode === "unavailable") {
      await refresh();
      return;
    }
    checking?.abort();
    checking = undefined;
    switching = new AbortController();
    draw();
    try {
      const result = await request("/toggle", switching, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedMode: mode })
      });
      if (!disposed) update(result);
    } catch {
      if (!disposed) update({ mode: "unavailable" }, "retry");
    } finally {
      switching = undefined;
      if (!disposed) draw();
    }
  });

  const observer = new MutationObserver(mount);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  const timer = setInterval(refresh, 5000);
  window.__vrchatGestureToggle = { version, dispose() {
    disposed = true;
    observer.disconnect();
    clearInterval(timer);
    checking?.abort();
    switching?.abort();
    button.remove();
    style.remove();
    delete window.__vrchatGestureToggle;
  } };
  draw();
  mount();
  refresh();
  return "installed";
})()
