/**
 * Seismic Monitor — Minimal SSE Client
 *
 * Handles: SSE connection, alarm banner updates, sound/notifications,
 * details panel toggle with lazy Leaflet loading, seismogram rendering.
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
  let seismogramData = {};  // station -> {data, sps}

  // Audio context (lazy init on user gesture)
  let audioCtx = null;
  function getAudioCtx() {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    return audioCtx;
  }

  // --- SSE Connection ---
  const sse = new EventSource('/sse');

  sse.addEventListener('alarm', function (e) {
    const d = JSON.parse(e.data);
    updateBanner(d.level, d.reason);
    updateMiniSeismogram(d);
    if (soundEnabled && (d.level === 'warning' || d.level === 'critical')) {
      playAlarmSound(d.level);
    }
    if (notifEnabled && (d.level === 'warning' || d.level === 'critical')) {
      sendNotification(d);
    }
  });

  sse.addEventListener('event', function (e) {
    const d = JSON.parse(e.data);
    updateLastEvent(d.event);
    if (detailsVisible) addEventToList(d.event);
  });

  sse.addEventListener('waveform', function (e) {
    if (!detailsVisible) return;
    const d = JSON.parse(e.data);
    seismogramData[d.station] = d;
    drawSeismogram();
  });

  sse.addEventListener('status', function (e) {
    const d = JSON.parse(e.data);
    updateConnectionStatus(true, d);
    if (detailsVisible) updateStationMarkers(d.stations || []);
  });

  sse.addEventListener('tsunami', function (e) {
    const d = JSON.parse(e.data);
    handleTsunami(d);
  });

  sse.addEventListener('history', function (e) {
    const d = JSON.parse(e.data);
    if (d.events && d.events.length > 0) {
      updateLastEvent(d.events[d.events.length - 1]);
    }
  });

  sse.onopen = function () {
    updateConnectionStatus(true);
  };

  sse.onerror = function () {
    updateConnectionStatus(false);
  };

  // --- Banner ---
  function updateBanner(level, reason) {
    const banner = document.getElementById('banner');
    banner.className = 'threat-' + (level || 'none');
    if (reason) {
      banner.textContent = reason;
    } else if (level === 'none' || level === 'info' || level === 'low') {
      banner.textContent = window.I18N.no_threat;
    }
  }

  // --- Mini Seismogram (compact view, SVG) ---
  function updateMiniSeismogram(alarmData) {
    const container = document.getElementById('miniSeismogram');
    if (!alarmData || alarmData.level === 'none') {
      container.classList.remove('active');
      return;
    }
    // Show mini seismogram during active alarm
    if (alarmData.waveform_snippet && alarmData.waveform_snippet.length > 0) {
      container.classList.add('active');
      const svg = document.getElementById('miniSvg');
      const data = alarmData.waveform_snippet;
      const w = 600, h = 70, mid = h / 2;
      const maxAmp = Math.max(...data.map(Math.abs)) || 1;
      const step = w / data.length;
      let path = 'M0,' + mid;
      for (let i = 0; i < data.length; i++) {
        const y = mid - (data[i] / maxAmp) * (mid - 4);
        path += ' L' + (i * step).toFixed(1) + ',' + y.toFixed(1);
      }
      svg.innerHTML = '<path d="' + path + '" stroke="#44aaff" stroke-width="1" fill="none" opacity="0.8"/>' +
        '<line x1="0" y1="' + mid + '" x2="' + w + '" y2="' + mid + '" stroke="#333" stroke-width="0.5"/>';
    }
  }

  // --- Last Event ---
  function updateLastEvent(evt) {
    if (!evt) return;
    const el = document.getElementById('lastEvent');
    const mag = (evt.mag || 0).toFixed(1);
    const place = evt.place || '?';
    el.innerHTML = '<strong>' + window.I18N.last_event + ':</strong> M' + mag + ' — ' + place;
  }

  // --- Connection Status ---
  function updateConnectionStatus(connected, statusData) {
    const el = document.getElementById('connStatus');
    const dot = document.getElementById('connDot');
    const sseDot = document.getElementById('sseDot');
    if (connected) {
      dot.className = 'dot dot-ok';
      sseDot.style.background = 'var(--green)';
      el.innerHTML = '<span class="dot dot-ok" id="connDot"></span> ' + window.I18N.connected;
    } else {
      dot.className = 'dot dot-err';
      sseDot.style.background = 'var(--red)';
      el.innerHTML = '<span class="dot dot-err" id="connDot"></span> ' + window.I18N.offline;
    }
  }

  // --- Details Toggle ---
  window.toggleDetails = async function () {
    const details = document.getElementById('details');
    const btn = document.getElementById('toggleBtn');
    if (!detailsVisible) {
      btn.textContent = window.I18N.hide_details + ' \u25B2';
      btn.disabled = true;
      try {
        const resp = await fetch('/details?lang=' + window.LANG);
        details.innerHTML = await resp.text();
        details.classList.add('open');

        // Lazy-load Leaflet
        if (!window.L) {
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
      btn.textContent = window.I18N.show_details + ' \u25BC';
    }
  };

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  // --- Map ---
  function initMap() {
    const mapEl = document.getElementById('detailMap');
    if (!mapEl || !window.L) return;

    map = L.map('detailMap').setView([10.5, -66.9], 6);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OSM &copy; CARTO',
      maxZoom: 15,
    }).addTo(map);

    // Caracas marker
    L.circleMarker([10.5, -66.9], {
      radius: 8, color: '#ff2222', fillColor: '#ff2222', fillOpacity: 0.3, weight: 2,
    }).addTo(map).bindPopup('Caracas');

    // Load station markers from data attributes
    const stationEl = document.getElementById('stationData');
    if (stationEl) {
      const count = parseInt(stationEl.dataset.stationCount) || 0;
      for (let i = 1; i <= count; i++) {
        try {
          const s = JSON.parse(stationEl.dataset['station' + i]);
          if (s.lat && s.lon) {
            const marker = L.circleMarker([s.lat, s.lon], {
              radius: 6, color: '#22cc44', fillColor: '#22cc44', fillOpacity: 0.5, weight: 2,
            }).addTo(map).bindPopup(s.station + '<br>CFT: ' + (s.cft || 0).toFixed(1));
            stationMarkers[s.station] = marker;
          }
        } catch (e) {}
      }
    }

    // Load event markers
    const eventEl = document.getElementById('eventData');
    if (eventEl && eventEl.dataset.events) {
      try {
        const events = JSON.parse(eventEl.dataset.events);
        events.forEach(function (evt) {
          if (evt.lat && evt.lon) {
            addEventMarker(evt);
          }
        });
      } catch (e) {}
    }

    // Invalidate size after render
    setTimeout(function () { map.invalidateSize(); }, 100);
  }

  function addEventMarker(evt) {
    if (!map || !window.L) return;
    const mag = evt.mag || 0;
    const radius = Math.max(4, Math.min(mag * 3, 20));
    const color = mag >= 6 ? '#ff2222' : mag >= 5 ? '#ff8800' : mag >= 4 ? '#ffcc00' : '#22cc44';
    const marker = L.circleMarker([evt.lat, evt.lon], {
      radius: radius, color: color, fillColor: color, fillOpacity: 0.4, weight: 2,
    }).addTo(map).bindPopup(
      'M' + mag.toFixed(1) + '<br>' + (evt.place || '') +
      '<br>' + (evt.depth ? 'Depth: ' + evt.depth.toFixed(0) + ' km' : '')
    );
    eventMarkers.push(marker);
  }

  // --- Station Markers Update (from SSE status) ---
  function updateStationMarkers(stations) {
    if (!map || !window.L) return;
    stations.forEach(function (s) {
      const marker = stationMarkers[s.station];
      if (marker) {
        const color = s.cft > 3.5 ? '#ff2222' : s.cft > 2.5 ? '#ff8800' : s.cft > 1.5 ? '#ffcc00' : '#22cc44';
        marker.setStyle({ color: color, fillColor: color });
        marker.setPopupContent(s.station + '<br>CFT: ' + s.cft.toFixed(1));
      }
    });
  }

  // --- Event List Update (SSE) ---
  function addEventToList(evt) {
    const list = document.getElementById('eventList');
    if (!list) return;
    const mag = (evt.mag || 0).toFixed(1);
    const magClass = evt.mag >= 6 ? 'mag-crit' : evt.mag >= 5 ? 'mag-high' : evt.mag >= 4 ? 'mag-mid' : 'mag-low';
    const row = document.createElement('div');
    row.className = 'event-row';
    row.innerHTML = '<span class="mag ' + magClass + '">M' + mag + '</span>' +
      '<span class="place">' + (evt.place || '?') + '</span>' +
      '<span class="time">just now</span>';
    list.insertBefore(row, list.firstChild);

    // Also add to map
    if (evt.lat && evt.lon) addEventMarker(evt);
  }

  // --- Seismogram Canvas ---
  function drawSeismogram() {
    const canvas = document.getElementById('seismogramCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width = canvas.offsetWidth;
    const h = canvas.height = canvas.offsetHeight;

    ctx.fillStyle = '#0a0c10';
    ctx.fillRect(0, 0, w, h);

    const stationIds = Object.keys(seismogramData);
    if (stationIds.length === 0) return;

    const trackH = h / stationIds.length;
    stationIds.forEach(function (sid, idx) {
      const d = seismogramData[sid];
      if (!d || !d.data || d.data.length < 2) return;

      const y0 = idx * trackH;
      const mid = y0 + trackH / 2;

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
      const data = d.data;
      const maxAmp = Math.max(...data.map(Math.abs)) || 1;
      const step = w / data.length;
      ctx.strokeStyle = '#44aaff';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i < data.length; i++) {
        const x = i * step;
        const y = mid - (data[i] / maxAmp) * (trackH / 2 - 4);
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
      const ctx = getAudioCtx();
      if (ctx.state === 'suspended') ctx.resume();
      if (level === 'critical') {
        playSiren(ctx, 3);
      } else {
        playBeep(ctx, 550, 0.3, 3);
      }
    } catch (e) {}
  }

  function playSiren(ctx, durationS) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    gain.gain.value = 0.3;
    osc.type = 'sawtooth';
    const now = ctx.currentTime;
    for (let i = 0; i < durationS * 2; i++) {
      osc.frequency.setValueAtTime(440, now + i * 0.5);
      osc.frequency.linearRampToValueAtTime(880, now + i * 0.5 + 0.25);
      osc.frequency.linearRampToValueAtTime(440, now + i * 0.5 + 0.5);
    }
    osc.start(now);
    osc.stop(now + durationS);
  }

  function playBeep(ctx, freq, duration, count) {
    for (let i = 0; i < count; i++) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = freq;
      gain.gain.value = 0.2;
      const t = ctx.currentTime + i * (duration + 0.15);
      osc.start(t);
      osc.stop(t + duration);
    }
  }

  // --- Notifications ---
  function sendNotification(alarmData) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const title = window.I18N.notif_title
      .replace('{mag}', (alarmData.event_mag || '?'))
      .replace('{place}', alarmData.event_place || '');
    new Notification(title, {
      body: alarmData.reason || '',
      icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🌍</text></svg>',
      requireInteraction: true,
    });
  }

  // --- Tsunami ---
  function handleTsunami(d) {
    const banner = document.getElementById('tsunamiBanner');
    if (d.level === 'none' || !d.level) {
      banner.classList.remove('active');
      return;
    }
    banner.classList.add('active');
    banner.textContent = (d.severity || 'TSUNAMI') + ': ' + (d.title || '');
    if (notifEnabled && d.caracas_threat) {
      new Notification('TSUNAMI — CARACAS', {
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
    } else if (Notification.permission === 'granted') {
      notifEnabled = true;
    }
    document.removeEventListener('click', requestNotif);
  }, { once: true });

})();
