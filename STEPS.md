# Flensburg - How to Build and Deploy

This guide walks through every step needed to take the Astraea framework and
turn it into a working German traffic law Q&A service. It assumes you already
have Python 3.11+, Qdrant, and a running LLM server (e.g. llama-server with
Qwen3-8B) on the same machine.

---

## Overview

The service has three layers:

```
Browser --> Web server (Nginx / Cloudflare) --> Uvicorn (port 8004)
                                                    |
                                         Qdrant (legislation + cases)
                                         LLM server (port 8080)
```

- **Uvicorn** runs the Python FastAPI app on localhost only.
- **Qdrant** holds the embedded law text that the app retrieves from.
- **LLM server** generates the final German-language answer.
- **Web server** sits in front and handles HTTPS, domain routing, and
  public access. You can use Cloudflare Tunnel (no open port needed) or a
  standard Nginx reverse proxy (requires a public IP or VPS).

---

## Step 1 - Create the repo and copy Astraea

Create a new GitHub repo (e.g. `flensburg`), clone it locally, then copy
the Astraea framework into it as a starting point.

```bash
cd ~/proj/priv
git clone git@github.com:yourname/flensburg.git
cd flensburg

# Copy framework folders from your Astraea installation
cp -r ~/proj/priv/astraea/core .
cp -r ~/proj/priv/astraea/apps .
cp -r ~/proj/priv/astraea/schemas .
cp -r ~/proj/priv/astraea/scripts .
cp -r ~/proj/priv/astraea/ingest .
cp -r ~/proj/priv/astraea/tests .
cp    ~/proj/priv/astraea/pyproject.toml .
cp    ~/proj/priv/astraea/conftest.py .
cp    ~/proj/priv/astraea/Dockerfile .
```

The `core/` folder is the Astraea engine. You generally do not modify it
(except for one small fix in Step 5).

---

## Step 2 - Create the jurisdiction folder

A "jurisdiction" in Astraea is a self-contained folder that defines how a
specific legal domain works: what law it covers, how to route questions to
the right statutes, and what the AI should say.

Create this folder structure:

```
jurisdictions/
  flensburg/
    __init__.py       exports the jurisdiction object
    jurisdiction.py   defines the corpus, routes, and data sources
    prompt.py         the German system prompt for the AI
    routes.py         keyword rules that map questions to statutes
    static/
      index.html      the frontend web page
      app.js          frontend JavaScript
      style.css       copy from Astraea
      favicon.svg     copy from Astraea
      astraea/
        astraea.js    copy from Astraea
```

### jurisdiction.py - tell Astraea where the data lives

```python
corpus = CorpusConfig(
    qdrant_collection="flensburg",   # Qdrant collection for court decisions
    courts=["BGH", "OLG", "MANUAL"],
    leg_collection="de_legal",       # Qdrant collection for statute text
    pg_database="de_legal",
)

leg_sources = [
    LegislationSource(id="StVO",  label="Strassenverkehrs-Ordnung"),
    LegislationSource(id="StVG",  label="Strassenverkehrsgesetz"),
    LegislationSource(id="OWiG",  label="Gesetz ueber Ordnungswidrigkeiten"),
    LegislationSource(id="FeV",   label="Fahrerlaubnis-Verordnung"),
    LegislationSource(id="BKatV", label="Bussgeldkatalog-Verordnung"),
]

# No live scraping needed - statutes are pre-ingested into Qdrant
def legislation(self):   return None
def web_verify(self):    return None
```

### routes.py - map question keywords to statutes

Each route says: "if the user's question contains any of these words, always
pull these specific statute sections into the answer." For example:

```python
StatuteRoute(
    intent="geschwindigkeit",
    include_any=["geschwindigkeit", "geblitzt", "km/h", "zu schnell", ...],
    forced_sections=[
        "DELEG/StVO/3",        # speed rules
        "DELEG/BKatV/anlage",  # fine table
        "DELEG/BKatV/4",       # driving ban thresholds
    ],
    synthetic_query="Geschwindigkeitsueberschreitung Bussgeld Punkte Fahrverbot",
)
```

The statute section IDs follow the format `DELEG/{LAW}/{section}` - for
example `DELEG/StVO/3` or `DELEG/BKatV/anlage`.

The full routes cover: Geschwindigkeit, Punkte/Flensburg, Fahrverbot, MPU,
Fahrerlaubnis, Einspruch gegen Bussgeld, Alkohol/Drogen, Unfall, Parken,
and Handy am Steuer.

### apps/flensburg_app.py - the entry point

```python
from jurisdictions.flensburg import jurisdiction
from core.api import create_app

app = create_app(jurisdiction)
```

---

## Step 3 - Ingest German federal legislation into Qdrant

The ingestion script fetches each statute section from gesetze-im-internet.de,
splits the text into chunks of about 150 words, converts them to vectors, and
stores them in Qdrant. This only needs to run once (the script skips sections
that are already in the database on subsequent runs).

```bash
cd ~/proj/priv/flensburg
python ingest/run_de_legislation.py
```

This ingests seven laws: StVO, StVG, OWiG, FeV, BKatV, StGB (traffic
sections only), and PflVG.

To check what has been ingested:

```bash
python ingest/run_de_legislation.py --list
```

To ingest only specific laws:

```bash
python ingest/run_de_legislation.py --laws StVO BKatV
```

### Things that are easy to get wrong

**BKatV URL** - the correct URL slug is `/bkatv_2013/`, not `/bkatv/`. Using
the wrong one returns a 404 and nothing gets ingested for that law.

**BKatV Anlage** - the actual fine table (the document listing specific euro
amounts and point penalties) lives in `anlage.html`, not in numbered section
pages. The section link scraper must match `anlage*.html` and `anhang*.html`
patterns, not just `__N.html` ones.

**StGB** has 358 sections, but only four of them are relevant to traffic law
(Unfallflucht, Gefaehrdung des Strassenverkehrs, Trunkenheit). The `LAWS`
dict uses a `sections_only` list to fetch just those four pages and skip the
rest, saving time and avoiding irrelevant data in the corpus.

---

## Step 4 - Create the Qdrant collections

The ingestion script creates the `de_legal` collection automatically if it
does not exist. The `flensburg` collection (for BGH/OLG court decisions, which
can be added later) must be created manually:

```bash
curl -X PUT http://localhost:6333/collections/flensburg \
  -H 'Content-Type: application/json' \
  -d '{"vectors": {"size": 768, "distance": "Cosine"}}'
```

The size 768 matches the nomic-embed-text-v1.5 model used for embeddings.
If you use a different embedding model, change the size to match its output
dimension.

---

## Step 5 - Fix the framework for legislation-only operation

Astraea was originally built assuming court decisions always exist. When the
`flensburg` collection is empty (no court cases ingested yet), the pipeline
would fail before it could use the statute text.

In `core/api.py`, find the early-exit guard and add the `anchor_vstore`
condition:

```python
# Before (fails when no court cases exist):
if not context_texts:

# After (falls through to statute-only generation):
if not context_texts and not anchor_vstore:
```

`anchor_vstore` is the list of statute chunks retrieved via `forced_sections`.
When it is non-empty, the AI can generate an answer from statute alone, even
with no court decisions in the corpus.

---

## Step 6 - Set up the application service

On Linux with systemd, create a user service so the app starts automatically
and restarts if it crashes.

Create `~/.config/systemd/user/flensburg.service`:

```ini
[Unit]
Description=Flensburg - Deutsches Verkehrsrecht Q&A
After=network.target qdrant.service
Wants=qdrant.service

[Service]
WorkingDirectory=/home/youruser/proj/priv/flensburg
Environment=PUBLIC_TOKEN=your-secret-token-here
Environment=DEBUG_KEY=your-debug-key-here
Environment=ALLOWED_ORIGIN=https://yourdomain.example.com
Environment=ENABLE_RERANKER=false
ExecStart=/usr/bin/uvicorn apps.flensburg_app:app --host 127.0.0.1 --port 8004
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Then enable and start it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now flensburg
systemctl --user status flensburg
```

The app listens on `127.0.0.1:8004` only - it is not directly exposed to the
internet. The web server in the next step forwards traffic to it.

**Generate your own tokens:**

```bash
# PUBLIC_TOKEN - what users send in X-API-Key header
python -c "import secrets; print(secrets.token_urlsafe(24))"

# DEBUG_KEY - to unlock debug mode in the browser UI
python -c "import secrets; print(secrets.token_urlsafe(9))"
```

---

## Step 7 - Expose the service publicly

Choose one of the two options below depending on your setup.

---

### Option A - Cloudflare Tunnel (no open ports, no VPS required)

Cloudflare Tunnel lets you serve the app from your home machine or laptop
without opening any firewall ports or having a static IP. Traffic goes:

```
Browser --> Cloudflare edge --> encrypted tunnel --> your machine --> app
```

**Install cloudflared and authenticate:**

```bash
# Gentoo / Arch
emerge net-vpn/cloudflared   # or download the binary from Cloudflare

cloudflared tunnel login      # opens browser to authorize
cloudflared tunnel create flensburg
```

**Add flensburg to `~/.cloudflared/config.yml`:**

```yaml
tunnel: your-tunnel-id
credentials-file: /home/youruser/.cloudflared/your-tunnel-id.json

ingress:
  - hostname: flensburg.yourdomain.com
    service: http://localhost:8004
  - service: http_status:404
```

**Create the DNS record** - in the Cloudflare dashboard for your domain, add:

| Type  | Name        | Target                                   | Proxy |
|-------|-------------|------------------------------------------|-------|
| CNAME | flensburg   | your-tunnel-id.cfargotunnel.com          | on    |

**Start the tunnel as a service:**

```bash
systemctl --user enable --now cloudflared
```

DNS propagation usually takes under a minute with Cloudflare's proxy enabled.
If the browser shows "Error 1016", the CNAME record has not been created yet.

---

### Option B - Nginx reverse proxy (VPS or dedicated server)

If you already have a server with a public IP and a domain pointing at it,
use Nginx as a reverse proxy. This is the standard approach on any VPS
(Hetzner, DigitalOcean, AWS Lightsail, Contabo, etc.).

**Install Nginx and Certbot:**

```bash
# Debian / Ubuntu
apt install nginx certbot python3-certbot-nginx

# Arch
pacman -S nginx certbot certbot-nginx
```

**Create `/etc/nginx/sites-available/flensburg`:**

```nginx
server {
    listen 80;
    server_name flensburg.yourdomain.com;

    # Certbot will add the HTTPS redirect automatically after step below
    location / {
        proxy_pass         http://127.0.0.1:8004;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # Required for SSE (streaming responses)
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 120s;
        chunked_transfer_encoding on;
    }
}
```

Enable it and get a free HTTPS certificate:

```bash
ln -s /etc/nginx/sites-available/flensburg /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

certbot --nginx -d flensburg.yourdomain.com
```

Certbot will modify your Nginx config to redirect HTTP to HTTPS and install
a certificate that auto-renews every 90 days.

**Set your DNS A record** at your domain registrar or DNS provider:

| Type | Name       | Value            |
|------|------------|------------------|
| A    | flensburg  | your.server.ip   |

> The `proxy_buffering off` and `proxy_read_timeout` lines are important.
> The app streams responses as Server-Sent Events (SSE). Without these
> settings, Nginx will buffer the stream and the user will see no output
> until the entire answer is complete, which defeats the streaming UX.

---

## Step 8 - Test the API

Authentication uses the `X-API-Key` header. The endpoint is `/ask/stream`
(streaming SSE). Add `X-No-Log: 1` during testing to avoid saving test
questions to the live log.

```bash
curl -sN https://flensburg.yourdomain.com/ask/stream \
  -H "X-API-Key: your-secret-token-here" \
  -H "X-No-Log: 1" \
  -H "Content-Type: application/json" \
  -d '{"question": "Ich wurde mit 25 km/h zu schnell geblitzt. Welches Bussgeld bekomme ich?"}'
```

A working response streams several `data:` lines ending with `event: done`.
If you get a 401, the token is wrong. If you get `event: error`, check the
app logs: `journalctl --user -u flensburg -f`.

---

## Current status

| Component               | Status                                             |
|-------------------------|----------------------------------------------------|
| Legislation (de_legal)  | Ingested: StVO, StVG, OWiG, FeV, BKatV, StGB (4 sections), PflVG |
| Case decisions (flensburg) | Collection exists, no documents yet           |
| LLM generator           | Qwen3-8B-Q5_K_M via llama-server on port 8080     |
| Reranker                | Disabled (can be enabled once case corpus exists)  |

The service answers from statute text alone until BGH/OLG case decisions
are ingested into the `flensburg` Qdrant collection. This already works well
for most common traffic law questions.
