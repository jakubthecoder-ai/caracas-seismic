/**
 * Seismic Monitor — SSE Client
 *
 * Handles: SSE connection, alarm banner updates (i18n-aware), sound/notifications,
 * details panel toggle with lazy Leaflet loading, seismogram rendering,
 * isoseismal circles, ghost ring animations, distance rings, fault lines.
 */

(function () {
  'use strict';

  // State
  let detailsVisible = false;
  let soundEnabled = true;
  let notifEnabled = false;
  let map = null;
  let stationMarkers = {};
  let eventMarkers = [];
  let isoseismalLayers = [];
  let ghostRings = [];
  let ghostAnimFrame = null;
  let seismogramData = {};  // station -> {data, sps}
  let allEvents = [];       // track events for map features
  let lastAlarmTime = 0;

  // Audio context (lazy init on user gesture)
  let audioCtx = null;
  function getAudioCtx() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return audioCtx;
  }

  // --- i18n helper: format I18N template with params ---
  function formatI18N(key, params) {
    var text = window.I18N[key] || key;
    if (params) {
      Object.keys(params).forEach(function (k) {
        text = text.split('{' + k + '}').join(params[k]);
      });
    }
    return text;
  }

  // --- SSE Connection ---
  var sse = new EventSource('/sse');

  sse.addEventListener('alarm', function (e) {
    var d = JSON.parse(e.data);
    updateBanner(d);
    updateMiniSeismogram(d);
    if (d.level === 'warning' || d.level === 'critical' || d.level === 'high' || d.level === 'medium') {
      lastAlarmTime = Date.now();
      if (soundEnabled) playAlarmSound(d.level);
      if (notifEnabled) sendNotification(d);
    }
  });

  sse.addEventListener('event', function (e) {
    var d = JSON.parse(e.data);
    if (d.event) {
      updateLastEvent(d.event);
      allEvents.push(d.event);
      if (detailsVisible) {
        addEventToList(d.event);
        addEventMarkerWithFeatures(d.event, true);
      }
    }
  });

  sse.addEventListener('waveform', function (e) {
    if (!detailsVisible) return;
    var d = JSON.parse(e.data);
    seismogramData[d.station] = d;
    drawSeismogram();
  });

  sse.addEventListener('status', function (e) {
    var d = JSON.parse(e.data);
    updateConnectionStatus(true, d);
    if (detailsVisible) updateStationMarkers(d.stations || []);
  });

  sse.addEventListener('tsunami', function (e) {
    var d = JSON.parse(e.data);
    handleTsunami(d);
  });

  sse.addEventListener('history', function (e) {
    var d = JSON.parse(e.data);
    if (d.events && d.events.length > 0) {
      allEvents = d.events;
      updateLastEvent(d.events[d.events.length - 1]);
    }
  });

  sse.onopen = function () { updateConnectionStatus(true); };
  sse.onerror = function () { updateConnectionStatus(false); };

  // --- Banner (i18n-aware) ---
  function updateBanner(alarmData) {
    var banner = document.getElementById('banner');
    var level = alarmData.level || 'none';
    banner.className = 'threat-' + level;

    if (alarmData.reason_key && level !== 'none' && level !== 'info' && level !== 'low') {
      banner.textContent = formatI18N(alarmData.reason_key, alarmData.reason_params || {});
    } else {
      banner.textContent = window.I18N.no_threat;
    }
  }

  // --- Mini Seismogram (compact view, SVG) ---
  function updateMiniSeismogram(alarmData) {
    var container = document.getElementById('miniSeismogram');
    if (!alarmData || alarmData.level === 'none') {
      container.classList.remove('active');
      return;
    }
    if (alarmData.waveform_snippet && alarmData.waveform_snippet.length > 0) {
      container.classList.add('active');
      var svg = document.getElementById('miniSvg');
      var data = alarmData.waveform_snippet;
      var w = 600, h = 70, mid = h / 2;
      var maxAmp = Math.max.apply(null, data.map(Math.abs)) || 1;
      var step = w / data.length;
      var path = 'M0,' + mid;
      for (var i = 0; i < data.length; i++) {
        var y = mid - (data[i] / maxAmp) * (mid - 4);
        path += ' L' + (i * step).toFixed(1) + ',' + y.toFixed(1);
      }
      svg.innerHTML = '<path d="' + path + '" stroke="#44aaff" stroke-width="1" fill="none" opacity="0.8"/>' +
        '<line x1="0" y1="' + mid + '" x2="' + w + '" y2="' + mid + '" stroke="#333" stroke-width="0.5"/>';
    }
  }

  // --- Last Event ---
  function updateLastEvent(evt) {
    if (!evt) return;
    var el = document.getElementById('lastEvent');
    var mag = (evt.mag || 0).toFixed(1);
    var place = evt.place || '?';
    el.innerHTML = '<strong>' + window.I18N.last_event + ':</strong> M' + mag + ' \u2014 ' + place;
  }

  // --- Connection Status ---
  function updateConnectionStatus(connected) {
    var el = document.getElementById('connStatus');
    if (connected) {
      el.innerHTML = '<span class="dot dot-ok" id="connDot"></span> ' + window.I18N.connected;
    } else {
      el.innerHTML = '<span class="dot dot-err" id="connDot"></span> ' + window.I18N.offline;
    }
    var sseDot = document.getElementById('sseDot');
    if (sseDot) sseDot.style.background = connected ? 'var(--green)' : 'var(--red)';
  }

  // --- Details Toggle ---
  window.toggleDetails = async function () {
    var details = document.getElementById('details');
    var btn = document.getElementById('toggleBtn');
    if (!detailsVisible) {
      btn.textContent = window.I18N.hide_details + ' \u25B2';
      btn.disabled = true;
      try {
        var resp = await fetch('/details?lang=' + window.LANG);
        details.innerHTML = await resp.text();
        details.classList.add('open');

        // Lazy-load Leaflet CSS + JS
        if (!window.L) {
          if (!document.querySelector('link[href*="leaflet"]')) {
            var link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
            document.head.appendChild(link);
          }
          await loadScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js');
        }
        initMap();
        detailsVisible = true;
      } catch (err) {
        console.error('Failed to load details:', err);
        details.innerHTML = '<div style="color:#ff4444;padding:20px;text-align:center">Error loading details</div>';
        details.classList.add('open');
      }
      btn.disabled = false;
    } else {
      details.classList.remove('open');
      details.innerHTML = '';
      detailsVisible = false;
      map = null;
      stationMarkers = {};
      eventMarkers = [];
      isoseismalLayers = [];
      stopGhostAnimation();
      btn.textContent = window.I18N.show_details + ' \u25BC';
    }
  };

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  // --- Map ---
  function initMap() {
    var mapEl = document.getElementById('detailMap');
    if (!mapEl || !window.L) return;

    map = L.map('detailMap').setView([10.5, -66.9], 6);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OSM &copy; CARTO',
      maxZoom: 15,
    }).addTo(map);

    // --- Caracas marker (bright green, prominent) ---
    L.circleMarker([10.5, -66.9], {
      radius: 8, color: '#00ff88', fillColor: '#00ff88', fillOpacity: 0.9, weight: 2,
    }).addTo(map).bindPopup('<strong>CARACAS</strong><br>' + (window.I18N.monitor_target || 'Monitoring target'));

    // --- Distance rings around Caracas ---
    L.circle([10.5, -66.9], {
      radius: 200000, color: '#ff2222', fillOpacity: 0, weight: 1, dashArray: '6,4', opacity: 0.3,
    }).addTo(map).bindPopup(window.I18N.severe_zone || '200 km');

    L.circle([10.5, -66.9], {
      radius: 400000, color: '#ff8800', fillOpacity: 0, weight: 1, dashArray: '6,4', opacity: 0.2,
    }).addTo(map).bindPopup(window.I18N.moderate_zone || '400 km');

    // --- San Sebastian Fault ---
    L.polyline([
      [10.5, -69.5], [10.48, -68.5], [10.5, -67.5], [10.5, -66.9], [10.5, -66], [10.5, -65]
    ], {
      color: '#ff6600', weight: 2, opacity: 0.6,
    }).addTo(map).bindPopup(window.I18N.san_sebastian || 'San Sebastian Fault');

    // --- Station markers from data attributes (triangle-style) ---
    var stationEl = document.getElementById('stationData');
    if (stationEl) {
      var count = parseInt(stationEl.dataset.stationCount) || 0;
      for (var i = 1; i <= count; i++) {
        try {
          var s = JSON.parse(stationEl.dataset['station' + i]);
          if (s.lat && s.lon) {
            var icon = L.divIcon({
              className: 'station-icon',
              html: '<div style="width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:10px solid #22cc44;filter:drop-shadow(0 0 2px #000);"></div>',
              iconSize: [12, 10],
              iconAnchor: [6, 10],
            });
            var marker = L.marker([s.lat, s.lon], { icon: icon })
              .addTo(map)
              .bindPopup('<strong>' + s.station + '</strong><br>' + (s.name || '') + '<br>CFT: ' + (s.cft || 0).toFixed(1));
            stationMarkers[s.station] = marker;
          }
        } catch (e) {}
      }
    }

    // --- Event markers with isoseismal circles ---
    var eventEl = document.getElementById('eventData');
    if (eventEl && eventEl.dataset.events) {
      try {
        var events = JSON.parse(eventEl.dataset.events);
        events.forEach(function (evt) {
          if (evt.lat && evt.lon) {
            addEventMarkerWithFeatures(evt, false);
          }
        });
      } catch (e) {}
    }

    // Invalidate size after render
    setTimeout(function () { map.invalidateSize(); }, 100);
  }

  // --- Event marker with isoseismal circles and ghost label ---
  function addEventMarkerWithFeatures(evt, isNew) {
    if (!map || !window.L) return;
    var mag = evt.mag || 0;
    var now = Date.now();
    var eventTime = evt.time || evt.received_at * 1000 || now;
    var age = now - eventTime;

    // Marker size based on magnitude
    var radius = Math.max(4, Math.min(mag * 3, 20));
    var color = mag >= 6 ? '#ff2222' : mag >= 5 ? '#ff8800' : mag >= 4 ? '#ffcc00' : '#22cc44';

    // Ghost opacity: fresh events are solid, old ones fade
    var GHOST_MAX_AGE = 24 * 3600 * 1000;
    var ghostOpacity = Math.max(0.2, 1 - age / GHOST_MAX_AGE);

    var marker = L.circleMarker([evt.lat, evt.lon], {
      radius: radius, color: color, fillColor: color,
      fillOpacity: 0.4 * ghostOpacity, weight: 2, opacity: ghostOpacity,
    }).addTo(map).bindPopup(
      '<strong>M' + mag.toFixed(1) + '</strong><br>' + (evt.place || '') +
      (evt.depth ? '<br>' + (window.I18N.depth || 'Depth') + ': ' + evt.depth.toFixed(0) + ' km' : '') +
      (evt.dist_km ? '<br>' + (window.I18N.dist_caracas || 'Dist Caracas') + ': ' + Math.round(evt.dist_km) + ' km' : '')
    );
    eventMarkers.push(marker);

    // Ghost time label for events < 6h old and M >= 3.5
    if (age < 6 * 3600000 && mag >= 3.5) {
      var timeStr = new Date(eventTime).toISOString().slice(11, 16) + ' UTC';
      var label = L.divIcon({
        className: 'ghost-label',
        html: '<span style="color:' + color + ';opacity:' + ghostOpacity +
          ';font-size:10px;font-family:monospace;text-shadow:0 0 3px #000,0 0 6px #000;white-space:nowrap;">' +
          'M' + mag.toFixed(1) + ' ' + timeStr + '</span>',
        iconSize: [80, 14],
        iconAnchor: [-radius - 2, 7],
      });
      var lbl = L.marker([evt.lat, evt.lon], { icon: label, interactive: false }).addTo(map);
      eventMarkers.push(lbl);
    }

    // Isoseismal felt-radius circles for M >= 3.0
    if (mag >= 3.0) {
      var feltRadii = calculateFeltRadii(mag);
      // During alarm: solid. After alarm: 30s fade
      var alarmActive = (now - lastAlarmTime) < 300000; // 5 min
      var fadeFraction = 0;
      if (!alarmActive && age > 0) {
        fadeFraction = Math.min(1, age / 60000); // 60s fade after alarm
      }
      if (fadeFraction < 1) {
        var circleOpacity = Math.max(0, 0.5 * (1 - fadeFraction));
        var fillOp = Math.max(0, 0.12 * (1 - fadeFraction));
        feltRadii.forEach(function (fr) {
          var fc = L.circle([evt.lat, evt.lon], {
            radius: fr.radiusKm * 1000,
            color: fr.color, fillColor: fr.color,
            fillOpacity: fillOp,
            weight: Math.max(0.5, 2 * (1 - fadeFraction)),
            opacity: circleOpacity,
            dashArray: fr.mmi <= 3 ? '4 4' : null,
          }).addTo(map).bindPopup(
            '<strong>M' + mag.toFixed(1) + '</strong> \u2014 ' + fr.label + '<br>' +
            (window.I18N.radius || 'Radius') + ': ' + Math.round(fr.radiusKm) + ' km'
          );
          isoseismalLayers.push(fc);
          eventMarkers.push(fc);
        });
      }
    }

    // Ghost ring animation for fresh events
    if (isNew && mag >= 3.0) {
      startGhostRing(evt.lat, evt.lon, color, mag);
    }
  }

  // --- Simplified felt-radius calculation (magnitude-based approximation) ---
  function calculateFeltRadii(mag) {
    // MMI level → approximate radius where that intensity is felt
    // Based on simplified Atkinson & Wald 2007 intensity-distance relations
    var levels = [
      { mmi: 7, color: '#ff2222', label: 'VII' },
      { mmi: 6, color: '#ff6600', label: 'VI' },
      { mmi: 5, color: '#ff8800', label: 'V' },
      { mmi: 4, color: '#ffcc00', label: 'IV' },
      { mmi: 3, color: '#88cc00', label: 'III' },
    ];
    var radii = [];
    levels.forEach(function (l) {
      // Approximate: r_km = 10^((mag - mmi*0.6 + 1.5) / 1.8)
      // Tuned to roughly match BA08 for Caribbean region
      var logR = (mag - l.mmi * 0.6 + 1.5) / 1.8;
      var r = Math.pow(10, logR);
      if (r >= 2 && r <= 800) {
        radii.push({ mmi: l.mmi, radiusKm: r, color: l.color, label: l.label });
      }
    });
    return radii;
  }

  // --- Ghost ring animation (expanding concentric circles from epicenter) ---
  function startGhostRing(lat, lon, color, mag) {
    if (!map || !window.L) return;
    var ring = {
      lat: lat, lon: lon, color: color, mag: mag,
      startTime: Date.now(),
      duration: 30000,
      maxRadius: mag * 30000,
      layers: [],
    };

    // Create 3 concentric expanding rings with staggered start
    for (var i = 0; i < 3; i++) {
      (function (delay) {
        setTimeout(function () {
          if (!map) return;
          var circle = L.circle([lat, lon], {
            radius: 1000, color: color, fillOpacity: 0,
            weight: 2, opacity: 0.6,
          }).addTo(map);
          ring.layers.push({ circle: circle, startTime: Date.now() });
        }, delay);
      })(i * 3000);
    }

    ghostRings.push(ring);
    if (!ghostAnimFrame) animateGhostRings();
  }

  function animateGhostRings() {
    var now = Date.now();

    ghostRings = ghostRings.filter(function (ring) {
      var age = now - ring.startTime;
      if (age > ring.duration + 10000) {
        ring.layers.forEach(function (l) {
          try { map.removeLayer(l.circle); } catch (e) {}
        });
        return false;
      }

      ring.layers.forEach(function (l) {
        var layerAge = now - l.startTime;
        var progress = Math.min(1, layerAge / ring.duration);
        var radius = progress * ring.maxRadius;
        var opacity = Math.max(0, 0.6 * (1 - progress));
        try {
          l.circle.setRadius(radius);
          l.circle.setStyle({ opacity: opacity, weight: Math.max(0.5, 2 * (1 - progress)) });
        } catch (e) {}
      });

      return true;
    });

    if (ghostRings.length > 0) {
      ghostAnimFrame = requestAnimationFrame(animateGhostRings);
    } else {
      ghostAnimFrame = null;
    }
  }

  function stopGhostAnimation() {
    if (ghostAnimFrame) {
      cancelAnimationFrame(ghostAnimFrame);
      ghostAnimFrame = null;
    }
    ghostRings = [];
  }

  // --- Station Markers Update (from SSE status) ---
  function updateStationMarkers(stations) {
    if (!map || !window.L) return;
    stations.forEach(function (s) {
      var marker = stationMarkers[s.station];
      if (marker) {
        var color = s.cft > 3.5 ? '#ff2222' : s.cft > 2.5 ? '#ff8800' : s.cft > 1.5 ? '#ffcc00' : '#22cc44';
        // Update triangle icon color
        var icon = L.divIcon({
          className: 'station-icon',
          html: '<div style="width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:10px solid ' + color + ';filter:drop-shadow(0 0 2px #000);"></div>',
          iconSize: [12, 10],
          iconAnchor: [6, 10],
        });
        marker.setIcon(icon);
        marker.setPopupContent('<strong>' + s.station + '</strong><br>' + (s.name || '') + '<br>CFT: ' + s.cft.toFixed(1));
      }
    });
  }

  // --- Event List Update (SSE) ---
  function addEventToList(evt) {
    var list = document.getElementById('eventList');
    if (!list) return;
    var mag = (evt.mag || 0).toFixed(1);
    var magClass = evt.mag >= 6 ? 'mag-crit' : evt.mag >= 5 ? 'mag-high' : evt.mag >= 4 ? 'mag-mid' : 'mag-low';
    var row = document.createElement('div');
    row.className = 'event-row';
    row.innerHTML = '<span class="mag ' + magClass + '">M' + mag + '</span>' +
      '<span class="place">' + (evt.place || '?') + '</span>' +
      '<span class="time">just now</span>';
    list.insertBefore(row, list.firstChild);
  }

  // --- Seismogram Canvas ---
  function drawSeismogram() {
    var canvas = document.getElementById('seismogramCanvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var w = canvas.width = canvas.offsetWidth;
    var h = canvas.height = canvas.offsetHeight;

    ctx.fillStyle = '#0a0c10';
    ctx.fillRect(0, 0, w, h);

    var stationIds = Object.keys(seismogramData);
    if (stationIds.length === 0) return;

    var trackH = h / stationIds.length;
    stationIds.forEach(function (sid, idx) {
      var d = seismogramData[sid];
      if (!d || !d.data || d.data.length < 2) return;

      var y0 = idx * trackH;
      var mid = y0 + trackH / 2;

      // Station label
      ctx.fillStyle = '#555';
      ctx.font = '10px monospace';
      ctx.fillText(sid.split('.').slice(0, 2).join('.'), 4, y0 + 12);

      // Center line
      ctx.strokeStyle = '#222';
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();

      // Waveform
      var data = d.data;
      var maxAmp = Math.max.apply(null, data.map(Math.abs)) || 1;
      var step = w / data.length;
      ctx.strokeStyle = '#44aaff';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (var i = 0; i < data.length; i++) {
        var x = i * step;
        var y = mid - (data[i] / maxAmp) * (trackH / 2 - 4);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.stroke();

      // Separator
      if (idx < stationIds.length - 1) {
        ctx.strokeStyle = '#1e2230';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(0, y0 + trackH);
        ctx.lineTo(w, y0 + trackH);
        ctx.stroke();
      }
    });
  }

  // --- Audio ---
  function playAlarmSound(level) {
    try {
      var ctx = getAudioCtx();
      if (ctx.state === 'suspended') ctx.resume();
      if (level === 'critical' || level === 'high') {
        playSiren(ctx, 3);
      } else {
        playBeep(ctx, 550, 0.3, 3);
      }
    } catch (e) {}
  }

  function playSiren(ctx, durationS) {
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    gain.gain.value = 0.3;
    osc.type = 'sawtooth';
    var now = ctx.currentTime;
    for (var i = 0; i < durationS * 2; i++) {
      osc.frequency.setValueAtTime(440, now + i * 0.5);
      osc.frequency.linearRampToValueAtTime(880, now + i * 0.5 + 0.25);
      osc.frequency.linearRampToValueAtTime(440, now + i * 0.5 + 0.5);
    }
    osc.start(now);
    osc.stop(now + durationS);
  }

  function playBeep(ctx, freq, duration, count) {
    for (var i = 0; i < count; i++) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = freq;
      gain.gain.value = 0.2;
      var t = ctx.currentTime + i * (duration + 0.15);
      osc.start(t);
      osc.stop(t + duration);
    }
  }

  // --- Notifications ---
  function sendNotification(alarmData) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    var title = window.I18N.notif_title
      .replace('{mag}', (alarmData.event_mag || '?'))
      .replace('{place}', alarmData.event_place || '');
    var body = '';
    if (alarmData.reason_key) {
      body = formatI18N(alarmData.reason_key, alarmData.reason_params || {});
    }
    new Notification(title, {
      body: body,
      icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">\uD83C\uDF0D</text></svg>',
      requireInteraction: true,
    });
  }

  // --- Tsunami ---
  function handleTsunami(d) {
    var banner = document.getElementById('tsunamiBanner');
    if (d.level === 'none' || !d.level) {
      banner.classList.remove('active');
      return;
    }
    banner.classList.add('active');
    banner.textContent = (d.severity || 'TSUNAMI') + ': ' + (d.title || '');
    if (notifEnabled && d.caracas_threat) {
      new Notification('TSUNAMI \u2014 CARACAS', {
        body: window.I18N.tsunami_notif,
        requireInteraction: true,
      });
    }
  }

  // --- Notification permission request on first interaction ---
  document.addEventListener('click', function requestNotif() {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().then(function (p) {
        if (p === 'granted') notifEnabled = true;
      });
    } else if ('Notification' in window && Notification.permission === 'granted') {
      notifEnabled = true;
    }
    document.removeEventListener('click', requestNotif);
  }, { once: true });

})();
