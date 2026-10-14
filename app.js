"use strict";

const $  = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const STATE = {
  tab: "overview",
  authMode: "login",
  user: null,
  mailTimer: null,
  voiceTimer: null,
  overviewTimer: null,
};

async function api(path, method = "GET", body = null, isForm = false) {
  const opts = { method, credentials: "same-origin" };
  if (body !== null) {
    if (isForm) opts.body = body;
    else {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
  }
  try {
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({ ok: false, error: "Bad JSON" }));
    return { status: r.status, data };
  } catch (e) {
    return { status: 0, data: { ok: false, error: "Network error" } };
  }
}

let _toastTimer = null;
function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show " + kind;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = "toast " + kind; }, 2600);
}

$$(".auth-tab").forEach(btn => {
  btn.onclick = () => {
    $$(".auth-tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    STATE.authMode = btn.dataset.auth;
    $("#authBtn").textContent = STATE.authMode === "login" ? "Đăng nhập" : "Đăng ký";
  };
});

$("#authBtn").onclick = async () => {
  const u = $("#authUser").value.trim();
  const p = $("#authPass").value;
  if (!u || !p) return toast("Nhập đủ username và password", "err");
  const path = STATE.authMode === "login" ? "/api/login" : "/api/register";
  const { data } = await api(path, "POST", { username: u, password: p });
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast("Đăng nhập thành công", "ok");
  await showApp();
};

async function logout() {
  await api("/api/logout", "POST");
  location.reload();
}

$$(".nav-item").forEach(el => {
  el.onclick = () => {
    const tab = el.dataset.tab;
    if (!tab) return;
    switchTab(tab);
    if (window.innerWidth <= 820) $("#sidebar").classList.remove("open");
  };
});

$("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");

function switchTab(tab) {
  STATE.tab = tab;
  $$(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.tab === tab));
  stopPolling();
  renderTab();
}

function stopPolling() {
  if (STATE.mailTimer)     { clearInterval(STATE.mailTimer); STATE.mailTimer = null; }
  if (STATE.voiceTimer)    { clearInterval(STATE.voiceTimer); STATE.voiceTimer = null; }
  if (STATE.overviewTimer) { clearInterval(STATE.overviewTimer); STATE.overviewTimer = null; }
}

const TABS = {
  overview: renderOverview,
  mail:     renderMail,
  token:    renderToken,
  status:   renderStatus,
  badge:    renderBadge,
  nick:     renderNick,
  voice:    renderVoice,
  name:     renderName,
};

function renderTab() {
  const el = $("#tabContent");
  const fn = TABS[STATE.tab];
  if (!fn) { el.innerHTML = ""; return; }
  el.innerHTML = "";
  fn(el);
}

async function renderOverview(el) {
  el.innerHTML = `<div class="cards" id="ovCards">${overviewSkeleton()}</div>`;
  await loadOverview();
  STATE.overviewTimer = setInterval(loadOverview, 15000);
}

function overviewSkeleton() {
  return [1,2,3,4,5,6].map(() => `
    <div class="card">
      <div class="card-head"><div class="card-title">—</div><div class="card-ico">·</div></div>
      <div class="card-value">—</div>
    </div>`).join("");
}

async function loadOverview() {
  const { data } = await api("/api/overview");
  if (!data.ok) return;
  const st = { online: "Online", idle: "Idle", dnd: "DND", streaming: "Streaming" }[data.status] || data.status;

  const cards = [
    { title: "Token",     ico: "🔑", value: data.tokens,        sub: `${data.keepalive ? "Keep-alive ON" : "Keep-alive OFF"} · ${data.keepalive_count} tasks`, dot: data.keepalive },
    { title: "Voice",     ico: "🎙", value: data.voice_active ? `${data.voice_joined}/${data.voice_total}` : "0", sub: data.voice_active ? "Đang treo voice" : "Chưa hoạt động", dot: data.voice_active },
    { title: "Status",    ico: "🎨", value: st, sub: "Trạng thái hiện tại" },
    { title: "Mail",      ico: "✉",  value: data.mail_count,    sub: data.mail_count ? "Session active" : "Không có session", dot: data.mail_count > 0 },
    { title: "Name",      ico: "📛", value: data.named_count,   sub: "Names đã dùng" },
    { title: "Tài khoản", ico: "👤", value: data.username,      sub: `ID: ${STATE.user?.user_id ?? "—"}` },
  ];

  const html = cards.map(c => `
    <div class="card">
      <div class="card-head">
        <div class="card-title">${c.title}</div>
        <div class="card-ico">${c.ico}</div>
      </div>
      <div class="card-value">${c.value}</div>
      <div class="card-sub">${c.dot !== undefined ? `<span class="dot ${c.dot ? "on" : "off"}"></span>` : ""}${c.sub}</div>
    </div>`).join("");

  const wrap = $("#ovCards");
  if (wrap) wrap.innerHTML = html;
}

function renderMail(el) {
  el.innerHTML = `
    <h2>✉️ Temp Mail</h2>
    <div class="row gap">
      <button class="btn-primary" onclick="mailCreate(1)">Tạo 1 mail</button>
      <button class="btn-cyan" onclick="mailCreate(5)">Tạo 5 mail</button>
      <button class="btn-cyan" onclick="mailCreate(10)">Tạo 10 mail</button>
      <button class="btn-danger" onclick="mailCancel()">Hủy session</button>
    </div>
    <h3 style="margin-top:20px">Đang hoạt động</h3>
    <div id="mailList" class="muted">Đang tải...</div>
    <h3 style="margin-top:20px">📥 Inbox</h3>
    <div id="mailInbox" class="log">Chưa có mail nào...</div>
  `;
  mailRefresh();
  STATE.mailTimer = setInterval(mailRefresh, 4000);
}

async function mailCreate(n) {
  toast("Đang tạo " + n + " mail...");
  const { data } = await api("/api/mail/create", "POST", { count: n });
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast(`Đã tạo ${data.mails.length} mail`, "ok");
  mailRefresh();
}

async function mailCancel() {
  await api("/api/mail/cancel", "POST");
  toast("Đã hủy session mail", "ok");
  mailRefresh();
}

async function mailRefresh() {
  const { data } = await api("/api/mail/current");
  const list = $("#mailList");
  const inbox = $("#mailInbox");
  if (!list || !inbox) return;

  if (!data.ok || !data.active) {
    list.innerHTML = '<p class="muted">Không có session mail.</p>';
    inbox.textContent = "Chưa có mail nào...";
    return;
  }

  list.innerHTML = data.mails.map(m =>
    `<div class="token-row"><span class="tk"><b style="color:var(--text)">${m.email}</b> <span class="pill">${m.provider}</span></span></div>`
  ).join("");

  let txt = "";
  for (const [email, msgs] of Object.entries(data.inbox)) {
    msgs.forEach(m => {
      txt += `📧 ${email}\nFrom: ${m.from}\nSubject: ${m.subject}\n${(m.body || "").slice(0, 700)}\n${"—".repeat(38)}\n`;
    });
  }
  inbox.textContent = txt || "Chưa có mail nào...";
  inbox.scrollTop = inbox.scrollHeight;
}

function renderToken(el) {
  el.innerHTML = `
    <h2>🔑 Token Manager</h2>
    <label>Nhập token (mỗi dòng 1 cái)</label>
    <textarea id="tokenInput" placeholder="MTIzNDU2..."></textarea>
    <div class="row gap mt-3">
      <button class="btn-primary" onclick="tokenAdd()">Thêm token</button>
      <button class="btn-cyan" onclick="tokenCheck()">Check all</button>
      <button class="btn-cyan" onclick="kaStart()">Bật keep-alive</button>
      <button class="btn-danger" onclick="kaStop()">Dừng keep-alive</button>
      <button class="btn-danger" onclick="tokenDelete(-1,true)">Xóa hết</button>
    </div>
    <div id="tokenBar" class="bar"><div style="width:0%"></div></div>
    <div id="tokenList" style="margin-top:16px"></div>
  `;
  tokenRefresh();
}

async function tokenRefresh() {
  const { data } = await api("/api/token/list");
  const el = $("#tokenList");
  if (!el || !data.ok) return;
  const tk = data.tokens || [];
  if (!tk.length) { el.innerHTML = '<p class="muted">Chưa có token.</p>'; return; }

  const head = `<p style="margin-bottom:12px">Tổng: <b>${tk.length}</b> token · Keep-alive <span class="pill ${data.keepalive ? "ok" : ""}">${data.keepalive ? "ON" : "OFF"}</span></p>`;
  const rows = tk.slice(0, 100).map((t, i) =>
    `<div class="token-row">
       <span class="tk">#${i + 1} ${t.slice(0, 28)}…${t.slice(-8)}</span>
       <button class="btn-danger btn-sm" onclick="tokenDelete(${i},false)">Xóa</button>
     </div>`).join("");
  const more = tk.length > 100 ? `<p class="muted" style="margin-top:8px">... và ${tk.length - 100} token khác</p>` : "";
  el.innerHTML = head + rows + more;
}

async function tokenAdd() {
  const raw = ($("#tokenInput")?.value || "").trim();
  if (!raw) return toast("Nhập token trước", "err");
  const tokens = raw.split("\n").map(x => x.trim()).filter(Boolean);
  toast(`Đang check ${tokens.length} token...`);
  const { data } = await api("/api/token/add", "POST", { tokens });
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  const added = data.added.filter(x => x.added).length;
  toast(`Thêm ${added} token mới, ${data.failed.length} lỗi`, added > 0 ? "ok" : "err");
  $("#tokenInput").value = "";
  tokenRefresh();
}

async function tokenCheck() {
  const bar = $("#tokenBar")?.firstElementChild;
  if (bar) bar.style.width = "10%";
  const { data } = await api("/api/token/check", "POST");
  if (bar) bar.style.width = "100%";
  if (!data.ok) return toast("Lỗi check", "err");
  const el = $("#tokenList");
  if (!el) return;
  let html = `<p>Tổng <b>${data.total}</b> · ✅ ${data.ok_count} · ❌ ${data.dead} · ⏱ ${data.rate} · ⚠️ ${data.fail}</p>`;
  html += `<div class="log mt-3">${data.results.map(r => `${r.token.slice(0,20)}… → ${r.result}`).join("\n")}</div>`;
  el.innerHTML = html;
  setTimeout(() => { if (bar) bar.style.width = "0%"; }, 1500);
}

async function tokenDelete(idx, all) {
  const body = all ? { all: true } : { index: idx };
  const { data } = await api("/api/token/delete", "POST", body);
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast("Đã xóa", "ok");
  tokenRefresh();
}

async function kaStart() {
  const { data } = await api("/api/token/keepalive/start", "POST");
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast(`Keep-alive với ${data.count} token`, "ok");
  tokenRefresh();
}

async function kaStop() {
  await api("/api/token/keepalive/stop", "POST");
  toast("Đã dừng keep-alive", "ok");
  tokenRefresh();
}

function renderStatus(el) {
  el.innerHTML = `
    <h2>🎨 Status</h2>
    <p class="muted" style="margin-bottom:14px">Chọn status cho keep-alive. Cần dừng + bật lại để áp dụng.</p>
    <div class="row gap">
      <button class="btn-ghost" onclick="setStatus('online')">🟢 Online</button>
      <button class="btn-ghost" onclick="setStatus('idle')">🌙 Idle</button>
      <button class="btn-ghost" onclick="setStatus('dnd')">🔴 DND</button>
      <button class="btn-ghost" onclick="setStatus('streaming')">🟣 Streaming</button>
    </div>
    <p class="muted mt-3">Streaming URL: <code style="color:var(--accent2)">https://twitch.tv/666</code></p>
    <p id="statusNote" style="color:var(--warn);margin-top:10px;font-size:12.5px"></p>
  `;
}

async function setStatus(s) {
  const { data } = await api("/api/status/set", "POST", { status: s });
  if (!data.ok) return toast("Lỗi", "err");
  toast("Status: " + s, "ok");
  const n = $("#statusNote");
  if (n) n.textContent = data.note || "";
}

function renderBadge(el) {
  el.innerHTML = `
    <h2>🏅 HypeSquad</h2>
    <div class="row gap">
      <button class="btn-primary" onclick="badgeClaim('bravery')">🏅 Bravery</button>
      <button class="btn-primary" onclick="badgeClaim('brilliance')">🏅 Brilliance</button>
      <button class="btn-primary" onclick="badgeClaim('balance')">🏅 Balance</button>
      <button class="btn-danger" onclick="badgeRemove()">Xóa Badge</button>
    </div>
    <div id="badgeLog" class="log mt-3">Sẵn sàng...</div>
  `;
}

async function badgeClaim(house) {
  const log = $("#badgeLog");
  log.textContent = "Đang claim " + house + "...";
  const { data } = await api("/api/badge/claim", "POST", { house });
  if (!data.ok) return log.textContent = data.error || "Lỗi";
  log.textContent = data.results.map(r => `${r.token} → ${r.result}`).join("\n") || "Không có token";
}

async function badgeRemove() {
  const log = $("#badgeLog");
  log.textContent = "Đang remove...";
  const { data } = await api("/api/badge/remove", "POST");
  if (!data.ok) return log.textContent = data.error || "Lỗi";
  log.textContent = data.results.map(r => `${r.token} → ${r.result}`).join("\n") || "Không có token";
}

function renderNick(el) {
  el.innerHTML = `
    <h2>👤 Đổi Display Name</h2>
    <div class="grid-2">
      <div>
        <label>Server ID</label>
        <input id="nickGuild" placeholder="123456789"/>
        <label>Nick mới</label>
        <input id="nickValue" placeholder="new nickname"/>
        <div class="mt-3">
          <button class="btn-primary" onclick="nickChange()">Đổi nick</button>
        </div>
      </div>
      <div>
        <h3>Kết quả</h3>
        <div id="nickLog" class="log">Sẵn sàng...</div>
      </div>
    </div>
  `;
}

async function nickChange() {
  const g = $("#nickGuild").value.trim();
  const n = $("#nickValue").value.trim();
  if (!g || !n) return toast("Nhập đủ server ID và nick", "err");
  const log = $("#nickLog");
  log.textContent = "Đang xử lý...";
  const { data } = await api("/api/nick/change", "POST", { guild_id: g, nick: n });
  if (!data.ok) return log.textContent = data.error || "Lỗi";
  let out = `Guild: ${data.guild_name}\n✅ ${data.ok_count} · ⏭ ${data.skip} · ❌ ${data.dead} · ⚠️ ${data.fail}\n${"—".repeat(30)}\n`;
  out += data.results.slice(0, 60).map(r => `${r.token} → ${r.result}`).join("\n");
  log.textContent = out;
}

function renderVoice(el) {
  el.innerHTML = `
    <h2>🎙 Treo Voice</h2>
    <div class="grid-2">
      <div>
        <label>Server ID</label>
        <input id="voiceGuild" placeholder="123456789"/>
        <label>Voice Channel ID</label>
        <input id="voiceChannel" placeholder="987654321"/>
        <div class="chk-row">
          <label class="chk"><input type="checkbox" id="vMute"/> Mic OFF</label>
          <label class="chk"><input type="checkbox" id="vDeaf"/> Deaf</label>
          <label class="chk"><input type="checkbox" id="vCam"/> Cam</label>
          <label class="chk"><input type="checkbox" id="vStream"/> Live</label>
        </div>
        <div class="row gap mt-3">
          <button class="btn-primary" onclick="voiceStart()">Bắt đầu</button>
          <button class="btn-danger" onclick="voiceStop()">Dừng</button>
          <button class="btn-ghost" onclick="voiceRefresh()">Refresh</button>
        </div>
        <div class="row gap mt-3">
          <button class="btn-ghost btn-sm" onclick="voiceToggle('mute')">🎤 Mic</button>
          <button class="btn-ghost btn-sm" onclick="voiceToggle('deaf')">🔈 Deaf</button>
          <button class="btn-ghost btn-sm" onclick="voiceToggle('camera')">📷 Cam</button>
          <button class="btn-ghost btn-sm" onclick="voiceToggle('stream')">📺 Live</button>
        </div>
      </div>
      <div>
        <h3>Trạng thái</h3>
        <div id="voiceLog" class="log">Chưa có process...</div>
      </div>
    </div>
  `;
  voiceRefresh();
  STATE.voiceTimer = setInterval(voiceRefresh, 3000);
}

async function voiceStart() {
  const g = $("#voiceGuild").value.trim();
  const c = $("#voiceChannel").value.trim();
  if (!g || !c) return toast("Nhập đủ guild + channel ID", "err");
  const { data } = await api("/api/voice/start", "POST", {
    guild_id: g, channel_id: c,
    mute: $("#vMute").checked, deaf: $("#vDeaf").checked,
    camera: $("#vCam").checked, stream: $("#vStream").checked,
  });
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast(`Đang treo ${data.total} token`, "ok");
  voiceRefresh();
}

async function voiceStop() {
  await api("/api/voice/stop", "POST");
  toast("Đã dừng voice", "ok");
  voiceRefresh();
}

async function voiceToggle(key) {
  const { data } = await api("/api/voice/toggle", "POST", { key });
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast(`${key} → ${data.value ? "ON" : "OFF"}`, "ok");
}

async function voiceRefresh() {
  const log = $("#voiceLog");
  if (!log) return;
  const { data } = await api("/api/voice/status");
  if (!data.ok) return;
  if (!data.active) { log.textContent = "Chưa có process..."; return; }
  const s = data.state;
  log.textContent =
    `Server: ${s.guild_id}\nChannel: ${s.channel_id}\n` +
    `Joined: ${s.joined}/${s.total} · Lỗi: ${s.failed}\n` +
    `Tasks: ${data.alive}/${data.total}\n` +
    `Mic: ${s.mute ? "OFF" : "ON"} · Deaf: ${s.deaf ? "OFF" : "ON"}\n` +
    `Cam: ${s.camera ? "ON" : "OFF"} · Live: ${s.stream ? "ON" : "OFF"}`;
}

function renderName(el) {
  el.innerHTML = `
    <h2>📛 Name Manager</h2>
    <div class="grid-3">
      <div class="card">
        <h3>Thống kê</h3>
        <p style="font-size:22px;font-weight:800" id="nameCount">0</p>
        <div class="mt-3"><button class="btn-ghost btn-sm" onclick="nameLoad()">Refresh</button></div>
      </div>
      <div class="card">
        <h3>Sinh random</h3>
        <input id="nameGenCount" type="number" value="20" min="1" max="500"/>
        <div class="mt-3"><button class="btn-primary btn-sm" onclick="nameGen()">Sinh</button></div>
      </div>
      <div class="card">
        <h3>Upload / Xóa</h3>
        <input type="file" id="nameFile" accept=".txt"/>
        <div class="row gap mt-3">
          <button class="btn-primary btn-sm" onclick="nameUpload()">Upload</button>
          <button class="btn-danger btn-sm" onclick="nameDelete()">Xóa hết</button>
        </div>
      </div>
    </div>
    <div id="nameList" class="log mt-3">Đang tải...</div>
  `;
  nameLoad();
}

async function nameLoad() {
  const { data } = await api("/api/name/list");
  const c = $("#nameCount"); const l = $("#nameList");
  if (!data.ok) return;
  if (c) c.textContent = data.count;
  if (l) {
    l.textContent = data.names.slice(0, 200).join("\n")
      + (data.names.length > 200 ? `\n... và ${data.names.length - 200} name khác` : "");
  }
}

async function nameGen() {
  const n = parseInt($("#nameGenCount").value, 10) || 20;
  const { data } = await api("/api/name/gen", "POST", { count: n });
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast(`Sinh ${data.generated} name`, "ok");
  nameLoad();
}

async function nameUpload() {
  const f = $("#nameFile")?.files[0];
  if (!f) return toast("Chọn file .txt", "err");
  const fd = new FormData();
  fd.append("file", f);
  const { data } = await api("/api/name/upload", "POST", fd, true);
  if (!data.ok) return toast(data.error || "Lỗi", "err");
  toast(`Đã ghi đè ${data.count} name`, "ok");
  nameLoad();
}

async function nameDelete() {
  await api("/api/name/delete", "POST");
  toast("Đã xóa named.txt", "ok");
  nameLoad();
}

document.addEventListener("keydown", (e) => {
  if (e.altKey && (e.key === "1" || e.code === "Digit1")) {
    e.preventDefault();
    openAdmin();
  }
  if (e.key === "Escape") closeAdmin();
});

function openAdmin() {
  const m = $("#adminModal");
  if (m) m.classList.remove("hidden");
  setTimeout(() => $("#adminKey")?.focus(), 40);
}
function closeAdmin() {
  const m = $("#adminModal");
  if (m) m.classList.add("hidden");
  const k = $("#adminKey"); if (k) k.value = "";
  const r = $("#adminResult"); if (r) r.innerHTML = "";
}

async function adminUnlock() {
  const key = $("#adminKey").value;
  const { data } = await api("/api/admin/users", "POST", { key });
  if (!data.ok) return toast(data.error || "Key sai", "err");
  const res = $("#adminResult");
  if (!data.users.length) { res.innerHTML = '<p class="muted">Không có user nào có token.</p>'; return; }
  let html = `<p class="muted" style="margin-bottom:10px">${data.users.length} user có token</p>`;
  html += data.users.map(u => `
    <div class="user-block">
      <h4>👤 ${u.username} <span class="pill">id: ${u.user_id}</span> <span class="pill ok">${u.count} token</span></h4>
      <div class="mono">${u.tokens.map((t, i) => `#${i + 1}  ${t}`).join("\n")}</div>
    </div>`).join("");
  res.innerHTML = html;
}

async function showApp() {
  const { data } = await api("/api/me");
  if (!data.ok) return;
  STATE.user = data;
  $("#authScreen").classList.add("hidden");
  $("#appScreen").classList.remove("hidden");
  $("#welcomeName").textContent = data.username;
  $("#userName").textContent = data.username;
  $("#userAvatar").textContent = (data.username || "?").slice(0, 1);
  switchTab("overview");
}

(async function boot() {
  const { data } = await api("/api/me");
  if (data.ok) {
    STATE.user = data;
    $("#authScreen").classList.add("hidden");
    $("#appScreen").classList.remove("hidden");
    $("#welcomeName").textContent = data.username;
    $("#userName").textContent = data.username;
    $("#userAvatar").textContent = (data.username || "?").slice(0, 1);
    switchTab("overview");
  }
})();