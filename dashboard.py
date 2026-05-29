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
  background:#09090e;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:#e2e8f0;
  overflow:hidden;
  position:relative;
}}
body::before{{
  content:"";position:absolute;width:600px;height:600px;border-radius:50%;
  background:radial-gradient(circle, rgba(99, 102, 241, 0.15) 0%, transparent 70%);
  top:-200px;left:-200px;z-index:-1;
}}
body::after{{
  content:"";position:absolute;width:600px;height:600px;border-radius:50%;
  background:radial-gradient(circle, rgba(139, 92, 246, 0.1) 0%, transparent 70%);
  bottom:-200px;right:-200px;z-index:-1;
}}
.card{{
  background:rgba(18, 18, 29, 0.65);
  backdrop-filter:blur(16px);
  border:1px solid rgba(255, 255, 255, 0.05);
  border-radius:20px;padding:48px 40px;width:400px;
  box-shadow:0 20px 50px rgba(0,0,0,0.5);
  transition:all .3s;
}}
.logo{{text-align:center;margin-bottom:36px}}
.logo svg{{width:56px;height:56px;fill:#6366f1;filter:drop-shadow(0 0 10px rgba(99, 102, 241, 0.6));animation:spin 12s linear infinite}}
@keyframes spin{{100%{{transform:rotate(360deg)}}}}
.logo h1{{font-size:22px;margin-top:16px;color:#fff;font-weight:700;letter-spacing:1px;background:linear-gradient(135deg, #a5b4fc 0%, #6366f1 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.logo p{{font-size:13px;color:#64748b;margin-top:6px}}
label{{display:block;font-size:12px;color:#94a3b8;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px}}
input[type=password]{{
  width:100%;padding:14px 18px;border:1px solid rgba(255,255,255,0.08);border-radius:10px;
  background:rgba(9, 9, 15, 0.6);color:#fff;font-size:15px;outline:none;
  transition:all .2s;
  font-family:monospace;
}}
input[type=password]:focus{{border-color:#6366f1;box-shadow:0 0 12px rgba(99, 102, 241, 0.25)}}
button{{
  width:100%;padding:14px;margin-top:24px;border:none;border-radius:10px;
  background:linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);color:#fff;font-size:15px;font-weight:600;cursor:pointer;
  box-shadow:0 4px 15px rgba(99, 102, 241, 0.35);
  transition:all .2s;
}}
button:hover{{background:linear-gradient(135deg, #818cf8 0%, #6366f1 100%);transform:translateY(-1px)}}
.error{{
  background:rgba(239, 68, 68, 0.1);border:1px solid rgba(239, 68, 68, 0.2);color:#fca5a5;
  padding:12px 16px;border-radius:10px;margin-bottom:20px;font-size:13px;
}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <svg viewBox="0 0 24 24"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 2.18l7 3.12v4.7c0 4.83-3.4 9.36-7 10.5-3.6-1.14-7-5.67-7-10.5V6.3l7-3.12z"/><path d="M11 7h2v6h-2zm0 8h2v2h-2z"/></svg>
    <h1>AnyRouter Bridge</h1>
    <p>Model Status Audit Panel</p>
  </div>
  {error_block}
  <form method="POST" action="/dashboard/login">
    <label for="password">Password Key</label>
    <input type="password" id="password" name="password" placeholder="••••••••••••••••" autofocus required>
    <button type="submit">Unlock System</button>
  </form>
</div>
</body>
</html>'''


def get_dashboard_html(models: list[str]) -> str:
    # 动态测试时默认清空列表，所以初始传入给模板的 models_json 传空列表
    models_json = "[]"

    return f'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AnyRouter Bridge - Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{
  background:#09090e;color:#cbd5e1;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  min-height:100vh;
  position:relative;
  overflow-x:hidden;
}}
body::before{{
  content:"";position:absolute;width:800px;height:800px;border-radius:50%;
  background:radial-gradient(circle, rgba(99, 102, 241, 0.08) 0%, transparent 70%);
  top:-300px;right:-100px;z-index:-2;
}}
body::after{{
  content:"";position:absolute;width:800px;height:800px;border-radius:50%;
  background:radial-gradient(circle, rgba(139, 92, 246, 0.05) 0%, transparent 70%);
  bottom:-300px;left:-100px;z-index:-2;
}}

/* Header Styling */
header{{
  display:flex;align-items:center;justify-content:space-between;
  padding:20px 32px;background:rgba(15, 15, 27, 0.5);
  backdrop-filter:blur(20px);
  border-bottom:1px solid rgba(255, 255, 255, 0.04);
  position:sticky;top:0;z-index:100;
}}
.title-area{{display:flex;align-items:center;gap:12px}}
.title-area svg{{width:28px;height:28px;fill:#6366f1;filter:drop-shadow(0 0 6px rgba(99, 102, 241, 0.5))}}
.title-area h1{{
  font-size:20px;color:#fff;font-weight:700;letter-spacing:0.5px;
  background:linear-gradient(135deg, #fff 30%, #a5b4fc 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}}
.title-area span{{
  background:rgba(99, 102, 241, 0.1);border:1px solid rgba(99, 102, 241, 0.2);
  color:#818cf8;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;
  letter-spacing:0.5px;
}}
header .actions{{display:flex;gap:16px;align-items:center}}

/* Form Control UX */
textarea#apiToken{{
  padding:10px 14px;border:1px solid rgba(255,255,255,0.06);border-radius:10px;
  background:rgba(9, 9, 15, 0.7);color:#fff;font-size:12px;width:380px;height:45px;
  outline:none;resize:vertical;font-family:"Fira Code",monospace;line-height:1.4;
  transition:all .2s;
}}
textarea#apiToken:focus{{border-color:#6366f1;box-shadow:0 0 10px rgba(99, 102, 241, 0.25)}}

/* Premium Buttons */
.btn{{
  padding:10px 20px;border:none;border-radius:10px;font-size:13px;font-weight:600;
  cursor:pointer;transition:all .25s cubic-bezier(0.4, 0, 0.2, 1);
  display:inline-flex;align-items:center;justify-content:center;gap:8px;
}}
.btn-primary{{
  background:linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
  color:#fff;box-shadow:0 4px 15px rgba(99, 102, 241, 0.25);
}}
.btn-primary:hover{{
  background:linear-gradient(135deg, #818cf8 0%, #6366f1 100%);
  transform:translateY(-1px);box-shadow:0 6px 20px rgba(99, 102, 241, 0.35);
}}
.btn-primary:disabled{{
  background:#1e1e2f;color:#475569;box-shadow:none;cursor:not-allowed;
}}
.btn-outline{{
  background:transparent;border:1px solid rgba(255,255,255,0.08);color:#94a3b8;
}}
.btn-outline:hover{{
  border-color:#6366f1;color:#fff;background:rgba(99,102,241,0.05);
}}
.btn-sm{{padding:6px 12px;font-size:12px;border-radius:6px}}

/* Layout */
main{{max-width:1400px;margin:0 auto;padding:32px}}
.stats{{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:16px;margin-bottom:32px;
}}
.stat{{
  background:rgba(18, 18, 29, 0.4);border-radius:14px;padding:20px;text-align:center;
  border:1px solid rgba(255, 255, 255, 0.04);
  transition:all .3s;
}}
.stat:hover{{border-color:rgba(255,255,255,0.08);background:rgba(18, 18, 29, 0.65)}}
.stat .num{{font-size:32px;font-weight:800;color:#fff;letter-spacing:-0.5px}}
.stat .label{{font-size:12px;color:#64748b;margin-top:6px;text-transform:uppercase;letter-spacing:1px}}
.stat.ok .num{{color:#10b981;text-shadow:0 0 10px rgba(16,185,129,0.2)}}
.stat.err .num{{color:#ef4444;text-shadow:0 0 10px rgba(239,68,68,0.2)}}
.stat.limit .num{{color:#f59e0b;text-shadow:0 0 10px rgba(245,158,11,0.2)}}

.toolbar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}}
.toolbar h2{{font-size:16px;color:#fff;font-weight:600;display:flex;align-items:center;gap:8px}}
.toolbar h2::before{{content:"";display:inline-block;width:3px;height:14px;background:#6366f1;border-radius:2px}}

/* Grid & Cards (Glassmorphism + Neon effects) */
.grid{{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));
  gap:20px;
}}
.card{{
  background:rgba(18, 18, 29, 0.45);
  backdrop-filter:blur(10px);
  border:1px solid rgba(255, 255, 255, 0.04);
  border-radius:16px;padding:24px;
  transition:all .35s cubic-bezier(0.4, 0, 0.2, 1);
  position:relative;overflow:hidden;
  animation:cardFadeIn 0.5s ease-out both;
}}
@keyframes cardFadeIn{{
  from{{opacity:0;transform:translateY(20px)}}
  to{{opacity:1;transform:translateY(0)}}
}}
.card:hover{{
  border-color:rgba(99, 102, 241, 0.25);
  transform:translateY(-4px);
  box-shadow:0 12px 30px rgba(99, 102, 241, 0.12);
  background:rgba(18, 18, 29, 0.65);
}}
.card-head{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px}}
.model-name{{font-size:13px;font-family:"Fira Code",Monaco,Consolas,monospace;color:#fff;word-break:break-all;font-weight:600;letter-spacing:-0.2px}}

/* Badge System */
.badge{{
  display:inline-block;padding:3px 10px;border-radius:6px;font-size:10px;
  font-weight:700;text-transform:uppercase;white-space:nowrap;flex-shrink:0;margin-left:8px;
  letter-spacing:0.5px;
}}
.badge-ok{{background:rgba(16, 185, 129, 0.12);color:#34d399;border:1px solid rgba(16, 185, 129, 0.2)}}
.badge-error{{background:rgba(239, 68, 68, 0.12);color:#fca5a5;border:1px solid rgba(239, 68, 68, 0.2)}}
.badge-rate_limited{{background:rgba(245, 158, 11, 0.12);color:#fcd34d;border:1px solid rgba(245, 158, 11, 0.2)}}
.badge-untested{{background:rgba(71, 85, 105, 0.15);color:#94a3b8;border:1px solid rgba(71, 85, 105, 0.2)}}
.badge-testing{{
  background:rgba(59, 130, 246, 0.15);color:#60a5fa;border:1px solid rgba(59, 130, 246, 0.25);
  animation:pulse 1s infinite alternate;
}}
@keyframes pulse{{0%{{opacity:1;filter:brightness(1)}}100%{{opacity:.5;filter:brightness(1.2)}}}}

.card-body{{font-size:13px;color:#94a3b8;min-height:50px}}
.card-body .row{{display:flex;justify-content:space-between;margin-bottom:6px}}
.card-body .row .val{{color:#f1f5f9;font-weight:600;font-family:"Fira Code",monospace}}
.card-body .err-msg{{
  background:rgba(239, 68, 68, 0.06);border:1px solid rgba(239, 68, 68, 0.15);
  color:#f87171;padding:8px 12px;border-radius:8px;margin-top:10px;font-size:11px;
  word-break:break-all;font-family:monospace;line-height:1.4;
}}
.card-body .preview{{
  background:rgba(16, 185, 129, 0.04);border:1px solid rgba(16, 185, 129, 0.1);
  color:#34d399;padding:8px 12px;border-radius:8px;margin-top:10px;
  font-size:11px;font-family:"Fira Code",monospace;line-height:1.4;
}}
.card-foot{{margin-top:16px;display:flex;justify-content:flex-end}}

/* Premium Empty State */
.empty-state{{
  text-align:center;padding:80px 40px;
  background:rgba(18, 18, 29, 0.35);border-radius:24px;
  border:1px dashed rgba(255, 255, 255, 0.05);
  margin-top:20px;
}}
.empty-icon{{
  width:80px;height:80px;margin:0 auto 24px;
  background:rgba(99, 102, 241, 0.05);border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  border:1px solid rgba(99, 102, 241, 0.15);
  box-shadow:0 0 20px rgba(99,102,241,0.05);
  position:relative;
}}
.empty-icon::after{{
  content:"";position:absolute;width:96px;height:96px;border-radius:50%;
  border:1px dashed rgba(99, 102, 241, 0.2);
  animation:spinClockwise 20s linear infinite;
}}
@keyframes spinClockwise{{100%{{transform:rotate(360deg)}}}}
.empty-icon svg{{width:36px;height:36px;fill:#818cf8;animation:pulseLight 2s infinite alternate}}
@keyframes pulseLight{{0%{{opacity:0.6;filter:drop-shadow(0 0 2px rgba(129,140,248,0.2))}}100%{{opacity:1;filter:drop-shadow(0 0 10px rgba(129,140,248,0.6))}}}}
.empty-state h3{{font-size:18px;color:#fff;font-weight:700;margin-bottom:8px}}
.empty-state p{{font-size:13px;color:#64748b;max-width:380px;margin:0 auto 24px;line-height:1.5}}

/* Diagnostic Warning Banner (for upstream latency/breakout) */
.diagnostic-banner{{
  display:flex;align-items:center;gap:12px;padding:12px 20px;
  background:rgba(245, 158, 11, 0.08);border:1px solid rgba(245, 158, 11, 0.15);
  border-radius:12px;color:#fcd34d;font-size:12px;margin-bottom:24px;
  line-height:1.5;animation:slideDown 0.4s ease-out;
}}
@keyframes slideDown{{from{{transform:translateY(-10px);opacity:0}}to{{transform:translateY(0);opacity:1}}}}
.diagnostic-banner svg{{width:18px;height:18px;fill:#f59e0b;flex-shrink:0}}

/* Flow progress glow line */
.global-loading{{
  position:fixed;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg, #6366f1, #a855f7, #6366f1);
  background-size:200% 100%;
  animation:loading 1.5s linear infinite;display:none;z-index:999;
}}
@keyframes loading{{0%{{background-position:200% 0}}100%{{background-position:-200% 0}}}}
</style>
</head>
<body>
<div class="global-loading" id="globalLoading"></div>
<header>
  <div class="title-area">
    <svg viewBox="0 0 24 24"><path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 2.18l7 3.12v4.7c0 4.83-3.4 9.36-7 10.5-3.6-1.14-7-5.67-7-10.5V6.3l7-3.12z"/><path d="M11 7h2v6h-2zm0 8h2v2h-2z"/></svg>
    <h1>AnyRouter Bridge</h1>
    <span>Production</span>
  </div>
  <div class="actions">
    <textarea id="apiToken" placeholder="[API Keys Vault] 每行一个令牌（轮询轮空防护激活）" style=""></textarea>
    <button class="btn btn-primary" id="btnTestAll" onclick="testAll()">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
      Audit Upstream Models
    </button>
    <form method="POST" action="/dashboard/logout" style="margin:0">
      <button type="submit" class="btn btn-outline">Lock</button>
    </form>
  </div>
</header>
<main>
  <!-- Background diagnostics -->
  <div class="diagnostic-banner">
    <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
    <div>
      <strong>[SRE 审计背景说明]</strong> 当前专注测试链路: <code>claude-client -> newapi -> anyrouter-proxy -> anyrouter.top</code>。<br>
      <em>鉴于上游 anyrouter.top 目前存在高延迟、易断流的链路特征，系统已全面激活 10B 简包免 camouflageCamouflage 伪装、503 秒级快速失败传递和 Outbound 600s 连接存活防护。</em>
    </div>
  </div>

  <div class="stats">
    <div class="stat" id="statTotal"><div class="num" id="numTotal">0</div><div class="label">Total Models</div></div>
    <div class="stat ok" id="statOk"><div class="num" id="numOk">0</div><div class="label">Audited OK</div></div>
    <div class="stat err" id="statErr"><div class="num" id="numErr">0</div><div class="label">Failed / Slow</div></div>
    <div class="stat limit" id="statLimit"><div class="num" id="numLimit">0</div><div class="label">Rate Limited</div></div>
  </div>
  <div class="toolbar">
    <h2>Synchronized Endpoint Nodes</h2>
    <span id="lastUpdate" style="font-size:12px;color:#475569">Last sync: Never</span>
  </div>

  <!-- Interactive Grid Area -->
  <div id="grid">
    <!-- Initial dynamic empty state -->
    <div class="empty-state" id="emptyState">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none"/><path d="M12 6v6l4 2"/></svg>
      </div>
      <h3>Audit Portal Idle</h3>
      <p>初次进入尚未加载任何模型。请输入您的 API 密钥，然后点击右上角 "Audit Upstream Models" 按钮以实时开始第一步动态拉取、第二步批量测试。</p>
      <button class="btn btn-outline" onclick="testAll()">立即拉取并开始审计</button>
    </div>
  </div>
</main>
<script>
let MODELS={models_json};
let results={{}};

function badge(status){{
  const labels={{ok:"OK",error:"ERROR",rate_limited:"RATE LIMITED",untested:"UNTESTED",testing:"TESTING"}};
  return `<span class="badge badge-${{status}}">${{labels[status]||status}}</span>`;
}}

function renderCard(m, index){{
  const r=results[m]||{{status:"untested"}};
  let body='';
  if(r.latency_ms!=null) body+=`<div class="row"><span>Latency</span><span class="val">${{r.latency_ms}} ms</span></div>`;
  if(r.tested_at) body+=`<div class="row"><span>Tested Time</span><span class="val">${{new Date(r.tested_at).toLocaleTimeString()}}</span></div>`;
  if(r.error_message) body+=`<div class="err-msg">${{r.error_message}}</div>`;
  if(r.response_preview) body+=`<div class="preview">&gt; "${{r.response_preview}}"</div>`;
  if(!body) body='<div style="color:#475569">Pending dynamic test execution...</div>';
  
  // Apply staggered slide-up animation
  const delay = (index * 0.05).toFixed(2);
  return `<div class="card" id="card-${{m}}" style="animation-delay: ${{delay}}s">
    <div class="card-head"><span class="model-name">${{m}}</span>${{badge(r.status)}}</div>
    <div class="card-body">${{body}}</div>
    <div class="card-foot"><button class="btn btn-outline btn-sm" onclick="testOne('${{m}}')" ${{r.status==='testing'?'disabled':''}}>Test</button></div>
  </div>`;
}}

function renderAll(){{
  const gridEl = document.getElementById('grid');
  if(MODELS.length === 0){{
    // If empty list, keep showing empty state
    gridEl.innerHTML = `
      <div class="empty-state" id="emptyState">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none"/><path d="M12 6v6l4 2"/></svg>
        </div>
        <h3>Audit Portal Idle</h3>
        <p>初次进入尚未加载任何模型。请输入您的 API 密钥，然后点击右上角 "Audit Upstream Models" 按钮以实时开始第一步动态拉取、第二步批量测试。</p>
        <button class="btn btn-outline" onclick="testAll()">立即拉取并开始审计</button>
      </div>`;
  }} else {{
    // Render dynamic glassmorphism grid
    gridEl.innerHTML = `<div class="grid">${{MODELS.map((m, idx)=>renderCard(m, idx)).join('')}}</div>`;
  }}
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

async function fetchModels(){{
  const token=getToken();
  if(!token) return;
  console.log("[fetchModels] Starting models fetch via /api/fetch-models...");
  try{{
    const data = await api('/api/fetch-models');
    if (data && Array.isArray(data.models)) {{
      MODELS = data.models;
      console.log("[fetchModels] Successfully updated models list to: ", MODELS);
      MODELS.forEach(m => {{
        if (!results[m]) results[m] = {{status: 'untested'}};
      }});
      renderAll();
    }} else {{
      console.error("[fetchModels] Fetch failed:", data);
    }}
  }}catch(e){{
    console.error("[fetchModels] Failed to fetch models",e);
  }}
}}

async function api(url,opts={{}}){{
  const token=getToken();
  const headers=opts.headers||{{}};
  if(token) headers['x-api-key']=token.replace(/\\r?\\\\n/g, ',');
  try {{
    const resp=await fetch(url,{{method:'POST',...opts,headers}});
    if(resp.status===401){{window.location='/dashboard/login';return null;}}
    const contentType = resp.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {{
      return await resp.json();
    }} else {{
      const text = await resp.text();
      return {{ error: {{ message: `HTTP ${{resp.status}}: ${{text.slice(0, 100)}}` }} }};
    }}
  }} catch (e) {{
    console.error("API Error: ", e);
    return {{ error: {{ message: e.message || "Network Error" }} }};
  }}
}}

async function testOne(model){{
  if(!getToken()){{alert('Please enter an API Key first');return;}}
  console.log(`[testOne] Starting test for model: ${{model}}...`);
  results[model]={{...results[model],status:'testing'}};
  renderAll();
  try {{
    const data=await api(`/api/test/${{encodeURIComponent(model)}}`);
    console.log(`[testOne] Response for ${{model}}: `, data);
    if(data && !data.error){{
      results[model]=data;
    }} else if (data && data.error) {{
      console.error(`[testOne] API returned error for ${{model}}:`, data.error.message);
      results[model]={{
        status: 'error',
        error_message: data.error.message,
        tested_at: new Date().toISOString()
      }};
    }}
  }} catch (e) {{
    console.error(`[testOne] Exception occurred for ${{model}}:`, e);
    results[model]={{
      status: 'error',
      error_message: e.message,
      tested_at: new Date().toISOString()
    }};
  }} finally {{
    renderAll();
  }}
}}

async function testAll(){{
  if(!getToken()){{alert('Please enter an API Key first');return;}}
  const btn=document.getElementById('btnTestAll');
  const bar=document.getElementById('globalLoading');
  btn.disabled=true;
  bar.style.display='block';
  
  try {{
    // 第一步：实时获取模型列表
    btn.textContent='Step 1/2: Syncing Nodes...';
    console.log("[testAll] Step 1/2: Fetching models from upstream...");
    const fetchRes = await api('/api/fetch-models');
    if (fetchRes && Array.isArray(fetchRes.models)) {{
      MODELS = fetchRes.models;
      MODELS.forEach(m => {{
        if (!results[m]) results[m] = {{status: 'testing'}};
        else results[m].status = 'testing';
      }});
      renderAll();
    }} else {{
      console.error("[testAll] Failed to fetch models:", fetchRes);
      alert("Failed to fetch models from upstream.");
      return;
    }}
    
    // 第二步：对模型发起测试
    btn.textContent='Step 2/2: Auditing Nodes...';
    console.log("[testAll] Step 2/2: Starting batch model testing...");
    const data = await api('/api/test-all');
    console.log("[testAll] Batch test results received: ", data);
    if (data && !data.error) {{
      Object.assign(results, data);
    }} else if (data && data.error) {{
      console.error("[testAll] Batch test failed:", data.error.message);
      alert("Test execution completed with some failures: " + data.error.message);
    }}
  }} catch(e) {{
    console.error("[testAll] Exception during testAll process:", e);
    alert("Error during test process: " + e.message);
  }} finally {{
    btn.disabled=false;
    btn.innerHTML='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> Audit Upstream Models';
    bar.style.display='none';
    document.getElementById('lastUpdate').textContent='Last sync: '+new Date().toLocaleString();
    renderAll();
  }}
}}

// Setup listeners and load initial state
document.getElementById('apiToken').addEventListener('input',()=>{{
  const token=getToken();
  localStorage.setItem('anyrouter_api_key',token);
  // 当用户在输入框打字改变 token 时，为了保持干净和防过度抓取，不自动触发 fetchModels()，仅由点击开始审计触发，这极度精准！
}});

(async()=>{{
  const savedToken=localStorage.getItem('anyrouter_api_key');
  if(savedToken){{
    document.getElementById('apiToken').value=savedToken;
  }}
  
  const resp=await fetch('/api/model-status');
  if(resp.status===401){{window.location='/dashboard/login';return;}}
  const data=await resp.json();
  if(data){{Object.assign(results,data);}}
  
  // 保持首次加载全空不内置！即便 localStorage 有 token，也不在载入时静默抓取，只在点击 test 时拉取！
  renderAll();
}})();
</script>
</body>
</html>'''
