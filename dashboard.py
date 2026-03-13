import json

def get_login_html(error: str = "") -> str:
    error_block = ""
    if error:
        error_block = f'<div class="error">{error}</div>'

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AnyRouter Bridge - Login</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#0f0f1a;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:#e0e0e0;
}}
.card{{
  background:#1a1a2e;border-radius:16px;padding:48px 40px;width:380px;
  box-shadow:0 8px 32px rgba(0,0,0,0.4);
}}
.logo{{text-align:center;margin-bottom:32px}}
.logo svg{{width:48px;height:48px;fill:#6c63ff}}
.logo h1{{font-size:18px;margin-top:12px;color:#fff;font-weight:600}}
.logo p{{font-size:13px;color:#888;margin-top:4px}}
label{{display:block;font-size:13px;color:#aaa;margin-bottom:6px}}
input[type=password]{{
  width:100%;padding:12px 16px;border:1px solid #2a2a3e;border-radius:8px;
  background:#12121f;color:#fff;font-size:15px;outline:none;
  transition:border-color .2s;
}}
input[type=password]:focus{{border-color:#6c63ff}}
button{{
  width:100%;padding:12px;margin-top:20px;border:none;border-radius:8px;
  background:#6c63ff;color:#fff;font-size:15px;font-weight:600;cursor:pointer;
  transition:background .2s;
}}
button:hover{{background:#5a52e0}}
.error{{
  background:#3d1f1f;border:1px solid #6b2f2f;color:#ff6b6b;
  padding:10px 14px;border-radius:8px;margin-bottom:16px;font-size:13px;
}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <svg viewBox="0 0 24 24"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 2.18l7 3.12v4.7c0 4.83-3.4 9.36-7 10.5-3.6-1.14-7-5.67-7-10.5V6.3l7-3.12z"/><path d="M11 7h2v6h-2zm0 8h2v2h-2z"/></svg>
    <h1>AnyRouter Bridge</h1>
    <p>Model Status Dashboard</p>
  </div>
  {error_block}
  <form method="POST" action="/dashboard/login">
    <label for="password">Password</label>
    <input type="password" id="password" name="password" placeholder="Enter password" autofocus required>
    <button type="submit">Login</button>
  </form>
</div>
</body>
</html>'''


def get_dashboard_html(models: list[str]) -> str:
    models_json = json.dumps(models)

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AnyRouter Bridge - Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f0f1a;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
header{{
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 24px;background:#1a1a2e;border-bottom:1px solid #2a2a3e;
}}
header h1{{font-size:18px;color:#fff;font-weight:600}}
header .actions{{display:flex;gap:12px;align-items:center}}
.btn{{
  padding:8px 16px;border:none;border-radius:6px;font-size:13px;font-weight:600;
  cursor:pointer;transition:all .2s;
}}
.btn-primary{{background:#6c63ff;color:#fff}}
.btn-primary:hover{{background:#5a52e0}}
.btn-primary:disabled{{background:#3a3a5e;color:#888;cursor:not-allowed}}
.btn-outline{{background:transparent;border:1px solid #3a3a5e;color:#aaa}}
.btn-outline:hover{{border-color:#6c63ff;color:#fff}}
.btn-sm{{padding:6px 12px;font-size:12px}}
main{{max-width:1200px;margin:0 auto;padding:24px}}
.stats{{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:12px;margin-bottom:24px;
}}
.stat{{
  background:#1a1a2e;border-radius:10px;padding:16px;text-align:center;
  border:1px solid #2a2a3e;
}}
.stat .num{{font-size:28px;font-weight:700;color:#fff}}
.stat .label{{font-size:12px;color:#888;margin-top:4px}}
.stat.ok .num{{color:#4ade80}}
.stat.err .num{{color:#ff6b6b}}
.stat.limit .num{{color:#fbbf24}}
.toolbar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}}
.toolbar h2{{font-size:16px;color:#fff}}
.grid{{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));
  gap:16px;
}}
.card{{
  background:#1a1a2e;border:1px solid #2a2a3e;border-radius:12px;
  padding:20px;transition:border-color .2s;position:relative;overflow:hidden;
}}
.card:hover{{border-color:#3a3a5e}}
.card-head{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px}}
.model-name{{font-size:14px;font-family:"SF Mono",Monaco,Consolas,monospace;color:#fff;word-break:break-all}}
.badge{{
  display:inline-block;padding:3px 8px;border-radius:4px;font-size:11px;
  font-weight:700;text-transform:uppercase;white-space:nowrap;flex-shrink:0;margin-left:8px;
}}
.badge-ok{{background:#1a3a2a;color:#4ade80}}
.badge-error{{background:#3d1f1f;color:#ff6b6b}}
.badge-rate_limited{{background:#3d2e0f;color:#fbbf24}}
.badge-untested{{background:#2a2a3e;color:#888}}
.badge-testing{{background:#1f2a3d;color:#60a5fa;animation:pulse 1s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.5}}}}
.card-body{{font-size:13px;color:#aaa;min-height:40px}}
.card-body .row{{display:flex;justify-content:space-between;margin-bottom:4px}}
.card-body .row .val{{color:#fff}}
.card-body .err-msg{{color:#ff6b6b;margin-top:6px;font-size:12px;word-break:break-all}}
.card-body .preview{{color:#4ade80;margin-top:6px;font-size:12px;font-style:italic}}
.card-foot{{margin-top:12px;display:flex;justify-content:flex-end}}
.global-loading{{
  position:fixed;top:0;left:0;right:0;height:3px;background:#6c63ff;
  animation:loading 1.5s ease-in-out infinite;display:none;z-index:999;
}}
@keyframes loading{{0%{{transform:translateX(-100%)}}50%{{transform:translateX(0)}}100%{{transform:translateX(100%)}}}}
</style>
</head>
<body>
<div class="global-loading" id="globalLoading"></div>
<header>
  <h1>AnyRouter Bridge Dashboard</h1>
  <div class="actions">
    <input type="password" id="apiToken" placeholder="API Key for testing" style="
      padding:7px 12px;border:1px solid #3a3a5e;border-radius:6px;
      background:#12121f;color:#fff;font-size:13px;width:220px;outline:none;
    ">
    <button class="btn btn-primary" id="btnTestAll" onclick="testAll()">Test All Models</button>
    <form method="POST" action="/dashboard/logout" style="margin:0">
      <button type="submit" class="btn btn-outline">Logout</button>
    </form>
  </div>
</header>
<main>
  <div class="stats">
    <div class="stat" id="statTotal"><div class="num" id="numTotal">0</div><div class="label">Total</div></div>
    <div class="stat ok" id="statOk"><div class="num" id="numOk">0</div><div class="label">OK</div></div>
    <div class="stat err" id="statErr"><div class="num" id="numErr">0</div><div class="label">Error</div></div>
    <div class="stat limit" id="statLimit"><div class="num" id="numLimit">0</div><div class="label">Rate Limited</div></div>
  </div>
  <div class="toolbar">
    <h2>Models</h2>
    <span id="lastUpdate" style="font-size:12px;color:#666"></span>
  </div>
  <div class="grid" id="grid"></div>
</main>
<script>
const MODELS={models_json};
let results={{}};

function badge(status){{
  const labels={{ok:"OK",error:"ERROR",rate_limited:"RATE LIMITED",untested:"UNTESTED",testing:"TESTING"}};
  return `<span class="badge badge-${{status}}">${{labels[status]||status}}</span>`;
}}

function renderCard(m){{
  const r=results[m]||{{status:"untested"}};
  let body='';
  if(r.latency_ms!=null) body+=`<div class="row"><span>Latency</span><span class="val">${{r.latency_ms}} ms</span></div>`;
  if(r.tested_at) body+=`<div class="row"><span>Tested</span><span class="val">${{new Date(r.tested_at).toLocaleString()}}</span></div>`;
  if(r.error_message) body+=`<div class="err-msg">${{r.error_message}}</div>`;
  if(r.response_preview) body+=`<div class="preview">"${{r.response_preview}}"</div>`;
  if(!body) body='<div style="color:#666">Not tested yet</div>';
  return `<div class="card" id="card-${{m}}">
    <div class="card-head"><span class="model-name">${{m}}</span>${{badge(r.status)}}</div>
    <div class="card-body">${{body}}</div>
    <div class="card-foot"><button class="btn btn-outline btn-sm" onclick="testOne('${{m}}')" ${{r.status==='testing'?'disabled':''}}>Test</button></div>
  </div>`;
}}

function renderAll(){{
  document.getElementById('grid').innerHTML=MODELS.map(renderCard).join('');
  updateStats();
}}

function updateStats(){{
  const vals=Object.values(results);
  document.getElementById('numTotal').textContent=MODELS.length;
  document.getElementById('numOk').textContent=vals.filter(r=>r.status==='ok').length;
  document.getElementById('numErr').textContent=vals.filter(r=>r.status==='error').length;
  document.getElementById('numLimit').textContent=vals.filter(r=>r.status==='rate_limited').length;
}}

function getToken(){{return document.getElementById('apiToken').value.trim();}}

async function api(url,opts={{}}){{
  const token=getToken();
  const headers=opts.headers||{{}};
  if(token) headers['x-api-key']=token;
  const resp=await fetch(url,{{method:'POST',...opts,headers}});
  if(resp.status===401){{window.location='/dashboard/login';return null;}}
  return resp.json();
}}

async function testOne(model){{
  if(!getToken()){{alert('Please enter an API Key first');return;}}
  results[model]={{...results[model],status:'testing'}};
  renderAll();
  const data=await api(`/api/test/${{encodeURIComponent(model)}}`);
  if(data){{results[model]=data;renderAll();}}
}}

async function testAll(){{
  if(!getToken()){{alert('Please enter an API Key first');return;}}
  const btn=document.getElementById('btnTestAll');
  const bar=document.getElementById('globalLoading');
  btn.disabled=true;btn.textContent='Testing...';bar.style.display='block';
  MODELS.forEach(m=>{{results[m]={{...results[m],status:'testing'}}}});
  renderAll();
  const data=await api('/api/test-all');
  if(data){{Object.assign(results,data);renderAll();}}
  btn.disabled=false;btn.textContent='Test All Models';bar.style.display='none';
  document.getElementById('lastUpdate').textContent='Last update: '+new Date().toLocaleString();
}}

(async()=>{{
  const resp=await fetch('/api/model-status');
  if(resp.status===401){{window.location='/dashboard/login';return;}}
  const data=await resp.json();
  if(data){{Object.assign(results,data);renderAll();}}
  else{{renderAll();}}
}})();
</script>
</body>
</html>'''
