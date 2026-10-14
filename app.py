# app.py — Cosmic Token Manager (FastAPI)
import os, io, json, time, random, string, secrets, sqlite3, asyncio, hashlib
from pathlib import Path
from typing import Optional
from base64 import b64encode

from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature
import requests
import websockets

# =========================================================
# CONFIG
# =========================================================
BASE_DIR      = Path(__file__).resolve().parent
DATA_DIR      = BASE_DIR / "data"
USERS_DIR     = DATA_DIR / "users"
DB_PATH       = DATA_DIR / "users.db"
SECRET_KEY    = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
ADMIN_KEY     = "ADMIN"
STREAM_URL    = "https://twitch.tv/666"
POLL_INTERVAL = 2.0

DATA_DIR.mkdir(exist_ok=True)
USERS_DIR.mkdir(exist_ok=True)

# =========================================================
# USER DB
# =========================================================
def _db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def _init_db():
    with _db() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "username TEXT UNIQUE NOT NULL,"
            "password_hash TEXT NOT NULL,"
            "created_at INTEGER NOT NULL)"
        )
        c.commit()

def _hash_pw(pw, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000).hex()
    return salt + "$" + h

def _verify_pw(pw, stored):
    try:
        salt, _ = stored.split("$", 1)
    except Exception:
        return False
    return secrets.compare_digest(_hash_pw(pw, salt), stored)

def create_user(username, password):
    if not username or len(username) < 3 or len(password) < 4:
        return None
    try:
        with _db() as c:
            cur = c.execute(
                "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
                (username, _hash_pw(password), int(time.time())))
            c.commit()
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None

def get_user_by_name(username):
    with _db() as c:
        return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

def get_user_by_id(uid):
    with _db() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

_init_db()

# =========================================================
# SESSION
# =========================================================
_signer = URLSafeTimedSerializer(SECRET_KEY, salt="cosmic")

def set_session(resp, user_id):
    token = _signer.dumps(user_id)
    resp.set_cookie("session", token, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600, path="/")

def clear_session(resp):
    resp.delete_cookie("session", path="/")

def current_user_id(request):
    tok = request.cookies.get("session")
    if not tok:
        return None
    try:
        return int(_signer.loads(tok, max_age=30 * 24 * 3600))
    except Exception:
        return None

def require_user(request):
    uid = current_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not logged in")
    return uid

# =========================================================
# PER-USER STORAGE
# =========================================================
def user_dir(uid):
    p = USERS_DIR / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p

def tokens_file(uid):
    return user_dir(uid) / "tokens.txt"

def status_file(uid):
    return user_dir(uid) / "status.txt"

def named_file(uid):
    return user_dir(uid) / "named.txt"

def load_tokens(uid):
    p = tokens_file(uid)
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]

def write_tokens(uid, tokens):
    tokens_file(uid).write_text("\n".join(tokens) + ("\n" if tokens else ""),
                                encoding="utf-8")

def append_tokens(uid, new):
    cur = set(load_tokens(uid))
    add = [t for t in new if t not in cur]
    if add:
        with tokens_file(uid).open("a", encoding="utf-8") as f:
            for t in add:
                f.write(t + "\n")
    return len(add)

def get_status(uid):
    p = status_file(uid)
    if p.exists():
        s = p.read_text(encoding="utf-8").strip().lower()
        if s in ("online", "idle", "dnd", "streaming"):
            return s
    return "online"

def set_status(uid, s):
    if s not in ("online", "idle", "dnd", "streaming"):
        s = "online"
    status_file(uid).write_text(s, encoding="utf-8")

def load_named(uid):
    p = named_file(uid)
    if not p.exists():
        return set()
    return {l.strip().lower() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}

def append_named(uid, names):
    with named_file(uid).open("a", encoding="utf-8") as f:
        for n in names:
            f.write(n + "\n")

def overwrite_named(uid, names):
    named_file(uid).write_text("\n".join(names) + ("\n" if names else ""),
                               encoding="utf-8")

# =========================================================
# MAIL PROVIDERS
# =========================================================
try:
    from TempMail import TempMail as _TempMail
    _TEMPMAIL_OK = True
except Exception:
    _TempMail = None
    _TEMPMAIL_OK = False

class TempMailProvider:
    name = "TempMail"
    def create(self):
        if not _TEMPMAIL_OK:
            return None
        try:
            t = _TempMail()
            i = t.createInbox()
            if i and getattr(i, "address", None):
                return {"email": i.address,
                        "token": getattr(i, "token", None) or i.address}
        except Exception:
            return None
        return None
    def fetch(self, token, seen):
        if not _TEMPMAIL_OK or not token:
            return []
        try:
            t = _TempMail()
            emails = t.getEmails(token)
        except Exception:
            return []
        out = []
        for m in emails or []:
            mid = str(getattr(m, "id", "") or getattr(m, "token", "") or id(m))
            if mid in seen:
                continue
            seen.add(mid)
            body = str(getattr(m, "body", "") or "") + str(getattr(m, "html", "") or "")
            out.append({
                "subject": str(getattr(m, "subject", "(no subject)")),
                "from": str(getattr(m, "sender", "") or getattr(m, "from_", "?")),
                "body": body[:4000],
            })
        return out

class MailTmProvider:
    name = "mail.tm"
    BASE = "https://api.mail.tm"
    def create(self):
        try:
            r = requests.get(self.BASE + "/domains", timeout=15)
            domain = r.json()["hydra:member"][0]["domain"]
            user = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
            email = user + "@" + domain
            pw = secrets.token_urlsafe(12)
            r = requests.post(self.BASE + "/accounts",
                              json={"address": email, "password": pw}, timeout=15)
            if r.status_code not in (200, 201):
                return None
            r = requests.post(self.BASE + "/token",
                              json={"address": email, "password": pw}, timeout=15)
            return {"email": email, "token": r.json()["token"]}
        except Exception:
            return None
    def fetch(self, token, seen):
        h = {"Authorization": "Bearer " + token}
        try:
            r = requests.get(self.BASE + "/messages", headers=h, timeout=15)
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []
        out = []
        for m in data.get("hydra:member", []):
            mid = m["id"]
            if mid in seen:
                continue
            seen.add(mid)
            try:
                full = requests.get(self.BASE + "/messages/" + mid, headers=h, timeout=15).json()
            except Exception:
                continue
            body = full.get("text") or full.get("html") or []
            if isinstance(body, list):
                body = "\n".join(body)
            out.append({
                "subject": full.get("subject", "(no subject)"),
                "from": full.get("from", {}).get("address", "?"),
                "body": body[:4000],
            })
        return out

class OneSecMailProvider:
    name = "1secmail"
    BASE = "https://www.1secmail.com/api/v1/"
    def create(self):
        d = ["1secmail.com", "1secmail.org", "1secmail.net", "icloudmail.net"]
        user = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=9))
        return {"email": user + "@" + random.choice(d), "token": None}
    def fetch(self, token, seen):
        try:
            user, domain = token.split("@")
        except Exception:
            return []
        try:
            r = requests.get(self.BASE,
                             params={"action": "getMessages", "login": user, "domain": domain},
                             timeout=15)
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []
        out = []
        for m in data:
            mid = str(m["id"])
            if mid in seen:
                continue
            seen.add(mid)
            try:
                full = requests.get(self.BASE,
                                    params={"action": "readMessage", "login": user,
                                            "domain": domain, "id": mid},
                                    timeout=15).json()
            except Exception:
                continue
            out.append({
                "subject": full.get("subject", "(no subject)"),
                "from": full.get("from", "?"),
                "body": (full.get("textBody") or full.get("body") or "")[:4000],
            })
        return out

class MaildropProvider:
    name = "Maildrop"
    URL = "https://api.maildrop.cc/graphql"
    def create(self):
        user = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=10))
        return {"email": user + "@maildrop.cc", "token": user}
    def fetch(self, token, seen):
        q = {"query": 'query { inbox(mailbox:"' + token + '") { id subject headerfrom data } }'}
        try:
            r = requests.post(self.URL, json=q,
                              headers={"Content-Type": "application/json"}, timeout=15)
            data = r.json()
        except Exception:
            return []
        out = []
        for m in data.get("data", {}).get("inbox", []):
            mid = m["id"]
            if mid in seen:
                continue
            seen.add(mid)
            out.append({
                "subject": m.get("subject", "(no subject)"),
                "from": m.get("headerfrom", "?"),
                "body": (m.get("data") or "")[:4000],
            })
        return out

FREE_PROVIDERS = [TempMailProvider(), MailTmProvider(), OneSecMailProvider(), MaildropProvider()]

MAIL_SESSIONS = {}
MAIL_TASKS = {}

async def mail_poll_loop(uid):
    while uid in MAIL_SESSIONS:
        sess = MAIL_SESSIONS[uid]
        for m in sess["mails"]:
            try:
                msgs = await asyncio.to_thread(m["provider"].fetch, m["token"], m["seen"])
                for msg in msgs:
                    sess["inbox"].setdefault(m["email"], []).append(msg)
            except Exception:
                pass
        await asyncio.sleep(POLL_INTERVAL)

# =========================================================
# DISCORD OPS
# =========================================================
def _sp():
    sp = {
        "os": "Windows", "browser": "Chrome", "device": "",
        "system_locale": "en-US",
        "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
        "browser_version": "120.0.0.0", "os_version": "10",
        "referrer": "https://discord.com/", "referring_domain": "discord.com",
        "search_engine": "google", "release_channel": "stable",
        "client_build_number": 266529, "client_event_source": None,
    }
    return b64encode(json.dumps(sp, separators=(",", ":")).encode()).decode()

def hs_headers(token):
    return {
        "Authorization": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/channels/@me",
        "X-Discord-Locale": "en-US", "X-Debug-Options": "bugReporterEnabled",
        "X-Super-Properties": _sp(),
    }

def check_token_sync(token):
    try:
        r = requests.get("https://discord.com/api/v9/users/@me",
                         headers={"Authorization": token.strip()}, timeout=10)
    except Exception as e:
        return {"ok": False, "reason": "Network: " + str(e)[:60]}
    if r.status_code == 401:
        return {"ok": False, "reason": "Dead (401)"}
    if r.status_code == 403:
        return {"ok": False, "reason": "Locked (403)"}
    if r.status_code == 429:
        return {"ok": False, "reason": "Rate (429)"}
    if r.status_code != 200:
        return {"ok": False, "reason": "HTTP " + str(r.status_code)}
    d = r.json()
    if d.get("bot"):
        return {"ok": False, "reason": "Bot token"}
    return {"ok": True, "user": d}

def token_alive_sync(token):
    try:
        r = requests.get("https://discord.com/api/v9/users/@me",
                         headers=hs_headers(token), timeout=12)
        if r.status_code == 200:
            if r.json().get("bot"):
                return "BOT"
            return "OK"
        if r.status_code == 401:
            return "DEAD"
        if r.status_code == 403:
            return "LOCKED"
        if r.status_code == 429:
            return "RATE"
        return "HTTP" + str(r.status_code)
    except Exception as e:
        return "ERR:" + str(e)[:40]

def claim_hs(token, house_id):
    try:
        r = requests.post("https://discord.com/api/v9/hypesquad/online",
                          headers=hs_headers(token),
                          json={"house_id": house_id}, timeout=20)
        if r.status_code == 204:
            return "OK"
        if r.status_code == 401:
            return "DEAD"
        if r.status_code == 429:
            return "RATE"
        return "HTTP" + str(r.status_code)
    except Exception as e:
        return "ERR:" + str(e)[:60]

def remove_hs(token):
    try:
        r = requests.delete("https://discord.com/api/v9/hypesquad/online",
                            headers=hs_headers(token), timeout=20)
        if r.status_code == 204:
            return "OK"
        if r.status_code == 401:
            return "DEAD"
        if r.status_code == 429:
            return "RATE"
        return "HTTP" + str(r.status_code)
    except Exception as e:
        return "ERR:" + str(e)[:60]

def check_guild_member_sync(token, guild_id):
    try:
        r = requests.get("https://discord.com/api/v9/users/@me",
                         headers=hs_headers(token), timeout=10)
        if r.status_code != 200:
            return False
        uid = r.json().get("id")
        r2 = requests.get("https://discord.com/api/v9/guilds/" + guild_id + "/members/" + uid,
                          headers=hs_headers(token), timeout=10)
        return r2.status_code == 200
    except Exception:
        return False

def change_guild_nick_sync(token, guild_id, nick):
    try:
        r = requests.patch("https://discord.com/api/v9/guilds/" + guild_id + "/members/@me",
                           headers=hs_headers(token), json={"nick": nick}, timeout=15)
        if r.status_code in (200, 204):
            return "OK"
        if r.status_code == 401:
            return "DEAD"
        if r.status_code == 403:
            return "NO_PERM"
        if r.status_code == 429:
            return "RATE"
        return "HTTP" + str(r.status_code)
    except Exception as e:
        return "ERR:" + str(e)[:60]

async def get_guild_name(token, guild_id):
    try:
        r = await asyncio.to_thread(
            requests.get, "https://discord.com/api/v9/guilds/" + guild_id,
            headers=hs_headers(token), timeout=10)
        if r.status_code == 200:
            return r.json().get("name", "?")
    except Exception:
        pass
    return "?"

# =========================================================
# KEEPALIVE
# =========================================================
GATEWAY = "wss://gateway.discord.gg/?v=9&encoding=json"
KEEPALIVE = {}

def _presence(s):
    if s == "streaming":
        return {"since": 0, "status": "online", "afk": False,
                "activities": [{"name": "Twitch", "type": 1, "url": STREAM_URL}]}
    return {"since": 0, "status": s, "afk": False, "activities": []}

async def keepalive_worker(token, status, stop):
    session_id = None
    resume_url = None
    seq = 0
    reconnect = 0
    while not stop.is_set():
        try:
            url = resume_url or GATEWAY
            async with websockets.connect(url, ping_interval=None,
                                          max_size=10000000,
                                          close_timeout=5) as ws:
                hello = json.loads(await ws.recv())
                hb_int = hello["d"]["heartbeat_interval"] / 1000

                async def hb():
                    while not stop.is_set():
                        await asyncio.sleep(hb_int)
                        try:
                            await ws.send(json.dumps({"op": 1, "d": seq}))
                        except Exception:
                            return

                hb_task = asyncio.create_task(hb())

                if session_id and resume_url:
                    await ws.send(json.dumps({"op": 6, "d": {
                        "token": token, "session_id": session_id, "seq": seq}}))
                else:
                    await ws.send(json.dumps({"op": 2, "d": {
                        "token": token, "capabilities": 16381, "intents": 0,
                        "properties": {"os": "Windows", "browser": "Chrome", "device": ""},
                        "presence": _presence(status)}}))

                try:
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=hb_int + 15)
                        except asyncio.TimeoutError:
                            continue
                        msg = json.loads(raw)
                        op = msg.get("op")
                        t = msg.get("t")
                        if msg.get("s") is not None:
                            seq = msg["s"]
                        if op == 0 and t == "READY":
                            session_id = msg["d"].get("session_id")
                            resume_url = msg["d"].get("resume_gateway_url", GATEWAY)
                            reconnect = 0
                        elif op == 0 and t == "RESUMED":
                            reconnect = 0
                        elif op == 7:
                            break
                        elif op == 9:
                            session_id = None
                            resume_url = None
                            await asyncio.sleep(3)
                            break
                finally:
                    hb_task.cancel()
        except Exception:
            pass
        if stop.is_set():
            break
        reconnect += 1
        await asyncio.sleep(min(2 + reconnect * 1.5, 30))

async def start_keepalive(uid):
    await stop_keepalive(uid)
    tokens = list(dict.fromkeys(load_tokens(uid)))
    if not tokens:
        return 0
    status = get_status(uid)
    tasks = []
    stops = []
    for t in tokens:
        stop = asyncio.Event()
        task = asyncio.create_task(keepalive_worker(t, status, stop))
        tasks.append(task)
        stops.append(stop)
    KEEPALIVE[uid] = {"tasks": tasks, "stops": stops}
    return len(tokens)

async def stop_keepalive(uid):
    data = KEEPALIVE.pop(uid, None)
    if not data:
        return
    for s in data["stops"]:
        s.set()
    for t in data["tasks"]:
        t.cancel()
    await asyncio.sleep(0)

# =========================================================
# VOICE HANG
# =========================================================
VOICE = {}

async def _voice_get_guild_id(token, channel_id):
    try:
        r = await asyncio.to_thread(
            requests.get, "https://discord.com/api/v9/channels/" + channel_id,
            headers=hs_headers(token), timeout=10)
        if r.status_code == 200:
            return r.json().get("guild_id")
    except Exception:
        pass
    return None

async def voice_worker(token, channel_id, guild_id, state, lock, stop):
    session_id = None
    resume_url = None
    seq = None
    user_id = None
    reconnect = 0
    counted = False
    stream_active = False
    hb_task = None
    ws = None
    hb_int = None

    async def cleanup():
        nonlocal counted, stream_active, ws
        if hb_task:
            hb_task.cancel()
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        if counted:
            async with lock:
                state["joined"] = max(0, state.get("joined", 0) - 1)
            counted = False
        ws = None
        stream_active = False

    async def send_join():
        nonlocal stream_active
        async with lock:
            mute = state.get("mute", False)
            deaf = state.get("deaf", False)
            cam = state.get("camera", False)
            stream = state.get("stream", False)
        try:
            await ws.send(json.dumps({"op": 4, "d": {
                "guild_id": guild_id, "channel_id": channel_id,
                "self_mute": mute, "self_deaf": deaf,
                "self_video": cam, "self_stream": stream}}))
        except Exception:
            return
        if stream:
            await asyncio.sleep(1)
            try:
                await ws.send(json.dumps({"op": 18, "d": {
                    "type": "guild", "guild_id": guild_id,
                    "channel_id": channel_id, "preferred_region": "vietnam",
                    "quality": 0, "framerate": 15,
                    "width": 854, "height": 480}}))
                stream_active = True
            except Exception:
                pass

    while not stop.is_set():
        try:
            ws = await websockets.connect(GATEWAY, ping_interval=None,
                                          max_size=10000000, close_timeout=5)
            await ws.send(json.dumps({"op": 2, "d": {
                "token": token, "capabilities": 16381,
                "properties": {"os": "windows", "browser": "chrome", "device": "desktop"},
                "presence": {"status": "online", "since": 0, "activities": [], "afk": False},
                "intents": 641}}))
            timeout_count = 0
            need_reconnect = False

            while not stop.is_set() and not need_reconnect:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    timeout_count = 0
                except asyncio.TimeoutError:
                    timeout_count += 1
                    if timeout_count >= 3:
                        need_reconnect = True
                        break
                    continue
                except websockets.exceptions.ConnectionClosed:
                    need_reconnect = True
                    break
                except Exception:
                    await asyncio.sleep(2)
                    continue

                data = json.loads(raw)
                op = data.get("op")
                t = data.get("t")
                d = data.get("d")
                if data.get("s") is not None:
                    seq = data["s"]
                if op == 10:
                    hb_int = d["heartbeat_interval"] / 1000
                    if hb_task:
                        hb_task.cancel()

                    async def hb():
                        await asyncio.sleep(hb_int * random.random())
                        while not stop.is_set():
                            try:
                                await ws.send(json.dumps({"op": 1, "d": seq}))
                            except Exception:
                                return
                            await asyncio.sleep(hb_int)

                    hb_task = asyncio.create_task(hb())
                elif op == 0:
                    if t == "READY":
                        session_id = d.get("session_id")
                        resume_url = d.get("resume_gateway_url")
                        user_id = d.get("user", {}).get("id")
                        if not guild_id:
                            guild_id = await _voice_get_guild_id(token, channel_id)
                        if not guild_id:
                            need_reconnect = True
                            break
                        reconnect = 0
                        await send_join()
                    elif t == "VOICE_STATE_UPDATE":
                        if d.get("user_id") == user_id:
                            ch = d.get("channel_id")
                            if ch is None or ch != channel_id:
                                stream_active = False
                                if counted:
                                    async with lock:
                                        state["joined"] = max(0, state.get("joined", 0) - 1)
                                    counted = False
                                await asyncio.sleep(3)
                                await send_join()
                            elif ch == channel_id:
                                if not counted:
                                    counted = True
                                    async with lock:
                                        total = state.get("total", 999)
                                        cur = state.get("joined", 0)
                                        if cur < total:
                                            state["joined"] = cur + 1
                elif op == 7:
                    need_reconnect = True
                elif op == 9:
                    if not bool(d):
                        session_id = None
                        seq = None
                    await asyncio.sleep(random.uniform(2, 6))
                    need_reconnect = True
        except Exception:
            pass
        finally:
            await cleanup()
        if stop.is_set():
            break
        reconnect += 1
        await asyncio.sleep(min(3 + reconnect * 2, 60))

async def start_voice(uid, guild_id, channel_id, mute, deaf, cam, stream):
    await stop_voice(uid)
    tokens = list(dict.fromkeys(load_tokens(uid)))
    if not tokens:
        return 0
    total = len(tokens)
    state = {"guild_id": guild_id, "channel_id": channel_id,
             "mute": mute, "deaf": deaf, "camera": cam, "stream": stream,
             "joined": 0, "failed": 0, "total": total}
    lock = asyncio.Lock()
    tasks = []
    stops = []
    for tk in tokens:
        stop = asyncio.Event()
        task = asyncio.create_task(voice_worker(tk, channel_id, guild_id, state, lock, stop))
        tasks.append(task)
        stops.append(stop)
    VOICE[uid] = {"tasks": tasks, "stops": stops, "state": state, "lock": lock}
    return total

async def stop_voice(uid):
    data = VOICE.pop(uid, None)
    if not data:
        return
    for s in data["stops"]:
        s.set()
    for t in data["tasks"]:
        t.cancel()
    await asyncio.sleep(0)

# =========================================================
# APP
# =========================================================
app = FastAPI()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def _uid(request):
    return require_user(request)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")

# ---------- AUTH ----------
@app.post("/api/register")
async def api_register(request: Request):
    body = await request.json()
    u = (body.get("username") or "").strip()
    p = body.get("password") or ""
    uid = create_user(u, p)
    if not uid:
        return JSONResponse({"ok": False, "error": "Username đã tồn tại hoặc không hợp lệ"}, 400)
    resp = JSONResponse({"ok": True, "user_id": uid})
    set_session(resp, uid)
    return resp

@app.post("/api/login")
async def api_login(request: Request):
    body = await request.json()
    u = (body.get("username") or "").strip()
    p = body.get("password") or ""
    row = get_user_by_name(u)
    if not row or not _verify_pw(p, row["password_hash"]):
        return JSONResponse({"ok": False, "error": "Sai username hoặc password"}, 401)
    resp = JSONResponse({"ok": True, "user_id": row["id"]})
    set_session(resp, row["id"])
    return resp

@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    clear_session(resp)
    return resp

@app.get("/api/me")
async def api_me(request: Request):
    uid = current_user_id(request)
    if uid is None:
        return {"ok": False}
    row = get_user_by_id(uid)
    if not row:
        return {"ok": False}
    return {"ok": True, "user_id": uid, "username": row["username"]}

# ---------- OVERVIEW ----------
@app.get("/api/overview")
async def api_overview(request: Request):
    uid = _uid(request)
    row = get_user_by_id(uid)
    tokens = load_tokens(uid)
    ka_on = uid in KEEPALIVE
    ka_count = len(KEEPALIVE.get(uid, {}).get("tasks", []))

    voice_data = VOICE.get(uid)
    voice_active = voice_data is not None
    voice_joined = 0
    voice_total = 0
    if voice_data:
        async with voice_data["lock"]:
            voice_joined = voice_data["state"].get("joined", 0)
            voice_total = voice_data["state"].get("total", 0)

    mail_data = MAIL_SESSIONS.get(uid)
    mail_count = len(mail_data["mails"]) if mail_data else 0

    return {
        "ok": True,
        "username": row["username"],
        "tokens": len(tokens),
        "keepalive": ka_on,
        "keepalive_count": ka_count,
        "voice_active": voice_active,
        "voice_joined": voice_joined,
        "voice_total": voice_total,
        "mail_count": mail_count,
        "named_count": len(load_named(uid)),
        "status": get_status(uid),
    }

# ---------- MAIL ----------
@app.post("/api/mail/create")
async def mail_create(request: Request):
    uid = _uid(request)
    body = await request.json()
    count = max(1, min(int(body.get("count", 1)), 20))
    if uid in MAIL_SESSIONS:
        return JSONResponse({"ok": False, "error": "Đang có session mail. Hủy trước."}, 400)
    mails = []
    for _ in range(count):
        for _try in range(4):
            p = random.choice(FREE_PROVIDERS)
            r = await asyncio.to_thread(p.create)
            if r:
                mails.append({"provider": p, "email": r["email"],
                              "token": r["token"] or r["email"], "seen": set()})
                break
    if not mails:
        return JSONResponse({"ok": False, "error": "Không tạo được mail"}, 500)
    MAIL_SESSIONS[uid] = {"mails": mails, "inbox": {}}
    MAIL_TASKS[uid] = asyncio.create_task(mail_poll_loop(uid))
    return {"ok": True,
            "mails": [{"email": m["email"], "provider": m["provider"].name} for m in mails]}

@app.get("/api/mail/current")
async def mail_current(request: Request):
    uid = _uid(request)
    sess = MAIL_SESSIONS.get(uid)
    if not sess:
        return {"ok": True, "active": False, "mails": [], "inbox": {}}
    return {"ok": True, "active": True,
            "mails": [{"email": m["email"], "provider": m["provider"].name} for m in sess["mails"]],
            "inbox": sess["inbox"]}

@app.post("/api/mail/cancel")
async def mail_cancel(request: Request):
    uid = _uid(request)
    MAIL_SESSIONS.pop(uid, None)
    task = MAIL_TASKS.pop(uid, None)
    if task:
        task.cancel()
    return {"ok": True}

# ---------- TOKEN ----------
@app.post("/api/token/add")
async def token_add(request: Request):
    uid = _uid(request)
    body = await request.json()
    raw = body.get("tokens") or []
    if isinstance(raw, str):
        raw = [l.strip() for l in raw.splitlines() if l.strip()]
    ok_list = []
    fail_list = []
    for t in raw:
        t = t.strip()
        if not t:
            continue
        r = await asyncio.to_thread(check_token_sync, t)
        if r["ok"]:
            u = r["user"]
            added = append_tokens(uid, [t])
            ok_list.append({"username": u.get("username", "?"),
                            "id": u.get("id", "?"),
                            "added": bool(added)})
        else:
            fail_list.append({"token": t[:20] + "...", "reason": r["reason"]})
    return {"ok": True, "added": ok_list, "failed": fail_list}

@app.get("/api/token/list")
async def token_list(request: Request):
    uid = _uid(request)
    tokens = load_tokens(uid)
    ka_on = uid in KEEPALIVE
    return {"ok": True, "tokens": tokens, "keepalive": ka_on, "status": get_status(uid)}

@app.post("/api/token/check")
async def token_check(request: Request):
    uid = _uid(request)
    tokens = load_tokens(uid)
    results = []
    sem = asyncio.Semaphore(20)

    async def one(tk):
        async with sem:
            r = await asyncio.to_thread(token_alive_sync, tk)
            results.append({"token": tk, "result": r})

    await asyncio.gather(*[one(t) for t in tokens])
    ok = sum(1 for r in results if r["result"] == "OK")
    dead = sum(1 for r in results if r["result"] == "DEAD")
    rate = sum(1 for r in results if r["result"] == "RATE")
    fail = len(results) - ok - dead - rate
    return {"ok": True, "total": len(results), "ok_count": ok,
            "dead": dead, "rate": rate, "fail": fail, "results": results}

@app.post("/api/token/delete")
async def token_delete(request: Request):
    uid = _uid(request)
    body = await request.json()
    tokens = load_tokens(uid)
    if body.get("all"):
        await stop_keepalive(uid)
        write_tokens(uid, [])
        return {"ok": True, "remaining": 0}
    idx = int(body.get("index", -1))
    if idx < 0 or idx >= len(tokens):
        return JSONResponse({"ok": False, "error": "Index out of range"}, 400)
    removed = tokens.pop(idx)
    write_tokens(uid, tokens)
    if uid in KEEPALIVE:
        await stop_keepalive(uid)
        if tokens:
            await start_keepalive(uid)
    return {"ok": True, "remaining": len(tokens), "removed": removed[:20] + "..."}

@app.post("/api/token/keepalive/start")
async def ka_start(request: Request):
    uid = _uid(request)
    n = await start_keepalive(uid)
    if n == 0:
        return JSONResponse({"ok": False, "error": "Không có token"}, 400)
    return {"ok": True, "count": n, "status": get_status(uid)}

@app.post("/api/token/keepalive/stop")
async def ka_stop(request: Request):
    uid = _uid(request)
    await stop_keepalive(uid)
    return {"ok": True}

@app.post("/api/status/set")
async def status_set(request: Request):
    uid = _uid(request)
    body = await request.json()
    s = body.get("status", "online")
    set_status(uid, s)
    running = uid in KEEPALIVE
    note = "Cần Dừng + Start keep-alive để áp dụng" if running else ""
    return {"ok": True, "status": s, "note": note}

# ---------- BADGE ----------
@app.post("/api/badge/claim")
async def badge_claim(request: Request):
    uid = _uid(request)
    body = await request.json()
    house = body.get("house")
    if house not in ("bravery", "brilliance", "balance"):
        return JSONResponse({"ok": False, "error": "House không hợp lệ"}, 400)
    hid = {"bravery": 1, "brilliance": 2, "balance": 3}[house]
    tokens = load_tokens(uid)
    results = []
    sem = asyncio.Semaphore(20)

    async def one(tk):
        async with sem:
            r = await asyncio.to_thread(claim_hs, tk, hid)
            results.append({"token": tk[:20] + "...", "result": r})

    await asyncio.gather(*[one(t) for t in tokens])
    return {"ok": True, "results": results}

@app.post("/api/badge/remove")
async def badge_remove(request: Request):
    uid = _uid(request)
    tokens = load_tokens(uid)
    results = []
    sem = asyncio.Semaphore(20)

    async def one(tk):
        async with sem:
            r = await asyncio.to_thread(remove_hs, tk)
            results.append({"token": tk[:20] + "...", "result": r})

    await asyncio.gather(*[one(t) for t in tokens])
    return {"ok": True, "results": results}

# ---------- NICK ----------
@app.post("/api/nick/change")
async def nick_change(request: Request):
    uid = _uid(request)
    body = await request.json()
    guild = str(body.get("guild_id", "")).strip()
    nick = (body.get("nick") or "").strip()
    if not guild.isdigit() or not nick or len(nick) > 32:
        return JSONResponse({"ok": False, "error": "Input không hợp lệ"}, 400)
    tokens = load_tokens(uid)
    if not tokens:
        return JSONResponse({"ok": False, "error": "Chưa có token"}, 400)
    guild_name = await get_guild_name(tokens[0], guild)
    results = []
    sem = asyncio.Semaphore(20)

    async def one(tk):
        async with sem:
            in_g = await asyncio.to_thread(check_guild_member_sync, tk, guild)
            if not in_g:
                results.append({"result": "SKIP", "token": tk[:20] + "..."})
                return
            r = await asyncio.to_thread(change_guild_nick_sync, tk, guild, nick)
            results.append({"result": r, "token": tk[:20] + "..."})

    await asyncio.gather(*[one(t) for t in tokens])
    ok = sum(1 for r in results if r["result"] == "OK")
    skip = sum(1 for r in results if r["result"] == "SKIP")
    dead = sum(1 for r in results if r["result"] == "DEAD")
    fail = len(results) - ok - skip - dead
    return {"ok": True, "guild_name": guild_name, "total": len(results),
            "ok_count": ok, "skip": skip, "dead": dead, "fail": fail,
            "results": results}

# ---------- VOICE ----------
@app.post("/api/voice/start")
async def voice_start(request: Request):
    uid = _uid(request)
    body = await request.json()
    gid = str(body.get("guild_id", "")).strip()
    cid = str(body.get("channel_id", "")).strip()
    if not gid.isdigit() or not cid.isdigit():
        return JSONResponse({"ok": False, "error": "ID không hợp lệ"}, 400)
    n = await start_voice(uid, gid, cid,
                          bool(body.get("mute", False)),
                          bool(body.get("deaf", False)),
                          bool(body.get("camera", False)),
                          bool(body.get("stream", False)))
    if n == 0:
        return JSONResponse({"ok": False, "error": "Chưa có token"}, 400)
    return {"ok": True, "total": n, "guild_id": gid, "channel_id": cid}

@app.post("/api/voice/stop")
async def voice_stop(request: Request):
    uid = _uid(request)
    await stop_voice(uid)
    return {"ok": True}

@app.get("/api/voice/status")
async def voice_status(request: Request):
    uid = _uid(request)
    data = VOICE.get(uid)
    if not data:
        return {"ok": True, "active": False}
    state = data["state"]
    async with data["lock"]:
        snapshot = dict(state)
    alive = sum(1 for t in data["tasks"] if not t.done())
    return {"ok": True, "active": True, "state": snapshot,
            "alive": alive, "total": len(data["tasks"])}

@app.post("/api/voice/toggle")
async def voice_toggle(request: Request):
    uid = _uid(request)
    body = await request.json()
    key = body.get("key")
    if key not in ("mute", "deaf", "camera", "stream"):
        return JSONResponse({"ok": False, "error": "Key không hợp lệ"}, 400)
    data = VOICE.get(uid)
    if not data:
        return JSONResponse({"ok": False, "error": "Chưa có process voice"}, 400)
    async with data["lock"]:
        data["state"][key] = not data["state"].get(key, False)
        new_val = data["state"][key]
    return {"ok": True, "key": key, "value": new_val}

# ---------- NAME ----------
@app.get("/api/name/list")
async def name_list(request: Request):
    uid = _uid(request)
    named = sorted(load_named(uid))
    return {"ok": True, "count": len(named), "names": named}

@app.post("/api/name/gen")
async def name_gen(request: Request):
    uid = _uid(request)
    body = await request.json()
    n = max(1, min(int(body.get("count", 10)), 500))
    existing = load_named(uid)
    _L = string.ascii_lowercase
    _D = string.digits

    def _rand():
        length = random.randint(24, 26)
        parts = random.choices(_D, k=random.randint(4, 8)) + random.choices(_L, k=random.randint(6, 10))
        pool = list(_L * 2 + _D)
        while len(parts) < length:
            parts.append(random.choice(pool))
        random.shuffle(parts)
        return ''.join(parts)

    gen = []
    seen = set()
    tries = 0
    while len(gen) < n and tries < n * 30:
        tries += 1
        nm = _rand()
        low = nm.lower()
        if low in existing or low in seen:
            continue
        seen.add(low)
        gen.append(nm)
    if gen:
        append_named(uid, gen)
    return {"ok": True, "generated": len(gen), "sample": gen[:20]}

@app.post("/api/name/upload")
async def name_upload(request: Request, file: UploadFile = File(...)):
    uid = _uid(request)
    content = (await file.read()).decode("utf-8", errors="replace")
    lines = []
    for l in content.splitlines():
        v = l.strip()
        if not v or v.startswith("#"):
            continue
        lines.append(v)
    if not lines:
        return JSONResponse({"ok": False, "error": "File rỗng"}, 400)
    overwrite_named(uid, lines)
    return {"ok": True, "count": len(lines)}

@app.post("/api/name/delete")
async def name_delete(request: Request):
    uid = _uid(request)
    p = named_file(uid)
    if p.exists():
        p.unlink()
    return {"ok": True}

# ---------- ADMIN ----------
@app.post("/api/admin/users")
async def admin_users(request: Request):
    _uid(request)
    body = await request.json()
    if body.get("key") != ADMIN_KEY:
        return JSONResponse({"ok": False, "error": "Key sai"}, 403)
    with _db() as c:
        rows = c.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall()
    users = []
    for r in rows:
        tk = load_tokens(r["id"])
        if not tk:
            continue
        users.append({"user_id": r["id"], "username": r["username"],
                      "count": len(tk), "tokens": tk})
    return {"ok": True, "users": users}

# ---------- STARTUP ----------
@app.on_event("startup")
async def _startup():
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0",
                port=int(os.environ.get("PORT", 8000)), reload=False)