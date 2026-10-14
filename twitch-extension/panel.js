// Fly Brain Live: renders the snapshot the Pi publishes.
// Initial state: broadcaster configuration segment (set by the Pi). Updates: broadcast pubsub.
// No network calls of our own; everything arrives through Twitch.
//
// Payload (JSON, < 5 KB):
// { t: epoch s,
//   attrib:   { tiles_clean, tiles_assisted },
//   learning: { in_effect, from: { human, game, rule, before_ledger } },
//   brain:    { eyes, cx, kc, pam, ppl1, motor }            (Hz)
//   thoughts: { per_min, phantom_pct, now },
//   team:     { lead, lv, badges, caught },
//   experiment: "one line of status" }
(function () {
  var last = null, lastT = 0;
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var num = function (v, d) { return v == null || isNaN(v) ? '—' : Number(v).toFixed(d || 0); };
  var pct = function (a, b) { return a + b > 0 ? 100 * a / (a + b) : null; };

  function split(parts) {
    var tot = parts.reduce(function (p, x) { return p + (x[1] || 0); }, 0);
    if (!tot) return '<div class="sub">no data yet</div>';
    return '<div class="split">' + parts.map(function (x) {
      return '<span style="width:' + (100 * (x[1] || 0) / tot) + '%;background:' + x[2] + '"></span>';
    }).join('') + '</div><div class="leg">' + parts.filter(function (x) { return x[1] > 0; }).map(function (x) {
      return '<span><i style="background:' + x[2] + '"></i>' + esc(x[0]) + ' ' +
        Math.round(100 * x[1] / tot) + '%</span>';
    }).join('') + '</div>';
  }

  function bars(items) {
    var mx = Math.max.apply(null, items.map(function (x) { return x[1] || 0; }).concat([1]));
    return items.map(function (x) {
      return '<div class="row"><span>' + esc(x[0]) + '</span><span class="bar"><span style="width:' +
        Math.min(100, 100 * (x[1] || 0) / mx) + '%;background:' + x[2] + '"></span></span><span class="n">' +
        num(x[1], x[1] < 10 ? 1 : 0) + '</span></div>';
    }).join('');
  }

  function render(d) {
    if (!d || typeof d !== 'object') return;
    last = d; lastT = Date.now();
    var a = d.attrib || {}, L = d.learning || {}, B = d.brain || {}, T = d.thoughts || {}, tm = d.team || {};
    var own = pct(a.tiles_clean || 0, a.tiles_assisted || 0);
    var src = L.from || {};
    var h = '';
    h += '<section><h2>Is it really the fly?</h2><div class="big">' + (own == null ? '—' : Math.round(own) + '%') +
      ' <span class="sub">of steps on its own</span></div>' +
      split([['fly alone', a.tiles_clean, 'var(--ok)'], ['chat helped', a.tiles_assisted, 'var(--tw)']]) +
      '<div class="sub">A step is the fly\'s own only if no human key reached the game for 5 s.</div></section>';
    h += '<section><h2>Who trained it</h2>' +
      split([['chat', src.human, 'var(--tw)'], ['the game', src.game, 'var(--warn)'],
             ['stillness rule', src.rule, 'var(--bad)'], ['before tracking', src.before_ledger, 'var(--faint)']]) +
      '<div class="sub">Lessons fade over about 3 hours. ' + num(L.in_effect) + ' units still in effect.</div></section>';
    h += '<section><h2>Brain right now (Hz)</h2>' + bars([
      ['eyes', B.eyes, '#5b8def'], ['navigation', B.cx, 'var(--acc)'], ['memory (KC)', B.kc, 'var(--warn)'],
      ['reward', B.pam, 'var(--ok)'], ['punishment', B.ppl1, 'var(--bad)'], ['motor', B.motor, '#9292a3']]) +
      '</section>';
    if (T.per_min != null) {
      h += '<section><h2>Thoughts</h2><div class="kv"><span>per minute</span><b>' + num(T.per_min) + '</b></div>' +
        '<div class="kv"><span>stops with nothing there</span><b>' + num(T.phantom_pct) + '%</b></div>' +
        (T.now ? '<div class="sub">now: ' + esc(T.now) + '</div>' : '') + '</section>';
    }
    if (tm.lead) {
      h += '<section><h2>Team</h2><div class="kv"><span>' + esc(tm.lead) + '</span><b>Lv ' + esc(tm.lv) + '</b></div>' +
        '<div class="kv"><span>badges · caught</span><b>' + esc(tm.badges) + ' · ' + esc(tm.caught) + '</b></div></section>';
    }
    if (d.experiment) h += '<section><h2>Experiment</h2><div class="sub">' + esc(d.experiment) + '</div></section>';
    document.getElementById('m').innerHTML = h;
    tick();
  }

  function tick() {
    var dot = document.getElementById('dot'), age = document.getElementById('age');
    if (!last) return;
    var s = Math.round((Date.now() - (last.t ? last.t * 1000 : lastT)) / 1000);
    dot.className = 'dot' + (s < 180 ? ' live' : '');
    age.textContent = s < 90 ? 'live' : (s < 3600 ? Math.round(s / 60) + ' min ago' : 'offline');
  }
  setInterval(tick, 15000);

  var ext = window.Twitch && window.Twitch.ext;
  if (!ext) return;
  ext.configuration.onChanged(function () {
    var c = ext.configuration.broadcaster;
    if (c && c.content) { try { render(JSON.parse(c.content)); } catch (e) {} }
  });
  ext.listen('broadcast', function (target, type, msg) {
    try { render(JSON.parse(msg)); } catch (e) {}
  });
})();
