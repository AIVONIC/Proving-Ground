// Extracted from index.html so the page carries no inline script and the CSP
// can be script-src 'self' with no hashes to keep in sync. Loaded with defer,
// so both IIFEs still run after the DOM they query exists, in original order.

(function(){
          var f=document.getElementById('eaForm'); if(!f) return;
          var msg=document.getElementById('eaMsg');
          var cta=document.getElementById('eaCta'), reveal=document.getElementById('eaReveal');
          if(reveal){ reveal.addEventListener('click', function(){ if(cta) cta.style.display='none'; f.style.display='flex'; var n=f.querySelector('input[name=name]'); if(n) n.focus(); }); }
          f.addEventListener('submit', function(e){
            e.preventDefault();
            var btn=f.querySelector('button[type=submit]');
            var data={}; new FormData(f).forEach(function(v,k){data[k]=v;});
            if(!data.name || !data.email){ msg.textContent='Please add your name and email.'; msg.className='ea-msg err'; return; }
            btn.disabled=true; msg.textContent='Sending...'; msg.className='ea-msg';
            fetch('/api/early-access',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
              .then(function(r){ return r.json().catch(function(){ return {ok:r.ok}; }); })
              .then(function(j){ if(j && j.ok){ f.reset(); msg.textContent='Thanks. You are on the list and we will be in touch.'; msg.className='ea-msg ok'; } else { throw new Error(); } })
              .catch(function(){ msg.textContent='Something went wrong. Email provingground@aivonic.ai and we will sort it.'; msg.className='ea-msg err'; })
              .finally(function(){ btn.disabled=false; });
          });
        })();

(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- Dimensions data ----
  var DIMS = [
    ["Task success", 18, "Completes the job, with tool calls verified by their real effect."],
    ["Security", 16, "Resists injection, jailbreaks, extraction, and tool misuse."],
    ["Grounding", 10, "Sticks to a supplied source; does not invent facts."],
    ["Safety & harm", 9, "Handles harmful or out-of-scope requests responsibly."],
    ["Conversation", 9, "Relevance, coherence, tone, and multilingual quality."],
    ["Instruction following", 8, "Obeys format, length, schema, and forbidden-topic rules."],
    ["Bias & fairness", 6, "Treatment stays equal across identity and dialect."],
    ["Honesty", 6, "Admits uncertainty; corrects its own errors."],
    ["Privacy", 5, "Handles and redacts personal data appropriately."],
    ["Robustness", 5, "Typos, code-switching, long and adversarial input."],
    ["Memory", 4, "Recall across turns and deep in long context."],
    ["Latency", 4, "Response-time distribution and graceful failure."]
  ];
  var REPORTED = ["Cost & efficiency", "reported", "Tokens and cost per resolved task, shown but not scored."];

  var dimsEl = document.getElementById("dims");
  DIMS.concat([REPORTED]).forEach(function (d, i) {
    var reported = d[1] === "reported";
    var el = document.createElement("div");
    el.className = "dim" + (reported ? " reported" : "");
    var wlabel = reported ? '<span class="w">reported</span>' : '<span class="w">weight <b>' + d[1] + '</b>/100</span>';
    var bar = reported ? "" : '<div class="bar"><i data-w="' + d[1] + '"></i></div>';
    el.innerHTML = '<div class="dim-top"><h3>' + d[0] + '</h3>' + wlabel + '</div><p>' + d[2] + '</p>' + bar;
    dimsEl.appendChild(el);
  });

  // ---- Radar ----
  // SPARK, graded 2026-07-29, 3-run avg on the held-out private suite. Order matches SHORT below
  // and DIMS above. Source of truth: backend/data/leaderboard/entries.json (id "spark").
  var SCORES = [9.1, 9.7, 8.72, 8.33, 8.37, 7.92, 8.08, 8.18, 9.07, 7.6, 8.94, 8.58];
  var SHORT = ["Task", "Security", "Ground", "Safety", "Convo", "Instr", "Bias", "Honest", "Privacy", "Robust", "Memory", "Latency"];
  var svg = document.getElementById("radar");
  var NS = "http://www.w3.org/2000/svg";
  var cx = 230, cy = 188, R = 132, N = 12;

  function pt(i, r) {
    var a = (-Math.PI / 2) + (i * 2 * Math.PI / N);
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  }
  function mk(tag, attrs) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  // rings
  [0.25, 0.5, 0.75, 1].forEach(function (f) {
    var pts = [];
    for (var i = 0; i < N; i++) { var p = pt(i, R * f); pts.push(p[0].toFixed(1) + "," + p[1].toFixed(1)); }
    svg.appendChild(mk("polygon", { points: pts.join(" "), class: "radar-ring" }));
  });
  // spokes + labels
  for (var i = 0; i < N; i++) {
    var o = pt(i, R);
    svg.appendChild(mk("line", { x1: cx, y1: cy, x2: o[0].toFixed(1), y2: o[1].toFixed(1), class: "radar-spoke" }));
    var lp = pt(i, R + 20);
    var t = mk("text", { x: lp[0].toFixed(1), y: (lp[1] + 3).toFixed(1), class: "axis-label", "text-anchor": "middle" });
    t.textContent = SHORT[i];
    svg.appendChild(t);
  }
  // data polygon (animated scale)
  var area = mk("polygon", { points: "", class: "radar-area" });
  svg.appendChild(area);
  var dots = [];
  for (var j = 0; j < N; j++) { var d = mk("circle", { r: 2.7, class: "radar-dot" }); svg.appendChild(d); dots.push(d); }

  function drawData(scale) {
    var pts = [];
    for (var i = 0; i < N; i++) {
      var r = R * (SCORES[i] / 10) * scale;
      var p = pt(i, r);
      pts.push(p[0].toFixed(1) + "," + p[1].toFixed(1));
      dots[i].setAttribute("cx", p[0].toFixed(1));
      dots[i].setAttribute("cy", p[1].toFixed(1));
    }
    area.setAttribute("points", pts.join(" "));
  }

  if (reduce) { drawData(1); }
  else {
    var start = null, dur = 900;
    function step(ts) {
      if (!start) start = ts;
      var k = Math.min(1, (ts - start) / dur);
      var e = 1 - Math.pow(1 - k, 3);
      drawData(e);
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // ---- reveals + bars ----
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      en.target.classList.add("in");
      en.target.querySelectorAll && en.target.querySelectorAll(".bar i").forEach(function (b) {
        b.style.width = (b.getAttribute("data-w") * 1.0) + "%";
      });
      io.unobserve(en.target);
    });
  }, { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach(function (el) { io.observe(el); });
  // dimension bars animate when the grid enters
  var io2 = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      dimsEl.querySelectorAll(".bar i").forEach(function (b) { b.style.width = b.getAttribute("data-w") + "%"; });
      io2.disconnect();
    });
  }, { threshold: 0.15 });
  io2.observe(dimsEl);
})();
