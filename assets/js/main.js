// Copy-to-clipboard for BibTeX
document.querySelectorAll('.copy-btn').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var target = document.getElementById(btn.dataset.copy + '-text');
    if (!target) return;
    var text = target.innerText;
    navigator.clipboard.writeText(text).then(function () {
      var original = btn.textContent;
      btn.textContent = 'Copied ✓';
      setTimeout(function () { btn.textContent = original; }, 1600);
    }).catch(function () {
      btn.textContent = 'Press Ctrl+C';
    });
  });
});

// Subtle reveal-on-scroll (progressive enhancement only).
// Skipped entirely when the user prefers reduced motion.
var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
var revealEls = document.querySelectorAll('.stat-card, .overview-figure, .env-feature, .results, .code-block');

if (!reduceMotion && 'IntersectionObserver' in window) {
  var reveal = function (el) { el.style.opacity = '1'; el.style.transform = 'none'; };
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { reveal(e.target); io.unobserve(e.target); }
    });
  }, { threshold: 0.08 });

  revealEls.forEach(function (el) {
    el.style.opacity = '0';
    el.style.transform = 'translateY(14px)';
    el.style.transition = 'opacity .5s ease, transform .5s ease';
    io.observe(el);
  });

  // Safety net: never leave content hidden (e.g. background tabs, odd viewports).
  setTimeout(function () { revealEls.forEach(reveal); }, 2500);
}
