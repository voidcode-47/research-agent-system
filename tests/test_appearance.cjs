"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadAppearance(saved = null, blocked = false, reducedMotion = false) {
  const attrs = new Map();
  const storage = new Map(saved ? [["paper-studio-appearance", JSON.stringify(saved)]] : []);
  const listeners = new Map();
  const windowListeners = new Map();
  function button(dataset) {
    return {
      dataset, attrs: new Map(), listeners: new Map(),
      setAttribute(key, value) { this.attrs.set(key, value); },
      getAttribute(key) { return this.attrs.get(key); },
      addEventListener(key, handler) { this.listeners.set(key, handler); },
      click() { this.listeners.get("click")?.({ currentTarget: this }); },
    };
  }
  const choices = ["paper", "ink", "mist", "iris", "paper", "ink", "mist", "iris"]
    .map(theme => button({ themeChoice: theme }));
  const switches = [button({ motionToggle: "" }), button({ motionToggle: "" })];
  const labels = [{ textContent: "" }, { textContent: "" }];
  const atmosphere = Object.assign(button({}), { innerHTML: "" });
  const document = {
    readyState: "loading", hidden: false,
    documentElement: { setAttribute(key, value) { attrs.set(key, value); } },
    getElementById(id) { return id === "theme-atmosphere" ? atmosphere : null; },
    querySelectorAll(selector) {
      return selector === "[data-theme-choice]" ? choices
        : selector === "[data-motion-toggle]" ? switches
          : selector === "[data-motion-label]" ? labels : [];
    },
    addEventListener(key, handler) { listeners.set(key, handler); },
  };
  const localStorage = {
    getItem(key) { if (blocked) throw new Error("storage denied"); return storage.get(key) || null; },
    setItem(key, value) { if (blocked) throw new Error("storage denied"); storage.set(key, value); },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "web", "appearance.js"), "utf8"), {
    document, localStorage,
    window: {
      addEventListener(key, handler) { windowListeners.set(key, handler); },
      matchMedia() { return { matches: reducedMotion }; },
    },
  });
  const beforeReady = new Map(attrs);
  listeners.get("DOMContentLoaded")();
  return { attrs, beforeReady, storage, choices, switches, labels, atmosphere, document, listeners, windowListeners };
}

test("saved appearance is applied before the page becomes visible", () => {
  const page = loadAppearance({ theme: "ink", motion: false });
  assert.equal(page.beforeReady.get("data-theme"), "ink");
  assert.equal(page.beforeReady.get("data-motion"), "off");
  assert.equal(page.choices[1].attrs.get("aria-pressed"), "true");
  assert.equal(page.choices[5].attrs.get("aria-pressed"), "true");
  assert.equal(page.switches[0].attrs.get("aria-checked"), "false");
});

test("theme selection stays in sync and survives a reload", () => {
  const page = loadAppearance();
  page.choices[3].click();
  assert.equal(page.attrs.get("data-theme"), "iris");
  assert.equal(page.choices[3].attrs.get("aria-pressed"), "true");
  assert.equal(page.choices[7].attrs.get("aria-pressed"), "true");
  assert.equal(page.choices[0].attrs.get("aria-pressed"), "false");
  const saved = JSON.parse(page.storage.get("paper-studio-appearance"));
  assert.equal(loadAppearance(saved).attrs.get("data-theme"), "iris");
});

test("animation switches share a persisted preference without changing the theme", () => {
  const page = loadAppearance({ theme: "mist", motion: true });
  page.switches[1].click();
  assert.equal(page.attrs.get("data-motion"), "off");
  assert.equal(page.switches[0].attrs.get("aria-checked"), "false");
  assert.equal(page.switches[1].attrs.get("aria-checked"), "false");
  assert.match(page.labels[0].textContent, /静止/);
  const saved = JSON.parse(page.storage.get("paper-studio-appearance"));
  assert.deepEqual(saved, { theme: "mist", motion: false });
  page.switches[0].click();
  assert.equal(page.attrs.get("data-motion"), "on");
});

test("unavailable storage still permits choosing a theme and pausing motion", () => {
  const page = loadAppearance(null, true);
  page.choices[1].click();
  page.switches[0].click();
  assert.equal(page.attrs.get("data-theme"), "ink");
  assert.equal(page.attrs.get("data-motion"), "off");
});

test("invalid stored settings recover to a readable default", () => {
  for (const saved of [{ theme: "unknown", motion: "false" }, [], "broken", 42]) {
    const page = loadAppearance(saved);
    assert.equal(page.attrs.get("data-theme"), "paper");
    assert.equal(page.attrs.get("data-motion"), "on");
  }
});

test("hidden tabs pause animations without changing the saved preference", () => {
  const page = loadAppearance({ theme: "paper", motion: true });
  page.document.hidden = true;
  page.listeners.get("visibilitychange")();
  assert.equal(page.attrs.get("data-motion-paused"), "true");
  assert.equal(page.attrs.get("data-motion"), "on");
  page.document.hidden = false;
  page.listeners.get("visibilitychange")();
  assert.equal(page.attrs.get("data-motion-paused"), "false");
});

test("appearance changes in another tab synchronize the visible controls", () => {
  const page = loadAppearance();
  page.windowListeners.get("storage")({
    key: "paper-studio-appearance", newValue: JSON.stringify({ theme: "ink", motion: false }),
  });
  assert.equal(page.attrs.get("data-theme"), "ink");
  assert.equal(page.switches[0].attrs.get("aria-checked"), "false");
  assert.equal(page.choices[1].attrs.get("aria-pressed"), "true");
});

test("each theme selects its own full-page environment", () => {
  const page = loadAppearance();
  for (const [index, effect] of ["leaves", "stars", "rain", "petals"].entries()) {
    page.choices[index].click();
    assert.equal(page.atmosphere.getAttribute("data-effect"), effect);
    assert.match(page.labels[0].textContent, /开启/);
  }
});

test("pausing and resuming an environment preserves the selected theme", () => {
  const page = loadAppearance({ theme: "iris", motion: true });
  const particles = page.atmosphere.innerHTML;
  page.switches[0].click();
  assert.equal(page.attrs.get("data-motion"), "off");
  assert.equal(page.atmosphere.getAttribute("data-effect"), "petals");
  page.switches[0].click();
  assert.equal(page.attrs.get("data-motion"), "on");
  assert.equal(page.atmosphere.innerHTML, particles);
});

test("system motion reduction is the default and a user can explicitly enable effects", () => {
  const page = loadAppearance(null, false, true);
  assert.equal(page.attrs.get("data-motion"), "off");
  assert.equal(page.switches[0].attrs.get("aria-checked"), "false");
  page.switches[0].click();
  assert.equal(page.attrs.get("data-motion"), "on");
  const saved = JSON.parse(page.storage.get("paper-studio-appearance"));
  assert.equal(loadAppearance(saved, false, true).attrs.get("data-motion"), "on");
});
