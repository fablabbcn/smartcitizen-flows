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
      // Sortable with the keyboard too
      header.tabIndex = 0;
      header.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          header.click();
        }
      });
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

  // Selection in tables: the header box ticks the rows shown (after filtering); the count says how many
  document.querySelectorAll('[data-select-all]').forEach(function (all) {
    var table = all.closest('table');
    // Only the selection boxes (not the dry run boxes of the rows)
    var boxes = function () { return Array.prototype.slice.call(table.querySelectorAll('tbody input[type=checkbox][name=job_id]')); };
    var label = document.querySelector('[data-selected]');
    var hint = label ? label.textContent : '';
    // Only the rows shown count (and are sent)
    var ticked = function () {
      return boxes().filter(function (box) { return box.checked && !box.closest('tr').hidden; }).length;
    };
    var update = function () {
      var count = ticked();
      if (label) label.textContent = count ? count + ' selected' : hint;
    };
    all.addEventListener('change', function () {
      boxes().forEach(function (box) {
        if (!box.closest('tr').hidden) box.checked = all.checked;
      });
      update();
    });
    table.addEventListener('change', function (event) {
      if (event.target !== all) update();
    });
    // The table filter runs first (registered earlier), then the count follows it
    var container = table.closest('[data-table]');
    var search = container ? container.querySelector('[data-search]') : null;
    if (search) search.addEventListener('input', update);
    // Only the rows shown are sent: untick the ones a later filter hid, and confirm
    var form = boxes().length ? boxes()[0].form : null;
    if (form) {
      form.addEventListener('submit', function (event) {
        boxes().forEach(function (box) { if (box.closest('tr').hidden) box.checked = false; });
        update();
        var count = ticked();
        var action = event.submitter ? event.submitter.value : 'change';
        if (count && !confirm(action.charAt(0).toUpperCase() + action.slice(1) + ' ' + count + ' jobs?')) event.preventDefault();
      });
    }
    update();
  });

  // Sensor ids: the Alphasense type from the code of the id (the table comes from smartcitizen-connector), as typed
  var codesElement = document.getElementById('sensor-codes');
  var codes = null;
  try { codes = codesElement ? JSON.parse(codesElement.textContent) : null; } catch (error) {}
  var describe = function (id) {
    id = (id || '').trim();
    if (!id) return null;
    var afe = /^([0-9]{2})-[0-9]{6}$/.exec(id);
    if (afe) {
      return codes.afe.indexOf(afe[1]) !== -1 ? { label: 'AFE board', title: 'Alphasense AFE board, for A-series sensors with a PT1000 (code ' + afe[1] + ')', afe: true }
        : { warning: 'AFE board id with an unknown code ' + afe[1], afe: true };
    }
    // An AFE board id being typed (fewer than six digits after the dash), or one with the wrong length
    if (/^[0-9]{2}(-[0-9]{0,5})?$/.test(id) || id.length < 3) return null;
    if (/^[0-9]{2}-/.test(id)) return { warning: 'AFE board ids have six digits after the dash (e.g. 10-002911)', afe: true };
    var found = codes.sensors[id.slice(0, 3)];
    return found ? { label: found[0], title: found[1], gain: found[2], afe: false } : { warning: 'Unknown Alphasense code ' + id.slice(0, 3), afe: false };
  };
  if (codes) {
    // New calibration: the type under the id, and whether it needs the other form
    document.querySelectorAll('[data-sensor-type]').forEach(function (hint) {
      var input = document.getElementById(hint.getAttribute('data-sensor-type'));
      var kind = document.getElementById(hint.getAttribute('data-kind'));
      var update = function () {
        var found = describe(input.value);
        hint.textContent = '';
        hint.classList.remove('warning-text');
        if (!found) return;
        var text = found.label ? found.label + ' · ' + found.title : found.warning;
        if (kind && (found.afe ? 'afe_board' : 'alphasense_sensor') !== kind.value) {
          text += ': choose ' + (found.afe ? 'AFE board' : 'Alphasense sensor') + ' and press Change';
          hint.classList.add('warning-text');
        }
        if (found.warning) hint.classList.add('warning-text');
        hint.textContent = text;
        // The usual PCB gain of this type, for the gain check below
        var gainBox = document.querySelector('[data-gain-check]');
        var gainField = document.getElementById('pcb_gain_mv_na');
        if (gainBox && gainField && !found.afe) {
          gainBox.setAttribute('data-gain-check', found.gain === null || found.gain === undefined ? '' : found.gain);
          gainBox.setAttribute('data-gain-model', found.label || '');
          gainField.dispatchEvent(new Event('input'));
        }
      };
      input.addEventListener('input', update);
      if (kind) kind.addEventListener('change', update);
      update();
    });
    // Hardware versions: the type of each slot, line by line
    document.querySelectorAll('[data-slot-types]').forEach(function (list) {
      var area = document.getElementById(list.getAttribute('data-slot-types'));
      var update = function () {
        list.textContent = '';
        area.value.split('\n').forEach(function (line) {
          var parts = line.split('=');
          if (parts.length !== 2 || !parts[1].trim()) return;
          var slot = parts[0].trim(), id = parts[1].trim(), found = describe(id);
          if (!found) return;
          var item = document.createElement('li');
          var problem = found.warning || (slot.indexOf('PT') === 0 && !found.afe ? 'a PT slot needs an AFE board id'
            : slot.indexOf('AS') === 0 && found.afe ? 'an AS slot needs an Alphasense sensor id' : null);
          item.textContent = slot + ' ' + id + ': ' + (found.label || '') + (problem ? (found.label ? ' · ' : '') + problem : '');
          if (found.title) item.title = found.title;
          if (problem) item.className = 'warning-text';
          list.appendChild(item);
        });
      };
      area.addEventListener('input', update);
      update();
    });
  }

  // Calibrations: mV/ppb = nA/ppb × PCB gain, worked out as the values are typed, and the usual gain of the sensor type
  var gainInput = document.getElementById('pcb_gain_mv_na');
  if (gainInput) {
    var gainBox = document.querySelector('[data-gain-check]');
    var gainHint = document.querySelector('[data-gain-hint]');
    // As the server: only whole numbers count ("-0.73abc" is not -0.73), and blanks are empty
    var number = function (value) { var parsed = value.trim() === '' ? NaN : Number(value); return isFinite(parsed) ? parsed : null; };
    var round = function (value) { return parseFloat(value.toPrecision(4)).toString(); };
    var updateRelations = function () {
      var gain = number(gainInput.value);
      document.querySelectorAll('[data-gain-value]').forEach(function (item) { item.textContent = gain === null ? 'gain' : gainInput.value.trim(); });
      document.querySelectorAll('[data-relation]').forEach(function (row) {
        var na = number(document.getElementById(row.getAttribute('data-na')).value);
        var mv = number(document.getElementById(row.getAttribute('data-mv')).value);
        var out = row.querySelector('[data-expected]');
        out.textContent = '';
        out.classList.remove('warning-text');
        if (gain === null || na === null) return;
        var expected = na * gain;
        out.textContent = round(na) + ' × ' + round(gain) + ' = ' + round(expected) + ' mV/ppb';
        if (mv !== null && Math.abs(mv - expected) > 0.01 * Math.max(Math.abs(expected), Math.abs(mv))) {
          out.textContent += ', not ' + round(mv) + ' (more than 1% apart)';
          out.classList.add('warning-text');
        }
      });
      var usual = gainBox ? number(gainBox.getAttribute('data-gain-check')) : null;
      if (gainHint) {
        var off = usual !== null && gain !== null && Math.abs(gain - usual) > 0.005;
        gainHint.textContent = usual === null ? 'pcb_gain_mv_na'
          : gainBox.getAttribute('data-gain-model') + ' sensors have ' + round(usual) + (off ? ', not ' + round(gain) : '');
        gainHint.classList.toggle('warning-text', off);
        gainHint.classList.toggle('mono', usual === null);
      }
    };
    document.querySelectorAll('[data-relation] input, #pcb_gain_mv_na').forEach(function (input) {
      input.addEventListener('input', updateRelations);
    });
    updateRelations();
  }

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
    // The hash names a panel: keep the page at the top instead of jumping to it
    if (known) window.addEventListener('load', function () { window.scrollTo(0, 0); });
  });

  // Log viewer: filter by level and copy
  document.querySelectorAll('[data-log-filter]').forEach(function (filter) {
    var log = document.getElementById(filter.getAttribute('data-log-filter'));
    filter.querySelectorAll('button').forEach(function (button) {
      button.addEventListener('click', function () {
        filter.querySelectorAll('button').forEach(function (other) { other.setAttribute('aria-pressed', 'false'); });
        button.setAttribute('aria-pressed', 'true');
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
      var label = button.textContent;
      var show = function (message) {
        button.textContent = message;
        setTimeout(function () { button.textContent = label; }, 2000);
      };
      var failed = function () {
        // Select the log to copy it by hand (the clipboard needs https and permission)
        var range = document.createRange();
        range.selectNodeContents(source);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        show('Selected: press Ctrl+C');
      };
      if (!navigator.clipboard || !window.isSecureContext) return failed();
      navigator.clipboard.writeText(text).then(function () { show('Copied'); }, failed);
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
