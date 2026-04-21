#!/usr/bin/env python3
"""
build_vp.py — Static Virtual Platform builder
==============================================
Part of the StaticFDP ecosystem (https://github.com/StaticFDP/staticfdp-vp).

Reads registered-indexes/*.yaml, fetches each FDP Index's index.ttl,
and writes a merged federation graph:
  docs/vp/federation.ttl    — full DCAT federation graph (RDF Turtle)
  docs/vp/federation.jsonld — same as JSON-LD
  docs/index.html           — HTML search + browse UI

Environment variables:
  GITHUB_TOKEN              — fine-grained PAT (optional, for GitHub API calls)
  FORGEJO_TOKEN             — Codeberg token (optional)
  INFRASTRUCTURE_OVERRIDE   — github | codeberg | both (overrides config file)
"""

import os
import sys
import json
import re
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

# ── Minimal YAML reader ────────────────────────────────────────────────────────

def _load_yaml(path):
    result = {}
    current = result
    parent_key = None
    try:
        with open(path) as f:
            for raw in f:
                line = raw.rstrip()
                if not line or line.lstrip().startswith('#'):
                    continue
                indent = len(line) - len(line.lstrip())
                key_val = line.strip()
                if ':' not in key_val:
                    continue
                k, _, v = key_val.partition(':')
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if indent == 0:
                    if not v:
                        parent_key = k
                        current = result.setdefault(k, {})
                    else:
                        result[k] = v
                        current = result
                        parent_key = None
                else:
                    if parent_key is None:
                        result[k] = v
                    else:
                        current[k] = v
    except FileNotFoundError:
        pass
    return result

_cfg_data = {}

def load_config():
    global _cfg_data
    p = Path(__file__).resolve().parent.parent / 'vp-config.yaml'
    _cfg_data = _load_yaml(str(p))

def cfg(*keys, default=''):
    node = _cfg_data
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, {})
    return node if node != {} else default

# ── Registered indexes ────────────────────────────────────────────────────────

def load_registered_indexes():
    base = Path(__file__).resolve().parent.parent / 'registered-indexes'
    indexes = []
    for path in sorted(base.glob('*.yaml')):
        data = _load_yaml(str(path))
        if data.get('index_url'):
            indexes.append(data)
    return indexes

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_url(url, timeout=30):
    try:
        req = urllib.request.Request(
            url,
            headers={'Accept': 'text/turtle, application/ld+json, */*'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f'  ⚠ fetch failed: {e}', file=sys.stderr)
        return None

def extract_datasets_from_turtle(turtle_text):
    """Very basic extraction of dcat:Dataset subjects from Turtle."""
    datasets = []
    for line in turtle_text.splitlines():
        # Match lines like: :slug a dcat:Dataset ;
        m = re.match(r'\s*(<[^>]+>|:\S+)\s+a\s+dcat:Dataset', line)
        if m:
            datasets.append(m.group(1))
    return datasets

# ── RDF generation ────────────────────────────────────────────────────────────

def build_federation_ttl(indexes, base_url, today):
    lines = [
        '@prefix dcat:    <https://www.w3.org/ns/dcat#> .',
        '@prefix dcterms: <http://purl.org/dc/terms/> .',
        '@prefix foaf:    <http://xmlns.com/foaf/0.1/> .',
        '@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .',
        '@prefix fdp:     <https://w3id.org/fdp/fdp-o#> .',
        f'@prefix :        <{base_url}/vp/> .',
        '',
        f'# StaticFDP Virtual Platform — generated {today}',
        '',
        '# ── Virtual Platform root ────────────────────────────────────────────────────',
        '',
        f'<{base_url}/vp/>',
        '    a                    fdp:MetadataService ;',
        f'    dcterms:title        "{cfg("virtual_platform","title","My Virtual Platform")}"@en ;',
        '    dcterms:description  "A StaticFDP Virtual Platform. Edit vp-config.yaml to customise."@en ;',
        f'    dcterms:issued       "{today}"^^xsd:date ;',
        f'    dcterms:modified     "{today}"^^xsd:date ;',
        f'    dcterms:license      <{cfg("virtual_platform","license","https://creativecommons.org/licenses/by/4.0/")}> ;',
        '    fdp:hasCatalog       :federation-catalog .',
        '',
        '# ── Federation catalog ───────────────────────────────────────────────────────',
        '',
        ':federation-catalog a dcat:Catalog ;',
        '    dcterms:title       "Federated FDP Indexes"@en ;',
        '    dcterms:description "All FDP Indexes aggregated by this Virtual Platform."@en ;',
        f'    dcterms:modified    "{today}"^^xsd:date ;',
    ]
    if indexes:
        refs = ' , '.join(f':index-{i}' for i in range(len(indexes)))
        lines.append(f'    dcat:dataset        {refs} ;')
    lines.append('    dcterms:publisher   :publisher .')
    lines.append('')
    pub_name = cfg('virtual_platform', 'publisher_name', 'My Organisation')
    pub_url  = cfg('virtual_platform', 'publisher_url',  'https://example.org/')
    lines += [
        ':publisher a foaf:Organization ;',
        f'    foaf:name  "{pub_name}" ;',
        f'    foaf:page  <{pub_url}> .',
        '',
        '# ── Registered FDP Indexes ───────────────────────────────────────────────────',
        '',
    ]
    for i, idx in enumerate(indexes):
        slug  = f'index-{i}'
        title = idx.get('title', f'FDP Index {i}').replace('"', '\\"')
        url   = idx.get('index_url', '')
        land  = idx.get('landing_page', url)
        lines += [
            f':{slug} a dcat:Dataset, fdp:MetadataService ;',
            f'    dcterms:title       "{title}"@en ;',
            f'    dcterms:modified    "{today}"^^xsd:date ;',
            f'    dcat:landingPage    <{land}> ;',
            f'    dcat:distribution   :{slug}-dist .',
            '',
            f':{slug}-dist a dcat:Distribution ;',
            f'    dcterms:format      "text/turtle" ;',
            f'    dcat:downloadURL    <{url}> .',
            '',
        ]
    return '\n'.join(lines)

def build_federation_jsonld(indexes, base_url, today):
    datasets = []
    for idx in indexes:
        datasets.append({
            "@type": "DataCatalog",
            "name": idx.get('title', 'Untitled FDP Index'),
            "url": idx.get('landing_page', idx.get('index_url', '')),
        })
    doc = {
        "@context": "https://schema.org/",
        "@type": "DataCatalog",
        "name": cfg('virtual_platform', 'title', 'My Virtual Platform'),
        "url": f"{base_url}/vp/",
        "dataset": datasets,
        "dateModified": today,
    }
    return json.dumps(doc, indent=2)

def build_vp_html(indexes, base_url, today):
    cards = ''
    for idx in indexes:
        name     = idx.get('title', 'Untitled FDP Index')
        land     = idx.get('landing_page', idx.get('index_url', '#'))
        idx_url  = idx.get('index_url', '')
        desc     = idx.get('description', '')
        fdp_count = idx.get('_fdp_count', '?')
        cards += f"""
      <div class="card">
        <h3>{name}</h3>
        <p>{desc}</p>
        <p style="font-size:12px;color:#9ca3af;margin-top:.4rem;">{fdp_count} FDPs registered in this index</p>
        <div style="margin-top:.8rem;display:flex;gap:.5rem;flex-wrap:wrap;">
          <a href="{land}" style="font-size:12px;font-weight:600;color:#7c3aed;border:1px solid #7c3aed;padding:3px 10px;border-radius:5px;text-decoration:none;">Visit Index →</a>
          <a href="{idx_url}" style="font-size:12px;font-weight:600;color:#6b7280;border:1px solid #d1d5db;padding:3px 10px;border-radius:5px;text-decoration:none;">index.ttl</a>
        </div>
      </div>"""

    fed_ttl_url    = f"{base_url}/vp/federation.ttl"
    fed_jsonld_url = f"{base_url}/vp/federation.jsonld"
    count = len(indexes)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Virtual Platform</title>
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org/",
    "@type": "DataCatalog",
    "name": "{cfg('virtual_platform','title','My Virtual Platform')}",
    "url": "{base_url}/vp/",
    "dateModified": "{today}"
  }}
  </script>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;font-size:16px;line-height:1.6;color:#1a1a2e;background:#f8fafc}}
    a{{color:#7c3aed}}
    .container{{max-width:860px;margin:0 auto;padding:0 24px}}
    header{{background:linear-gradient(135deg,#4c1d95 0%,#7c3aed 60%,#1a6b5a 100%);color:#fff;padding:44px 24px 36px;text-align:center}}
    header h1{{font-size:clamp(24px,4vw,40px);font-weight:800;letter-spacing:-.02em;margin-bottom:8px}}
    header p{{font-size:clamp(14px,2vw,17px);opacity:.85;max-width:560px;margin:0 auto 16px}}
    .badge{{display:inline-block;background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.35);border-radius:20px;padding:3px 14px;font-size:12px;letter-spacing:.05em;text-transform:uppercase;margin-bottom:12px}}
    section{{padding:36px 0}}
    section+section{{border-top:1px solid #e5e7eb}}
    h2{{font-size:20px;font-weight:700;margin-bottom:8px}}
    .card-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem;margin-top:1rem}}
    .card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:1.2rem 1.4rem}}
    .card h3{{font-size:15px;margin-bottom:4px}}
    .card p{{font-size:14px;color:#6b7280}}
    code{{font-family:monospace;font-size:13px;background:#f1f5f9;padding:2px 6px;border-radius:4px}}
    pre{{background:#1e293b;color:#e2e8f0;padding:1rem 1.2rem;border-radius:8px;font-size:13px;overflow-x:auto;margin-top:.6rem}}
    footer{{background:#1a1a2e;color:rgba(255,255,255,.5);padding:20px;text-align:center;font-size:13px}}
    footer a{{color:rgba(255,255,255,.7)}}
  </style>
</head>
<body>
<header>
  <div class="container">
    <div class="badge">Virtual Platform</div>
    <h1>{cfg('virtual_platform','title','My Virtual Platform')}</h1>
    <p>{count} FDP Index{'es' if count != 1 else ''} aggregated &nbsp;·&nbsp; Updated {today}</p>
    <div style="margin-top:12px;display:flex;gap:.6rem;justify-content:center;flex-wrap:wrap">
      <a href="{fed_ttl_url}" style="font-size:13px;font-weight:600;color:#fff;border:1px solid rgba(255,255,255,.5);padding:4px 14px;border-radius:6px;text-decoration:none;">federation.ttl (RDF)</a>
      <a href="{fed_jsonld_url}" style="font-size:13px;font-weight:600;color:#fff;border:1px solid rgba(255,255,255,.5);padding:4px 14px;border-radius:6px;text-decoration:none;">federation.jsonld</a>
    </div>
  </div>
</header>
<main class="container">
  <section>
    <h2>Aggregated FDP Indexes</h2>
    <p>These FDP Indexes are harvested daily. Each index contains multiple registered FAIR Data Points.</p>
    <div class="card-grid">
      {cards if cards else '<p style="color:#9ca3af;margin-top:.8rem;">No indexes registered yet. <a href="https://github.com/StaticFDP/staticfdp-vp/issues/new?template=register-index.yml">Register yours →</a></p>'}
    </div>
  </section>
  <section>
    <h2>Machine-readable federation graph</h2>
    <pre>curl {fed_ttl_url}
# or
curl {fed_jsonld_url}</pre>
    <pre style="margin-top:.6rem"># Python
from rdflib import Graph
g = Graph()
g.parse("{fed_ttl_url}")
for s, p, o in g:
    print(s, p, o)</pre>
  </section>
  <section>
    <h2>Register an FDP Index</h2>
    <p>Open a <a href="https://github.com/StaticFDP/staticfdp-vp/issues/new?template=register-index.yml">Register FDP Index issue</a> with your index URL and it will be included in the next aggregation run.</p>
  </section>
</main>
<footer>
  StaticFDP Ecosystem &nbsp;·&nbsp;
  <a href="https://github.com/StaticFDP/staticfdp-vp">GitHub</a> &nbsp;·&nbsp;
  <a href="https://codeberg.org/StaticFDP/staticfdp-vp">Codeberg</a>
</footer>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_config()
    base_url = cfg('virtual_platform', 'base_url', 'https://OWNER.github.io/staticfdp-vp')
    today    = str(date.today())
    timeout  = int(cfg('aggregate', 'timeout_seconds', 30))

    print(f'StaticFDP VP builder — {today}')
    print(f'Base URL: {base_url}')

    indexes = load_registered_indexes()
    print(f'Registered FDP Indexes: {len(indexes)}')

    # Fetch each index
    fetched = []
    for idx in indexes:
        url = idx.get('index_url', '')
        print(f'  → {idx.get("title","?")}  {url}')
        text = fetch_url(url, timeout=timeout)
        if text and 'dcat:Catalog' in text:
            datasets = extract_datasets_from_turtle(text)
            idx['_fdp_count'] = len(datasets)
            print(f'    ✓ {len(datasets)} FDPs found in index')
            fetched.append(idx)
        else:
            print(f'    ✗ unreachable or not a DCAT catalog')
            if cfg('aggregate', 'soft_fail', 'true').lower() in ('true', '1', 'yes'):
                idx['_fdp_count'] = '?'
                fetched.append(idx)

    out_dir = Path(__file__).resolve().parent.parent / 'docs' / 'vp'
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / 'federation.ttl').write_text(build_federation_ttl(fetched, base_url, today))
    print('Wrote docs/vp/federation.ttl')

    (out_dir / 'federation.jsonld').write_text(build_federation_jsonld(fetched, base_url, today))
    print('Wrote docs/vp/federation.jsonld')

    docs_dir = Path(__file__).resolve().parent.parent / 'docs'
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / 'index.html').write_text(build_vp_html(fetched, base_url, today))
    print('Wrote docs/index.html')

    print(f'Done — {len(fetched)} FDP Indexes in federation.')

if __name__ == '__main__':
    main()
