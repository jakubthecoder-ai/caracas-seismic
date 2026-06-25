# Bare-bone deployment (no Docker) behind Apache

How to run the Caracas Seismic Monitor on a plain Linux host **without Docker**,
served publicly over HTTPS behind Apache, protected with HTTP Basic Auth, and
hardened (DDoS rate-limiting, brute-force bans, security headers).

This is an alternative to `docker-compose up` for hosts that already run Apache
(e.g. sharing port 80/443 with another site) and have no Docker.

```
Browser ──HTTPS:8443 (Basic Auth)──▶ Apache ──proxy /ws──▶ 127.0.0.1:8768 (python backend)
                                       │
                                       └── serves static frontend from web root
```

Replace these placeholders throughout:

| Placeholder        | Meaning                                  |
|--------------------|------------------------------------------|
| `YOUR_DOMAIN`      | hostname with a valid TLS cert           |
| `YOUR_IP`          | server public IP                         |
| `WEBUSER`          | basic-auth username                      |
| `APP_DIR`          | where this repo is checked out           |

---

## 1. Backend — venv + systemd

No Docker: run `main.py` under a Python virtualenv as a systemd service.

```bash
sudo apt-get install -y python3-venv python3-pip
cd APP_DIR
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`/etc/systemd/system/caracas-seismic.service`:

```ini
[Unit]
Description=Caracas Seismic Monitor (WebSocket backend)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=APP_DIR
Environment=PYTHONUNBUFFERED=1
Environment=SEISMIC_DB_PATH=/var/lib/caracas-seismic/seismic_monitor.db
ExecStart=APP_DIR/.venv/bin/python -u main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/lib/caracas-seismic
sudo systemctl daemon-reload
sudo systemctl enable --now caracas-seismic.service
```

The WebSocket server listens on `:8768`. It is **not** exposed publicly — the
firewall (step 7) only opens 8080/8443, and Apache proxies to `127.0.0.1:8768`.

> The default `SEISMIC_DB_PATH` in the code is `/data/...` (Docker volume); the
> service overrides it to `/var/lib/caracas-seismic/...` for bare-metal.

---

## 2. Frontend — static files in a web root

Apache (running as `www-data`) cannot read files under `/root`, so copy the
served HTML into a web root it can read:

```bash
sudo mkdir -p /var/www/caracas-seismic
sudo cp APP_DIR/index.html APP_DIR/caracas_monitor.html /var/www/caracas-seismic/
sudo chown -R www-data:www-data /var/www/caracas-seismic
```

Re-run this copy after every `git pull` that touches the HTML.

---

## 3. Basic Auth (htpasswd)

```bash
# bcrypt; you will be prompted for the password
sudo htpasswd -cB /etc/apache2/.htpasswd-caracas WEBUSER
sudo chown root:www-data /etc/apache2/.htpasswd-caracas
sudo chmod 640 /etc/apache2/.htpasswd-caracas
```

> Never commit the htpasswd file or the password to the repo.

---

## 4. Apache — HTTPS vhost (`:8443`)

Enable the modules used below:

```bash
sudo a2enmod ssl proxy_http proxy_wstunnel headers substitute
```

`/etc/apache2/sites-available/caracas-seismic-ssl.conf`:

```apache
Listen 8443

<VirtualHost *:8443>
    ServerName YOUR_DOMAIN
    DocumentRoot /var/www/caracas-seismic
    DirectoryIndex index.html

    SSLEngine on
    SSLCertificateFile    /etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem
    Include /etc/letsencrypt/options-ssl-apache.conf

    # Always serve fresh HTML (no stale frontend after a deploy)
    <FilesMatch "\.html$">
        Header set Cache-Control "no-cache, no-store, must-revalidate"
        Header set Pragma "no-cache"
        Header set Expires "0"
    </FilesMatch>

    # --- Serving-layer patches (so the app source stays untouched) ---
    AddOutputFilterByType SUBSTITUTE text/html
    # (a) The frontend hardcodes ws://localhost:8768 — rewrite it to the
    #     same-origin wss endpoint proxied below.
    Substitute "s|ws://localhost:8768|wss://YOUR_DOMAIN:8443/ws|n"
    # (b) Make triggerAlarm() crash-proof: browser autoplay policy can make the
    #     audio alarm throw on an un-clicked tab, which would abort the live-event
    #     render. Rename the body to _safeAlarm and wrap it in try/catch.
    Substitute "s|function triggerAlarm(level, mag, place) {|function triggerAlarm(){try{return _safeAlarm.apply(this,arguments)}catch(_e){console.warn('[alarm] skipped:',_e)}}function _safeAlarm(level, mag, place) {|n"

    # CSP — XSS hardening. 'unsafe-inline' is required (inline scripts in the app);
    # external script/style locked to unpkg (Leaflet), connections to known origins.
    Header always set Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' 'unsafe-inline' https://unpkg.com; img-src 'self' data: https:; connect-src 'self' https://earthquake.usgs.gov wss://www.seismicportal.eu wss://YOUR_DOMAIN:8443; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"

    # WebSocket reverse proxy (wss -> local backend)
    ProxyPreserveHost On
    ProxyPass        /ws  ws://127.0.0.1:8768/
    ProxyPassReverse /ws  ws://127.0.0.1:8768/

    # Basic Auth on EVERYTHING (static pages + WebSocket handshake)
    <Location />
        AuthType Basic
        AuthName "Caracas Seismic Monitor"
        AuthUserFile /etc/apache2/.htpasswd-caracas
        Require valid-user
    </Location>
    <Directory /var/www/caracas-seismic>
        Options FollowSymLinks
        AllowOverride None
        Require valid-user
    </Directory>

    ErrorLog  ${APACHE_LOG_DIR}/caracas-ssl-error.log
    CustomLog ${APACHE_LOG_DIR}/caracas-ssl-access.log combined
</VirtualHost>
```

> **No public TLS cert / no spare subdomain?** Reuse any valid Let's Encrypt cert
> already on the box (point `ServerName` at the hostname that cert matches, and
> access the monitor on `:8443` under that hostname). The browser only validates
> the hostname, not the port.

---

## 5. Apache — force HTTPS (`:8080` redirect)

So the Basic-Auth password is never sent over plaintext HTTP.

`/etc/apache2/sites-available/caracas-seismic.conf`:

```apache
Listen 8080

<VirtualHost *:8080>
    ServerName YOUR_DOMAIN
    ServerAlias YOUR_IP
    Redirect permanent / https://YOUR_DOMAIN:8443/

    ErrorLog  ${APACHE_LOG_DIR}/caracas-error.log
    CustomLog ${APACHE_LOG_DIR}/caracas-access.log combined
</VirtualHost>
```

---

## 6. Hardening (server-wide)

`/etc/apache2/conf-available/zz-security-hardening.conf` — the `zz-` prefix makes
it load last so its `ServerTokens` wins over the distro default:

```apache
ServerTokens Prod
ServerSignature Off
TraceEnable Off

<IfModule mod_headers.c>
    Header always set X-Content-Type-Options "nosniff"
    Header always set X-Frame-Options "SAMEORIGIN"
    Header always set Referrer-Policy "strict-origin-when-cross-origin"
    Header always set Permissions-Policy "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
    Header always set Strict-Transport-Security "max-age=31536000"
</IfModule>

# Anti-DDoS / rate limiting
<IfModule mod_evasive20.c>
    DOSHashTableSize 3097
    DOSPageCount 20
    DOSPageInterval 2
    DOSSiteCount 150
    DOSSiteInterval 2
    DOSBlockingPeriod 30
    DOSLogDir "/var/log/mod_evasive"
    DOSWhitelist 127.0.0.1
    DOSWhitelist YOUR_IP
</IfModule>
```

```bash
sudo apt-get install -y libapache2-mod-evasive
sudo mkdir -p /var/log/mod_evasive && sudo chown www-data:www-data /var/log/mod_evasive
sudo a2enconf zz-security-hardening
```

**fail2ban** — ban basic-auth brute force and known bad bots.
`/etc/fail2ban/jail.d/caracas-apache.local`:

```ini
[apache-auth]
enabled  = true
port     = http,https,8080,8443
filter   = apache-auth
logpath  = /var/log/apache2/*error.log
maxretry = 5
findtime = 600
bantime  = 3600

[apache-badbots]
enabled  = true
port     = http,https,8080,8443
filter   = apache-badbots
logpath  = /var/log/apache2/*access.log
maxretry = 2
bantime  = 86400
```

```bash
sudo apt-get install -y fail2ban
sudo systemctl restart fail2ban
```

---

## 7. Firewall

```bash
sudo ufw allow 8080/tcp
sudo ufw allow 8443/tcp
```

Port 8768 (the backend) is intentionally **not** opened — only Apache (localhost)
reaches it.

---

## 8. Enable everything & test

```bash
sudo a2ensite caracas-seismic caracas-seismic-ssl
sudo apache2ctl configtest && sudo systemctl reload apache2

# expect: 401 without auth, 200 with auth, 101 on the WS handshake
curl -s -o /dev/null -w "%{http_code}\n" https://YOUR_DOMAIN:8443/
curl -s -o /dev/null -w "%{http_code}\n" -u WEBUSER:PASS https://YOUR_DOMAIN:8443/
curl -s -o /dev/null -w "%{http_code}\n" -u WEBUSER:PASS \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: x3JJHMbDL1EzLkh9GBhXDw==" -H "Sec-WebSocket-Version: 13" \
  https://YOUR_DOMAIN:8443/ws
```

Open `https://YOUR_DOMAIN:8443/` and log in with `WEBUSER`.

---

## Updating after `git pull`

```bash
cd APP_DIR && git pull
sudo cp index.html caracas_monitor.html /var/www/caracas-seismic/
sudo systemctl restart caracas-seismic.service
sudo systemctl reload apache2
```

The two `Substitute` rules keep working automatically — no code changes needed.

## Notes

- **Why `mod_substitute`?** The app source is kept unmodified; the two runtime
  fixes (WS URL, crash-proof alarm) are applied at the serving layer so `git pull`
  never conflicts.
- **Audio alarm** still needs one user click to start (browser autoplay policy);
  the *visual* event render no longer depends on it.
