/* Overseer docs, progressive enhancement only. No framework, no dependencies.
   Theme toggle, mobile nav, TOC scroll-spy, and copy-to-clipboard for code blocks. */
(function () {
  "use strict";
  var root = document.documentElement;

  /* ---- theme toggle ------------------------------------------------------ */
  function setTheme(t) {
    root.setAttribute("data-theme", t);
    try { localStorage.setItem("ovs-theme", t); } catch (e) {}
  }
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light");
    });
  });

  /* ---- mobile navigation ------------------------------------------------- */
  var body = document.body;
  function closeNav() { body.classList.remove("nav-open"); var t = document.querySelector("[data-toggle-nav]"); if (t) t.setAttribute("aria-expanded", "false"); }
  document.querySelectorAll("[data-toggle-nav]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var open = body.classList.toggle("nav-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
  document.querySelectorAll("[data-close-nav]").forEach(function (el) { el.addEventListener("click", closeNav); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeNav(); });

  /* ---- copy code --------------------------------------------------------- */
  document.querySelectorAll(".codeblock").forEach(function (block) {
    var pre = block.querySelector("pre");
    if (!pre) return;
    var btn = document.createElement("button");
    btn.className = "copy"; btn.type = "button"; btn.textContent = "Copy";
    btn.setAttribute("aria-label", "Copy code to clipboard");
    btn.addEventListener("click", function () {
      var text = pre.innerText;
      var done = function () { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1400); };
      if (navigator.clipboard) { navigator.clipboard.writeText(text).then(done, done); }
      else { var ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand("copy"); } catch (e) {} document.body.removeChild(ta); done(); }
    });
    block.appendChild(btn);
  });

  /* ---- TOC scroll-spy ---------------------------------------------------- */
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll(".toc a[href^='#']"));
  if (tocLinks.length && "IntersectionObserver" in window) {
    var map = {};
    var targets = tocLinks.map(function (a) {
      var id = decodeURIComponent(a.getAttribute("href").slice(1));
      var el = document.getElementById(id); if (el) map[id] = a; return el;
    }).filter(Boolean);
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          tocLinks.forEach(function (l) { l.classList.remove("active"); });
          var a = map[en.target.id]; if (a) a.classList.add("active");
        }
      });
    }, { rootMargin: "-15% 0px -75% 0px", threshold: 0 });
    targets.forEach(function (t) { obs.observe(t); });
  }

  /* ---- scroll reveal ----------------------------------------------------- */
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var reveals = document.querySelectorAll(".reveal");
  if (reveals.length && !reduce && "IntersectionObserver" in window) {
    root.classList.add("js-anim");
    var rObs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); rObs.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });
    reveals.forEach(function (el) { rObs.observe(el); });
  }

  /* ---- lightbox (click a gallery image to enlarge) ----------------------- */
  var zoomables = document.querySelectorAll(".zoomable");
  if (zoomables.length) {
    var lb = null;
    function closeLb() { if (lb) { lb.remove(); lb = null; document.removeEventListener("keydown", escLb); } }
    function escLb(e) { if (e.key === "Escape") closeLb(); }
    function openLb(src, cap) {
      closeLb();
      lb = document.createElement("div"); lb.className = "lightbox";
      var img = document.createElement("img"); img.src = src; img.alt = cap || "";
      var btn = document.createElement("button"); btn.className = "lb-close"; btn.type = "button";
      btn.setAttribute("aria-label", "Close"); btn.innerHTML = "✕";
      lb.appendChild(btn); lb.appendChild(img);
      if (cap) { var c = document.createElement("div"); c.className = "lb-cap"; c.textContent = cap; lb.appendChild(c); }
      lb.addEventListener("click", function (e) { if (e.target === lb || e.target === btn) closeLb(); });
      document.body.appendChild(lb);
      document.addEventListener("keydown", escLb);
    }
    zoomables.forEach(function (el) {
      el.setAttribute("role", "button"); el.setAttribute("tabindex", "0");
      el.addEventListener("click", function () { openLb(el.getAttribute("src"), el.getAttribute("data-cap") || ""); });
      el.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openLb(el.getAttribute("src"), el.getAttribute("data-cap") || ""); } });
    });
  }
})();
