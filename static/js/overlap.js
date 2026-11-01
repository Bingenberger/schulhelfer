/* Überschneidungswarnung.
 *
 * Zwei gleichzeitige Schichten sind zulässig – etwa wenn sich beide Elternteile
 * über eine Anmeldung eintragen. Der Hinweis erscheint deshalb direkt bei der
 * Auswahl und verlangt eine bewusste Bestätigung, statt beim Absenden einen
 * Systemdialog zu öffnen. Gilt für beide Formulare (Anmeldung und Ergänzung).
 */
(function () {
  'use strict';

  var form = document.getElementById('booking-form');
  var warning = document.getElementById('overlap-warning');
  if (!form || !warning) return;

  var CONFIRM_ID = 'overlap-confirm';

  function boxes() {
    return Array.prototype.slice.call(
      form.querySelectorAll('input[name="shift_ids"]')
    );
  }

  function rangeOf(box) {
    var start = new Date(box.dataset.start).getTime();
    var minutes = parseInt(box.dataset.duration, 10);
    if (isNaN(start) || isNaN(minutes)) return null;
    return { start: start, end: start + minutes * 60000, box: box };
  }

  function findOverlaps() {
    var ranges = boxes()
      .filter(function (b) { return b.checked; })
      .map(rangeOf)
      .filter(Boolean);

    var pairs = [];
    var hit = new Set();
    for (var i = 0; i < ranges.length; i++) {
      for (var j = i + 1; j < ranges.length; j++) {
        if (ranges[i].start < ranges[j].end && ranges[j].start < ranges[i].end) {
          pairs.push([ranges[i].box, ranges[j].box]);
          hit.add(ranges[i].box);
          hit.add(ranges[j].box);
        }
      }
    }
    return { pairs: pairs, boxes: hit };
  }

  function label(box) {
    return box.dataset.label || 'Schicht';
  }

  function render(result) {
    boxes().forEach(function (box) {
      var row = box.closest('.shift-item');
      if (row) row.classList.toggle('is-overlapping', result.boxes.has(box));
    });

    if (!result.pairs.length) {
      warning.hidden = true;
      warning.textContent = '';
      return;
    }

    var confirmed = false;
    var previous = document.getElementById(CONFIRM_ID);
    if (previous) confirmed = previous.checked;

    warning.textContent = '';

    var heading = document.createElement('p');
    heading.className = 'overlap-title';
    heading.textContent = result.pairs.length === 1
      ? 'Zwei gewählte Schichten überschneiden sich zeitlich'
      : 'Mehrere gewählte Schichten überschneiden sich zeitlich';
    warning.appendChild(heading);

    var list = document.createElement('ul');
    result.pairs.forEach(function (pair) {
      var li = document.createElement('li');
      li.textContent = label(pair[0]) + ' ↔ ' + label(pair[1]);
      list.appendChild(li);
    });
    warning.appendChild(list);

    var row = document.createElement('label');
    row.className = 'checkbox-row';
    row.setAttribute('for', CONFIRM_ID);

    var check = document.createElement('input');
    check.type = 'checkbox';
    check.id = CONFIRM_ID;
    check.checked = confirmed;
    check.addEventListener('change', function () {
      warning.classList.toggle('is-unconfirmed', !check.checked);
    });

    var text = document.createElement('span');
    text.textContent = 'Das ist beabsichtigt – die Anmeldung gilt für zwei Personen '
      + '(z. B. beide Elternteile).';

    row.appendChild(check);
    row.appendChild(text);
    warning.appendChild(row);

    warning.hidden = false;
    warning.classList.toggle('is-unconfirmed', !confirmed);
  }

  function update() {
    render(findOverlaps());
  }

  form.addEventListener('change', function (event) {
    if (event.target.name === 'shift_ids') update();
  });

  form.addEventListener('submit', function (event) {
    var result = findOverlaps();
    if (!result.pairs.length) return;

    var check = document.getElementById(CONFIRM_ID);
    if (check && check.checked) return;

    event.preventDefault();
    render(result);
    warning.classList.add('is-unconfirmed');
    warning.scrollIntoView({ block: 'center', behavior: 'smooth' });
    if (check) check.focus();
  });

  update();
}());
