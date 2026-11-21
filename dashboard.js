(() => {
  const version = 1;
  const previous = window.__vrchatGestureToggle;
  if (previous?.version === version) return "already installed";
  // frame_dock.py checks the CEF target URL. Steam sets a different document
  // location inside that target, which does not contain the overlay key.
  previous?.dispose();

  const api = "http://127.0.0.1:27656";
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
  label.textContent = "手势";
  const indicator = document.createElement("span");
  indicator.className = "vrchat-gesture-mode";
  indicator.setAttribute("aria-live", "polite");
  button.append(label, indicator);

  let anchor;
  let slot;
  let mode = "unavailable";
  let detail = "正在连接电脑上的 VRChat 手势服务";
  let checking;
  let switching;
  let disposed = false;

  function draw() {
    const names = { frame: "Frame", quest: "Quest", unavailable: "未就绪" };
    const next = mode === "quest" ? "Frame" : "Quest";
    const caption = switching ? "切换中" : names[mode];
    button.dataset.mode = mode;
    button.setAttribute("aria-busy", String(Boolean(switching)));
    if (mode === "unavailable") button.removeAttribute("aria-pressed");
    else button.setAttribute("aria-pressed", String(mode === "quest"));
    if (indicator.textContent !== caption) indicator.textContent = caption;
    button.title = mode === "unavailable"
      ? "VRChat 手势 · " + detail
      : "VRChat 手势 · 当前 " + names[mode] + "，点击切换到 " + next;
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
      if (!response.ok) throw new Error(data.detail || data.error || "手势服务响应异常");
      if (!["frame", "quest", "unavailable"].includes(data.mode)) throw new Error("无法读取绑定状态");
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }

  function update(state) {
    mode = state.mode;
    detail = state.detail || "请确认串流电脑上的 SteamVR 已启动";
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
      update(await request("/toggle", switching, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expectedMode: mode })
      }));
    } catch (error) {
      update({ mode: "unavailable", detail: error.message || "切换失败，请重试" });
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
