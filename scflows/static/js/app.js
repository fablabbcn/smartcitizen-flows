// Small enhancements for the interface. Every page works without them.
(function () {
  'use strict';

  // UTC clock in the header (all times in the interface are UTC)
  var clock = document.querySelector('[data-clock]');
  if (clock) {
    var tick = function () {
      clock.textContent = new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
    };
    tick();
    setInterval(tick, 10000);
  }

  // Theme switch: auto (system setting), light, dark. The choice is kept in this browser
  var themeSwitch = document.querySelector('[data-theme-switch]');
  var systemDark = window.matchMedia('(prefers-color-scheme: dark)');
  var savedTheme = function () {
    try { return localStorage.getItem('theme') || 'auto'; } catch (error) { return 'auto'; }
  };
  var applyTheme = function (choice) {
    var theme = choice === 'auto' ? (systemDark.matches ? 'dark' : 'light') : choice;
    document.documentElement.setAttribute('data-theme', theme);
    if (themeSwitch) themeSwitch.textContent = choice.charAt(0).toUpperCase() + choice.slice(1);
  };
  applyTheme(savedTheme());
  systemDark.addEventListener('change', function () { if (savedTheme() === 'auto') applyTheme('auto'); });
  if (themeSwitch) {
    themeSwitch.addEventListener('click', function () {
      var next = { auto: 'light', light: 'dark', dark: 'auto' }[savedTheme()] || 'auto';
      try { localStorage.setItem('theme', next); } catch (error) {}
      applyTheme(next);
    });
  }

  // Tables: filter rows with a search box and sort by clicking headers
  document.querySelectorAll('[data-table]').forEach(function (container) {
    var table = container.querySelector('table');
    var body = table.tBodies[0];
    var rows = Array.prototype.slice.call(body.rows).filter(function (row) { return !row.querySelector('.empty'); });
    var search = container.querySelector('[data-search]');
    var count = container.querySelector('[data-count]');

    var update = function () {
      var terms = search ? search.value.toLowerCase().trim().split(/\s+/).filter(Boolean) : [];
      var shown = 0;
      rows.forEach(function (row) {
        var text = row.textContent.toLowerCase();
        var visible = terms.every(function (term) { return text.indexOf(term) !== -1; });
        row.hidden = !visible;
        if (visible) shown++;
      });
      if (count) count.textContent = shown === rows.length ? rows.length + ' total' : shown + ' of ' + rows.length;
    };
    if (search) search.addEventListener('input', update);
    update();

    table.querySelectorAll('th[data-sort]').forEach(function (header) {
      header.addEventListener('click', function () {
        var column = header.cellIndex;
        var ascending = header.getAttribute('aria-sort') !== 'ascending';
        table.querySelectorAll('th[aria-sort]').forEach(function (other) { other.removeAttribute('aria-sort'); });
        header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
        var key = function (row) {
          var cell = row.cells[column];
          return cell ? (cell.getAttribute('data-value') || cell.textContent.trim()) : '';
        };
        var numeric = header.getAttribute('data-sort') === 'number';
        rows.sort(function (a, b) {
          var x = key(a), y = key(b);
          var result = numeric ? (parseFloat(x) || 0) - (parseFloat(y) || 0) : x.localeCompare(y, undefined, { numeric: true });
          return ascending ? result : -result;
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  });

  // Tabs inside a page: panels are shown one at a time, the choice is kept in the url hash
  document.querySelectorAll('[data-tabs]').forEach(function (tabs) {
    var buttons = tabs.querySelectorAll('[data-tab]');
    var select = function (name) {
      buttons.forEach(function (button) {
        var selected = button.getAttribute('data-tab') === name;
        button.setAttribute('aria-selected', selected ? 'true' : 'false');
        document.getElementById(button.getAttribute('data-tab')).hidden = !selected;
      });
    };
    buttons.forEach(function (button) {
      button.addEventListener('click', function () {
        select(button.getAttribute('data-tab'));
        history.replaceState(null, '', '#' + button.getAttribute('data-tab'));
      });
    });
    var initial = location.hash.slice(1);
    var known = Array.prototype.some.call(buttons, function (button) { return button.getAttribute('data-tab') === initial; });
    select(known ? initial : buttons[0].getAttribute('data-tab'));
  });

  // Log viewer: filter by level and copy
  document.querySelectorAll('[data-log-filter]').forEach(function (filter) {
    var log = document.getElementById(filter.getAttribute('data-log-filter'));
    filter.querySelectorAll('button').forEach(function (button) {
      button.addEventListener('click', function () {
        filter.querySelectorAll('button').forEach(function (other) { other.setAttribute('aria-selected', 'false'); });
        button.setAttribute('aria-selected', 'true');
        log.setAttribute('data-filter', button.value);
      });
    });
  });

  document.querySelectorAll('[data-copy]').forEach(function (button) {
    button.addEventListener('click', function () {
      var source = document.getElementById(button.getAttribute('data-copy'));
      var text = Array.prototype.map.call(source.querySelectorAll('.log-line'), function (line) {
        return line.getAttribute('data-text');
      }).join('\n');
      navigator.clipboard.writeText(text).then(function () {
        var label = button.textContent;
        button.textContent = 'Copied';
        setTimeout(function () { button.textContent = label; }, 1500);
      });
    });
  });

  // Confirmation for destructive actions
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      if (!confirm(form.getAttribute('data-confirm'))) event.preventDefault();
    });
  });

  // Slow forms (signing in can take 30 seconds): show progress and avoid double submits
  document.querySelectorAll('form[data-busy]').forEach(function (form) {
    form.addEventListener('submit', function () {
      var button = form.querySelector('button[type="submit"]');
      if (button) {
        button.disabled = true;
        button.textContent = form.getAttribute('data-busy');
      }
    });
  });
})();
