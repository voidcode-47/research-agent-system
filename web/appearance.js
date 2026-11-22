/* Apply the saved palette in <head>, before the first paint. */
"use strict";

(() => {
  const key = "paper-studio-appearance";
  const themes = new Set(["paper", "ink", "mist", "iris"]);
  const root = document.documentElement;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const environments = {
    paper: { effect: "leaves", particle: "leaf", count: 44, label: "飘叶" },
    ink: { effect: "stars", particle: "star", count: 68, label: "流星" },
    mist: { effect: "rain", particle: "drop", count: 96, label: "细雨" },
    iris: { effect: "petals", particle: "petal", count: 56, label: "落花" },
  };

  function parse(value) {
    let saved;
    try { saved = JSON.parse(value); } catch (_) { /* Use defaults for corrupt preferences. */ }
    return {
      theme: themes.has(saved?.theme) ? saved.theme : "paper",
      motion: typeof saved?.motion === "boolean" ? saved.motion : !reducedMotion?.matches,
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
      label.textContent = preference.motion ? `${environments[preference.theme].label} · 开启` : "保持静止";
    });
    renderAtmosphere();
  }

  function renderAtmosphere() {
    const layer = document.getElementById("theme-atmosphere");
    if (!layer || layer.getAttribute("data-theme") === preference.theme) return;
    const environment = environments[preference.theme];
    const particles = [];
    for (let i = 0; i < environment.count; i++) {
      const rain = environment.particle === "drop";
      const star = environment.particle === "star";
      const depth = i % 3;
      const duration = rain ? .95 + (i % 9) / 10 : star ? 3 + i % 5 : 9 + i * 7 % 10;
      // Negative delays distribute particles through the viewport on the first frame.
      const phase = -(i + .5) * duration / environment.count;
      const x = (7.21 + i * 37.73) % 100;
      const y = (3.67 + i * 23.47) % 97;
      const size = star ? 3 + i % 5 : rain ? 36 + i % 8 * 6 : 20 + depth * 8 + i % 4 * 2;
      const alpha = rain ? .5 + depth * .16 : .6 + depth * .16;
      const drift = rain ? -170 : (i % 2 ? 1 : -1) * (70 + i * 13 % 160);
      particles.push(`<i class="weather-particle weather-${environment.particle}" style="--x:${x.toFixed(2)}%;--y:${y.toFixed(2)}%;--size:${size}px;--alpha:${alpha.toFixed(2)};--fall:${duration}s;--phase:${phase.toFixed(2)}s;--drift:${drift}px;--turn:${i * 47 % 360}deg;--sway-cycle:${4 + i % 4}s"><span class="weather-shape"></span></i>`);
    }
    if (environment.effect === "stars") {
      for (let i = 0; i < 8; i++) {
        const duration = 5.5 + i * .5;
        const phase = -(i + .5) * duration / 8;
        particles.push(`<i class="weather-particle weather-meteor" style="--x:${52 + i * 6}%;--y:${5 + i * 10}%;--fall:${duration}s;--phase:${phase.toFixed(2)}s"><span class="weather-shape"></span></i>`);
      }
    }
    if (environment.effect === "rain") {
      for (let i = 0; i < 10; i++) {
        particles.push(`<i class="weather-particle weather-ripple" style="--x:${6 + i * 10}%;--y:${70 + i % 5 * 6}%;--fall:${3 + i % 3}s;--phase:-${i * .6}s"><span class="weather-shape"></span></i>`);
      }
    }
    layer.setAttribute("data-theme", preference.theme);
    layer.setAttribute("data-effect", environment.effect);
    // Only fixed markup and generated numbers are inserted; no user text enters this layer.
    layer.innerHTML = particles.join("");
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
