/* Inicialização síncrona de tema e design antes da primeira pintura (evita FOUC e permite CSP script-src 'self'). */
(function () {
  try {
    var d = localStorage.getItem("sentencia_design") || "original";
    if (d !== "original" && d !== "classico" && d !== "moderno" && d !== "cockpit" && d !== "matilha") d = "original";
    document.documentElement.setAttribute("data-design", d);
    var legadoTema = localStorage.getItem("jvdfm_theme");
    if (legadoTema && !localStorage.getItem("sentencia_theme")) {
      localStorage.setItem("sentencia_theme", legadoTema);
      localStorage.removeItem("jvdfm_theme");
    }
    var mode = localStorage.getItem("sentencia_theme_mode") || localStorage.getItem("sentencia_theme") || "light";
    var t = mode === "system"
      ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : (mode === "dark" ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    document.documentElement.setAttribute("data-design", "original");
  }
})();
