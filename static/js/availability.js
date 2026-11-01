/* Live-Belegung.
 *
 * Aktualisiert Zählerstände, Fortschrittsbalken und volle Schichten, ohne das
 * Formular neu zu rendern – eine bereits getroffene Auswahl bleibt dadurch
 * erhalten. Eine Schicht, die während des Ausfüllens vollgelaufen ist, wird
 * sichtbar markiert, statt den Nutzer erst nach dem Absenden zu überraschen.
 */
(function () {
  'use strict';

  var INTERVAL_MS = 30000;

  var host = document.querySelector('[data-availability]');
  if (!host) return;

  var url = host.getAttribute('data-availability');
  var stamp = document.querySelector('[data-role="updated"]');
  var timer = null;

  function setBar(container, value, target, kind) {
    if (!container || !target) return;
    var bar = container.querySelector('.progress-bar');
    var fill = container.querySelector('.progress-fill');
    if (!bar || !fill) return;

    var pct = Math.min(100, Math.round((value / target) * 100));
    fill.style.width = pct + '%';
    bar.setAttribute('aria-valuenow', String(value));
    bar.setAttribute('aria-valuemax', String(target));

    fill.classList.remove('is-warning', 'is-full', 'is-goal');
    if (value >= target) {
      fill.classList.add(kind === 'donation' ? 'is-goal' : 'is-full');
    } else if (kind !== 'donation' && pct >= 66) {
      fill.classList.add('is-warning');
    }
  }

  function updateShift(row, data) {
    var count = row.querySelector('[data-role="count"]');
    if (count) {
      count.textContent = data.booked + '/' + data.required;
      count.classList.toggle('is-full', data.full);
    }

    setBar(row.querySelector('[data-role="bar"]'), data.booked, data.required);

    var badge = row.querySelector('[data-role="full-badge"]');
    if (badge) badge.hidden = !data.full;

    var box = row.querySelector('input[type="checkbox"]');
    if (!box) {
      row.classList.toggle('is-full', data.full);
      return;
    }

    if (box.checked) {
      // Auswahl niemals still zurücknehmen – nur darauf hinweisen, dass die
      // Schicht inzwischen belegt ist.
      var contested = data.booked >= data.required;
      row.classList.toggle('is-contested', contested);
      var note = row.querySelector('[data-role="contested-note"]');
      if (contested && !note) {
        note = document.createElement('span');
        note.className = 'shift-note';
        note.setAttribute('data-role', 'contested-note');
        note.textContent = 'Inzwischen belegt – Ihre Auswahl kann abgelehnt werden.';
        row.appendChild(note);
      } else if (!contested && note) {
        note.remove();
      }
      return;
    }

    box.disabled = data.full;
    row.classList.toggle('is-full', data.full);
  }

  function updateDonation(article, data) {
    var total = article.querySelector('[data-role="don-total"]');
    if (total && data.target) total.textContent = data.total + '/' + data.target;

    if (data.target) {
      setBar(article.querySelector('[data-role="don-bar"]'), data.total, data.target, 'donation');
      var goal = article.querySelector('[data-role="don-goal"]');
      if (goal) goal.hidden = data.total < data.target;
    }

    var list = article.querySelector('[data-role="don-list"]');
    if (!list) return;

    list.textContent = '';
    if (!data.items.length) {
      var empty = document.createElement('li');
      empty.className = 'text-muted';
      var em = document.createElement('em');
      em.textContent = 'Noch keine Zusagen.';
      empty.appendChild(em);
      list.appendChild(empty);
      return;
    }
    data.items.forEach(function (item) {
      var li = document.createElement('li');
      li.className = 'text-muted';
      li.textContent = item.text + ' (×' + item.quantity + ')';
      list.appendChild(li);
    });
  }

  function apply(payload) {
    Object.keys(payload.shifts || {}).forEach(function (id) {
      var row = document.querySelector('li[data-shift="' + id + '"]');
      if (row) updateShift(row, payload.shifts[id]);
    });

    Object.keys(payload.donations || {}).forEach(function (id) {
      var article = document.querySelector('article[data-station="' + id + '"][data-type="donation"]');
      if (article) updateDonation(article, payload.donations[id]);
    });

    if (stamp) stamp.textContent = 'Belegung aktualisiert um ' + payload.updated + ' Uhr.';
  }

  function poll() {
    fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (payload) { if (payload) apply(payload); })
      .catch(function () { /* Netzwerkaussetzer still übergehen */ });
  }

  function start() {
    if (timer === null) timer = window.setInterval(poll, INTERVAL_MS);
  }

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
  }

  // Im Hintergrundtab nicht weiterpollen.
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      stop();
    } else {
      poll();
      start();
    }
  });

  poll();
  start();
}());
