const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "dashboard.js"), "utf8");

function element() {
  return {
    children: [], dataset: {}, textContent: "",
    append(...children) { this.children.push(...children); },
    remove() {}, setAttribute() {}, removeAttribute() {},
    addEventListener(event, listener) { this[event] = listener; }
  };
}

function openDock({baseURI = "about:blank", browserLanguage = "en-US", cache = new Map(), previous} = {}) {
  let interval;
  const button = element();
  const document = {
    baseURI, head: element(), documentElement: element(),
    createElement: tag => tag === "button" ? button : element(),
    querySelector: () => null, querySelectorAll: () => []
  };
  const responses = [];
  const window = {__vrchatGestureToggle: previous};
  vm.runInNewContext(source, {
    window, document, navigator: {language: browserLanguage}, URL, AbortController,
    localStorage: {
      getItem: key => cache.get(key),
      setItem: (key, value) => cache.set(key, value)
    },
    MutationObserver: class { observe() {} disconnect() {} },
    setInterval: callback => { interval = callback; }, clearInterval() {},
    setTimeout, clearTimeout,
    fetch: async () => {
      const next = responses.shift();
      if (!next) throw new Error("disconnected");
      return {ok: true, json: async () => next};
    }
  });
  return {
    button, window, responses, refresh: () => interval(),
    click: () => button.click({preventDefault() {}, stopPropagation() {}}),
    firstRefresh: () => new Promise(resolve => setImmediate(resolve))
  };
}

test("Steam UI language controls offline text, before the first PC connection", async () => {
  const dock = openDock({baseURI: "https://steamloopback.host/?LANGUAGE=schinese", browserLanguage: "en-GB"});
  await dock.firstRefresh();
  assert.equal(dock.button.lang, "zh-CN");
  assert.deepEqual(dock.button.children.map(span => span.textContent), ["手势", "未就绪"]);
  assert.match(dock.button.title, /串流电脑/);
});

test("installer choice overrides headset, survives reconnects and CEF reloads", async () => {
  const cache = new Map();
  let disposed = false;
  const dock = openDock({browserLanguage: "zh-HK", cache, previous: {version: 1, dispose() { disposed = true; }}});
  assert.equal(disposed, true);
  await dock.firstRefresh();
  dock.responses.push({mode: "frame", language: "en"});
  await dock.refresh();
  assert.deepEqual(dock.button.children.map(span => span.textContent), ["Gestures", "Frame"]);
  assert.match(dock.button.title, /switch to Quest/);
  dock.responses.push({mode: "quest", language: "en"});
  await dock.click();
  assert.match(dock.button.title, /switch to Frame/);
  await dock.click();
  assert.match(dock.button.title, /try again/);
  const reopened = openDock({browserLanguage: "zh-HK", cache});
  await reopened.firstRefresh();
  assert.deepEqual(reopened.button.children.map(span => span.textContent), ["Gestures", "Not ready"]);
  reopened.responses.push({mode: "unavailable", language: "zh-CN", detail: "请连接头显。"});
  await reopened.refresh();
  assert.match(reopened.button.title, /请连接头显/);
  assert.equal(reopened.button.children[0].textContent, "手势");
});

test("English headset handles a legacy bridge without displaying Chinese diagnostics", async () => {
  const dock = openDock();
  await dock.firstRefresh();
  dock.responses.push({mode: "unavailable", detail: "请先启动 SteamVR。"});
  await dock.refresh();
  assert.match(dock.button.title, /streaming PC/);
  dock.responses.push({mode: "frame"});
  await dock.refresh();
  assert.equal(dock.button.children[1].textContent, "Frame");
});
