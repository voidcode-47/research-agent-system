/* Apply the saved palette in <head>, before the first paint. */
"use strict";

(() => {
  const key = "paper-studio-appearance";
  const themes = new Set(["paper", "ink", "mist", "iris"]);
  const root = document.documentElement;

  function parse(value) {
    let saved;
    try { saved = JSON.parse(value); } catch (_) { /* Use defaults for corrupt preferences. */ }
    return {
      theme: themes.has(saved?.theme) ? saved.theme : "paper",
      motion: typeof saved?.motion === "boolean" ? saved.motion : true,
    };
  }

  let raw = null;
  try { raw = localStorage.getItem(key); } catch (_) { /* Storage can be disabled. */ }
  let preference = parse(raw);

  function apply() {
    root.setAttribute("data-theme", preference.theme);
    root.setAttribute("data-motion", preference.motion ? "on" : "off");
  }

  function render() {
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === preference.theme));
    });
    document.querySelectorAll("[data-motion-toggle]").forEach(button => {
      button.setAttribute("aria-checked", String(preference.motion));
    });
    document.querySelectorAll("[data-motion-label]").forEach(label => {
      label.textContent = preference.motion ? "动态开启" : "保持静止";
    });
  }

  function save() {
    apply();
    render();
    try { localStorage.setItem(key, JSON.stringify(preference)); } catch (_) { /* Keep the current session usable. */ }
  }

  apply();

  function init() {
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      button.addEventListener("click", () => {
        if (!themes.has(button.dataset.themeChoice)) return;
        preference.theme = button.dataset.themeChoice;
        save();
      });
    });
    document.querySelectorAll("[data-motion-toggle]").forEach(button => {
      button.addEventListener("click", () => {
        preference.motion = !preference.motion;
        save();
      });
    });
    const pause = () => root.setAttribute("data-motion-paused", String(document.hidden));
    document.addEventListener("visibilitychange", pause);
    window.addEventListener("storage", event => {
      if (event.key !== key && event.key !== null) return;
      preference = parse(event.newValue);
      apply();
      render();
    });
    pause();
    render();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
