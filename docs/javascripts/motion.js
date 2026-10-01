/*
 * motion.js (from strands-labs/robots docs/assets/motion.js): the docs site's small motions. Each one carries a state or an arrival:
 *   - the content column fades in after an instant-navigation swap (never on the first paint),
 *   - the landing's stat pills count up once when they scroll into view,
 *   - the "On this page" bar travels to the active entry,
 *   - the theme toggle cross-fades through the View Transitions API where the browser has it.
 * Everything here is skipped under prefers-reduced-motion, and every hook re-runs on Material's
 * document$ so it survives instant navigation. No fetches, no layout reads in a loop, no timers
 * that outlive their element.
 */
(() => {
  const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  let paints = 0;

  function enter() {
    paints += 1;
    const inner = document.querySelector(".md-content__inner");
    if (!inner || paints === 1 || reduced()) return;
    inner.classList.remove("sr-enter");
    void inner.offsetWidth;
    inner.classList.add("sr-enter");
  }

  function countUp() {
    const cells = document.querySelectorAll(".stat-pill strong:not([data-counted])");
    if (!cells.length) return;
    if (reduced() || !("IntersectionObserver" in window)) { cells.forEach((c) => (c.dataset.counted = "1")); return; }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const el = e.target;
        io.unobserve(el);
        el.dataset.counted = "1";
        const text = el.textContent.trim();
        const m = text.match(/^(\d+)(.*)$/);
        if (!m) continue;
        const target = Number(m[1]), tail = m[2], t0 = performance.now(), dur = 600;
        const tick = (now) => {
          const k = Math.min(1, (now - t0) / dur);
          el.textContent = Math.round(target * (1 - Math.pow(1 - k, 3))) + tail;
          if (k < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }
    }, { threshold: 0.6 });
    cells.forEach((c) => io.observe(c));
  }

  function tocBar() {
    const nav = document.querySelector(".md-sidebar--secondary .md-nav--secondary");
    if (!nav || nav.dataset.srBar) return;
    nav.dataset.srBar = "1";
    const place = () => {
      const active = nav.querySelector(".md-nav__link--active");
      if (!active) { nav.style.setProperty("--sr-toc-h", "0px"); return; }
      const navBox = nav.getBoundingClientRect(), box = active.getBoundingClientRect();
      nav.style.setProperty("--sr-toc-y", `${box.top - navBox.top}px`);
      nav.style.setProperty("--sr-toc-h", `${box.height}px`);
    };
    new MutationObserver(place).observe(nav, { attributes: true, subtree: true, attributeFilter: ["class"] });
    place();
  }

  function themeFade() {
    if (document.documentElement.dataset.srTheme) return;
    document.documentElement.dataset.srTheme = "1";
    document.addEventListener("click", (e) => {
      const label = e.target.closest?.(".md-header__button[for^='__palette']");
      if (!label || reduced() || typeof document.startViewTransition !== "function") return;
      const input = document.getElementById(label.htmlFor);
      if (!input) return;
      e.preventDefault();
      document.startViewTransition(() => input.click());
    }, true);
  }

  function run() { enter(); countUp(); tocBar(); themeFade(); }
  if (window.document$?.subscribe) window.document$.subscribe(run);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
