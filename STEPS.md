# Flensburg - Build Steps

Documents how to go from zero to a live Astraea jurisdiction for German
federal traffic law (Verkehrsrecht / Fahrerlaubnisrecht).

---

## 1. Create the repo and copy Astraea as base

```bash
# Clone the empty GitHub repo
cd ~/proj/priv
git clone git@github.com:jwongso/flensburg.git
cd flensburg

# Copy Astraea framework files into it
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

The `core/` folder contains the Astraea engine: `api.py`, `routing.py`,
`embedder.py`, `retriever.py`, and supporting modules. Do not modify it
unless fixing a framework bug (see Step 4).

---

## 2. Create the flensburg jurisdiction

```
jurisdictions/
  flensburg/
    __init__.py      - exports `jurisdiction`
    jurisdiction.py  - AstraeaJurisdiction subclass
    prompt.py        - German system prompt
    routes.py        - StatuteRoute definitions (10 routes)
    static/
      index.html
      app.js
      style.css      - copied from Astraea static/
      favicon.svg    - copied from Astraea static/
      astraea/
        astraea.js   - copied from Astraea static/astraea/
```

### jurisdiction.py

Key settings:

```python
corpus = CorpusConfig(
    qdrant_collection="flensburg",   # case decisions (empty initially)
    courts=["BGH", "OLG", "MANUAL"],
    leg_collection="de_legal",       # pre-ingested legislation
    pg_database="de_legal",
)

leg_sources = [
    LegislationSource(id="StVO",  label="Strassenverkehrs-Ordnung"),
    LegislationSource(id="StVG",  label="Strassenverkehrsgesetz"),
    LegislationSource(id="OWiG",  label="Gesetz ueber Ordnungswidrigkeiten"),
    LegislationSource(id="FeV",   label="Fahrerlaubnis-Verordnung"),
    LegislationSource(id="BKatV", label="Bussgeldkatalog-Verordnung"),
]

def legislation(self):   return None   # pre-ingested, no Playwright needed
def web_verify(self):    return None
```

### routes.py

10 StatuteRoute entries covering:

| intent            | primary forced_sections                         |
|-------------------|-------------------------------------------------|
| geschwindigkeit   | StVO/3, BKatV/anlage, BKatV/4                  |
| punkte_flensburg  | StVG/4, StVG/29, StVG/4a                       |
| fahrverbot        | StVG/25, BKatV/4, BKatV/3                      |
| mpu               | FeV/11, FeV/13, FeV/13a, FeV/46                |
| fahrerlaubnis     | StVG/2, StVG/3, FeV/6, FeV/20                  |
| einspruch_bussgeld| OWiG/67, OWiG/66, OWiG/69, OWiG/31            |
| alkohol_drogen    | StVG/24a, StVG/24c, StGB/316, StGB/315c, FeV/13, FeV/13a |
| unfall            | StVO/34, StGB/142, StVG/7, PflVG/1             |
| parken            | StVO/12, StVO/13, StVO/15a                     |
| handy_steuer      | StVO/23, BKatV/anlage                          |

Legislation chunk IDs follow the pattern `DELEG/{ACT}/{section}` (e.g.
`DELEG/StVO/3`, `DELEG/BKatV/anlage`). The `_is_leg_chunk()` helper in
`core/` identifies these by checking whether "LEG" appears in the first
segment of the case_id.

### apps/flensburg_app.py

```python
from jurisdictions.flensburg import jurisdiction
from core.api import create_app

app = create_app(jurisdiction)
```

---

## 3. Ingest German federal legislation

All seven laws are scraped from gesetze-im-internet.de section by section,
chunked into ~150-word windows, embedded with nomic-embed-text-v1.5 (768-dim),
and upserted into the `de_legal` Qdrant collection.

```bash
cd ~/proj/priv/flensburg
python ingest/run_de_legislation.py
```

Laws ingested: StVO, StVG, OWiG, FeV, BKatV, StGB (4 traffic sections only),
PflVG.

### Notes on tricky parts

**BKatV URL** - the slug is `/bkatv_2013/`, not `/bkatv/`. The 2013 slug is
what gesetze-im-internet.de uses.

**BKatV Anlage** - the fine table lives in `anlage.html`, not in numbered
section pages. The section link regex must match `anlage*.html` and
`anhang*.html` in addition to `__N.html`:

```python
r"(__[\w]+\.html|BJNR[\w]+\.html|anlage[\w]*\.html|anhang[\w]*\.html)"
```

**StGB selective ingestion** - StGB has 358 sections. Only the four
traffic-relevant criminal sections are needed. Use `sections_only` in the
LAWS dict to skip the index crawl and fetch those pages directly:

```python
"StGB_Verkehr": {
    "act_id": "StGB",   # canonical act ID for DELEG/StGB/... case_ids
    "sections_only": ["__142.html", "__315b.html", "__315c.html", "__316.html"],
    ...
}
```

**Idempotency** - the script skips sections whose `case_id` already exists in
the collection, so it is safe to re-run after a partial failure.

To verify what was ingested:

```bash
python ingest/run_de_legislation.py --list
```

---

## 4. Fix api.py for legislation-only operation

By default Astraea raises an error if the case corpus returns no chunks,
because it was designed assuming case decisions always exist. With an empty
`flensburg` collection this blocked all responses.

In `core/api.py` change the early-exit guard so the pipeline falls through
to generation when legislation context is available:

```python
# Before:
if not context_texts:

# After:
if not context_texts and not anchor_vstore:
```

`anchor_vstore` holds the legislation chunks retrieved via forced_sections.
When it is non-empty, generation proceeds using statute alone.

---

## 5. Create the Qdrant collection for case decisions

The `flensburg` collection (for BGH/OLG decisions, currently empty) must
exist before the service starts, or Qdrant will error on first query.

```bash
curl -X PUT http://localhost:6333/collections/flensburg \
  -H 'Content-Type: application/json' \
  -d '{"vectors": {"size": 768, "distance": "Cosine"}}'
```

The `de_legal` collection is created automatically by the ingestion script
if it does not exist.

---

## 6. Set up the systemd user service

Create `~/.config/systemd/user/flensburg.service`:

```ini
[Unit]
Description=Flensburg - Deutsches Verkehrsrecht Q&A
After=network.target qdrant.service
Wants=qdrant.service

[Service]
WorkingDirectory=/home/wdha/proj/priv/flensburg
Environment=PUBLIC_TOKEN=eUulgmpISwrFhbwHqHhGkVXQk9poYgW9
Environment=DEBUG_KEY=UXbYpE5yxzmA
Environment=ALLOWED_ORIGIN=https://flensburg.localrun.ai
Environment=ENABLE_RERANKER=false
ExecStart=/usr/bin/uvicorn apps.flensburg_app:app --host 127.0.0.1 --port 8004
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Port 8004 is the flensburg-specific port (8000=nz-legal, 8001=nz-tenancy,
8003=buildingconsents).

```bash
systemctl --user daemon-reload
systemctl --user enable --now flensburg
systemctl --user status flensburg
```

---

## 7. Add flensburg to the Cloudflare tunnel

The single tunnel `f7d1f35e-5f6f-4fb0-b4b8-beeb21e2836d` routes all
localrun.ai subdomains. Add the flensburg entry to `~/.cloudflared/config.yml`:

```yaml
ingress:
  - hostname: flensburg.localrun.ai
    service: http://localhost:8004
  # ... other services ...
  - service: http_status:404
```

Restart the tunnel:

```bash
systemctl --user restart cloudflared
```

---

## 8. Create the Cloudflare DNS CNAME (manual step in dashboard)

In the Cloudflare dashboard for localrun.ai, add:

| Type  | Name       | Target                                                  | Proxy |
|-------|------------|---------------------------------------------------------|-------|
| CNAME | flensburg  | f7d1f35e-5f6f-4fb0-b4b8-beeb21e2836d.cfargotunnel.com | on    |

Without this record the tunnel config has no effect and the browser shows
Error 1016 (origin DNS error).

---

## 9. Test the API

Authentication uses `X-API-Key` (not `Authorization: Bearer`). The endpoint
is `/ask/stream` (SSE streaming). Use `X-No-Log` to avoid polluting the live
question log during testing.

```bash
curl -sN https://flensburg.localrun.ai/ask/stream \
  -H "X-API-Key: eUulgmpISwrFhbwHqHhGkVXQk9poYgW9" \
  -H "X-No-Log: 1" \
  -H "Content-Type: application/json" \
  -d '{"question": "Ich wurde mit 25 km/h zu schnell geblitzt. Welches Bussgeld bekomme ich?"}'
```

A successful response streams SSE events ending with `event: done`.

---

## What is live vs. what is still empty

| Component        | Status                                      |
|------------------|---------------------------------------------|
| Legislation (de_legal) | Ingested: StVO, StVG, OWiG, FeV, BKatV, StGB (4 sections), PflVG |
| Case decisions (flensburg) | Collection exists, no documents yet |
| LLM generator    | Qwen3-8B-Q5_K_M on llama-server port 8080  |
| Reranker         | Disabled (ENABLE_RERANKER=false)            |

The service answers from statute alone until BGH/OLG case decisions are
ingested.
