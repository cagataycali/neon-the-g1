/* ─────────────────────────────────────────────────────────────────────
   Mermaid init for MkDocs Material + neon theme.
   Handles:
   - dark/light theme switching via data-md-color-scheme
   - navigation.instant (SPA-style page swaps via document$)
   - re-running mermaid after AJAX page loads
   ───────────────────────────────────────────────────────────────────── */

(function () {
  if (typeof mermaid === "undefined") {
    console.warn("[neon] mermaid.js not loaded yet");
    return;
  }

  // ── Theme tokens pulled from neon.css ────────────────────────────────
  const neonDark = {
    background:       "transparent",
    primaryColor:     "#12121a",
    primaryTextColor: "#e6e8ee",
    primaryBorderColor: "#00f0ff",
    lineColor:        "#9aa0b5",
    secondaryColor:   "#1a1a26",
    tertiaryColor:    "#0d0d16",
    nodeBkg:          "#12121a",
    nodeTextColor:    "#e6e8ee",
    clusterBkg:       "#0d0d16",
    clusterBorder:    "rgba(154,160,181,0.2)",
    edgeLabelBackground: "#0a0a12",
    labelBoxBkgColor: "#12121a",
    labelTextColor:   "#e6e8ee",
    actorBkg:         "#12121a",
    actorBorder:      "#00f0ff",
    actorTextColor:   "#e6e8ee",
    signalColor:      "#9aa0b5",
    signalTextColor:  "#e6e8ee",
    noteBkgColor:     "#141420",
    noteTextColor:    "#e6e8ee",
    noteBorderColor:  "rgba(0,240,255,0.3)",
  };

  const neonLight = {
    background:       "transparent",
    primaryColor:     "#ffffff",
    primaryTextColor: "#0a0a12",
    primaryBorderColor: "#b967ff",
    lineColor:        "#6b7280",
  };

  // ── Detect current scheme ────────────────────────────────────────────
  function currentScheme() {
    const el = document.querySelector("body, [data-md-color-scheme]");
    return (
      document.body.getAttribute("data-md-color-scheme") ||
      (el && el.getAttribute("data-md-color-scheme")) ||
      "slate"
    );
  }

  function buildConfig() {
    const dark = currentScheme() === "slate";
    return {
      startOnLoad: false,
      theme: "base",
      themeVariables: dark ? neonDark : neonLight,
      fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif",
      flowchart: {
        curve: "basis",
        htmlLabels: true,
        padding: 14,
        nodeSpacing: 40,
        rankSpacing: 50,
      },
      sequence: {
        useMaxWidth: true,
        boxMargin: 8,
        messageMargin: 28,
      },
      gantt:   { useMaxWidth: true },
      journey: { useMaxWidth: true },
      er:      { useMaxWidth: true },
      securityLevel: "loose",
    };
  }

  // ── Re-render every .mermaid on the current page ─────────────────────
  function render() {
    const nodes = document.querySelectorAll(".mermaid");
    if (!nodes.length) return;

    // Reset previously-rendered nodes so mermaid will re-process them
    nodes.forEach((el) => {
      if (el.dataset.source) {
        el.innerHTML = el.dataset.source;
      } else {
        el.dataset.source = el.innerHTML;
      }
      el.removeAttribute("data-processed");
    });

    try {
      mermaid.initialize(buildConfig());
      mermaid.run({ nodes });
    } catch (err) {
      console.error("[neon] mermaid render error:", err);
    }
  }

  // ── Hook into Material's SPA router if present, else DOMContentLoaded ─
  function setup() {
    if (typeof document$ !== "undefined" && document$.subscribe) {
      // MkDocs Material's RxJS observable — fires on every instant nav
      document$.subscribe(() => render());
    } else {
      render();
    }

    // Also re-render on palette toggle (dark/light switch)
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === "data-md-color-scheme") {
          render();
          return;
        }
      }
    });
    observer.observe(document.body, { attributes: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
