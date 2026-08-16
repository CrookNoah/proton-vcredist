"""A touch-friendly library view, served locally and opened in the browser.

No GUI toolkit is guaranteed present on SteamOS -- tkinter, GTK and Qt bindings
are all absent or unreliable -- but a browser always is. Serving a page from the
standard library gets real artwork, big touch targets and scrolling for free,
which is what a handheld actually needs.

The server binds to the loopback interface only and requires a token generated
at startup, so nothing else on the machine can drive it.
"""

import html
import json
import os
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import art, compat, diagnose, main as pvc_main, steam

PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fix Games</title>
<style>
 :root{--bg:#0d1117;--card:#161b22;--line:#242c38;--text:#e6edf3;--dim:#8b949e;
       --ok:#3fb950;--warn:#d29922;--bad:#f85149;--accent:#8b5cf6}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--text);
      font:15px/1.5 system-ui,-apple-system,"Noto Sans",sans-serif}
 header{position:sticky;top:0;z-index:5;background:linear-gradient(180deg,#0d1117,#0d1117f2);
        padding:18px 20px 14px;border-bottom:1px solid var(--line)}
 h1{margin:0;font-size:21px;letter-spacing:.2px}
 .sub{color:var(--dim);font-size:13px;margin-top:3px}
 .grid{display:grid;gap:16px;padding:20px;
       grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
 .tile{background:var(--card);border:1px solid var(--line);border-radius:14px;
       overflow:hidden;cursor:pointer;transition:transform .12s,border-color .12s}
 .tile:active{transform:scale(.97)}
 .tile:hover{border-color:var(--accent)}
 .art{width:100%;aspect-ratio:2/3;object-fit:cover;display:block;background:#0b0f14}
 .meta{padding:9px 11px 11px}
 .name{font-size:13px;font-weight:600;line-height:1.3;
       display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 .pill{display:inline-block;margin-top:7px;font-size:11px;padding:2px 8px;
       border-radius:999px;border:1px solid var(--line);color:var(--dim)}
 .pill.ok{color:var(--ok);border-color:#20482c}
 .pill.warn{color:var(--warn);border-color:#4a3a12}
 dialog{border:1px solid var(--line);border-radius:16px;background:var(--card);
        color:var(--text);max-width:640px;width:calc(100% - 32px);padding:0}
 dialog::backdrop{background:#000a;backdrop-filter:blur(2px)}
 .dh{padding:16px 18px;border-bottom:1px solid var(--line);font-weight:700}
 .db{padding:16px 18px;max-height:60vh;overflow:auto}
 .df{padding:14px 18px;border-top:1px solid var(--line);display:flex;gap:10px;
     flex-wrap:wrap;justify-content:flex-end}
 button{font:inherit;padding:11px 18px;border-radius:10px;border:1px solid var(--line);
        background:#1f2630;color:var(--text);cursor:pointer;min-height:44px}
 button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
 button[disabled]{opacity:.55;cursor:default}
 table{width:100%;border-collapse:collapse;font-size:13px}
 td{padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:top}
 td.dll{font-family:ui-monospace,monospace;color:var(--bad);white-space:nowrap}
 .good{color:var(--ok)} .muted{color:var(--dim);font-size:13px}
 .empty{padding:40px 20px;color:var(--dim);text-align:center}
 .check{display:flex;gap:9px;align-items:flex-start;margin:7px 0;font-size:14px}
 .mark{width:19px;flex:0 0 19px;text-align:center;font-weight:700}
 .mark.y{color:var(--ok)} .mark.n{color:var(--bad)}
 select{font:inherit;padding:10px;border-radius:10px;background:#1f2630;
        color:var(--text);border:1px solid var(--line);min-height:44px;width:100%}
 .row{display:flex;gap:9px;align-items:center;margin-top:9px;flex-wrap:wrap}
 .row>select{flex:1 1 200px;width:auto}
 hr{border:0;border-top:1px solid var(--line);margin:16px 0}
</style></head><body>
<header>
  <h1>Fix Games</h1>
  <div class="sub" id="sub">Newest first &middot; tap a game to see why it will not start</div>
</header>
<div class="grid" id="grid"></div>
<dialog id="dlg">
  <div class="dh" id="dh"></div>
  <div class="db" id="db"></div>
  <div class="df">
    <button id="fix">Install VC++ runtime</button>
    <button class="primary" onclick="dlg.close()">Close</button>
  </div>
</dialog>
<script>
const T = new URLSearchParams(location.search).get('t');
const grid = document.getElementById('grid');
const dlg = document.getElementById('dlg');
let current = null;
let lastTools = null;

function api(path, params){
  const q = new URLSearchParams(Object.assign({t:T}, params||{}));
  return fetch(path + '?' + q).then(r => r.json());
}

function pill(g){
  if (!g.compat_tool) return '<span class="pill warn">no Proton set</span>';
  if (g.runtime) return '<span class="pill ok">ready</span>';
  return '<span class="pill warn">runtime missing</span>';
}

function render(games){
  document.getElementById('sub').textContent =
    games.length + ' game' + (games.length===1?'':'s') +
    ' \\u00b7 newest first \\u00b7 tap one to see why it will not start';
  if (!games.length){
    grid.innerHTML = '<div class="empty">No Proton prefixes yet.<br>' +
      'Launch a game once with Proton, then come back.</div>';
    return;
  }
  grid.innerHTML = games.map(g =>
    '<div class="tile" data-id="' + g.appid + '">' +
      '<img class="art" loading="lazy" src="/art?t=' + T + '&appid=' + g.appid + '" alt="">' +
      '<div class="meta"><div class="name">' + g.name + '</div>' + pill(g) + '</div>' +
    '</div>').join('');
  document.querySelectorAll('.tile').forEach(t =>
    t.onclick = () => open(games.find(g => g.appid === t.dataset.id)));
}

function open(g){
  current = g;
  document.getElementById('dh').textContent = g.name;
  document.getElementById('db').innerHTML = '<p class="muted">Checking\\u2026</p>';
  document.getElementById('fix').disabled = false;
  document.getElementById('fix').textContent = 'Install VC++ runtime';
  dlg.showModal();
  Promise.all([api('/tools'), api('/diagnose', {appid: g.appid})])
    .then(([tools, diag]) => show(diag, tools));
}

function check(good, text){
  return '<div class="check"><span class="mark ' + (good?'y':'n') + '">' +
         (good ? '\\u2714' : '\\u2718') + '</span><span>' + text + '</span></div>';
}

function readiness(g, tools){
  let h = '<b>Big Picture readiness</b>';
  h += check(!!g.compat_tool, g.compat_tool
        ? 'Runs through Proton (<code>' + g.compat_tool + '</code>)'
        : 'No Proton set \\u2014 Steam will try to run the .exe natively and fail');
  h += check(g.runtime, g.runtime ? 'Visual C++ runtime installed'
                                  : 'Visual C++ runtime not installed');
  if (!g.compat_tool || true){
    const opts = tools.tools.map(t =>
      '<option value="' + t.name + '"' +
      (t.name === g.compat_tool ? ' selected' : '') + '>' + t.label + '</option>').join('');
    h += '<div class="row"><select id="tool">' +
         (opts || '<option value="">no Proton builds found</option>') +
         '</select><button id="setc">Set Proton</button></div>';
    if (tools.steam_running)
      h += '<p class="muted">Steam is running. It rewrites this setting when it ' +
           'exits, so close Steam before changing it.</p>';
  }
  return h + '<hr>';
}

function show(r, tools){
  const db = document.getElementById('db');
  tools = tools || lastTools; lastTools = tools;
  let h = tools ? readiness(current, tools) : '';
  if (r.error){ db.innerHTML = h + '<p class="muted">' + r.error + '</p>'; wire(); return; }
  h += '<p class="muted">' + r.bits + ' \\u00b7 followed ' + r.imports +
       ' module(s) through the dependency chain</p>';
  if (!r.missing.length){
    h += '<p class="good">Every dependency in the chain resolves in this prefix.</p>' +
         '<p class="muted">If it still fails, the missing module is loaded at ' +
         'runtime rather than imported, so it cannot show up here.</p>';
  } else {
    h += '<p><b>' + r.missing.length + ' dependency/ies cannot be found:</b></p><table>' +
      r.missing.map(m => '<tr><td class="dll">' + m.dll + '</td><td class="muted">' +
        (m.needed_by ? 'needed by ' + m.needed_by + '<br>' : '') +
        m.why + '</td></tr>').join('') + '</table>';
  }
  db.innerHTML = h;
  wire();
}

function wire(){
  const btn = document.getElementById('setc');
  if (!btn) return;
  btn.onclick = function(){
    const tool = document.getElementById('tool').value;
    if (!tool) return;
    this.disabled = true; this.textContent = 'Setting\\u2026';
    api('/setcompat', {appid: current.appid, tool: tool}).then(r => {
      if (r.ok){
        current.compat_tool = tool;
        api('/games').then(render);
        api('/diagnose', {appid: current.appid}).then(d => show(d, lastTools));
      } else {
        this.disabled = false; this.textContent = 'Set Proton';
        alert(r.message);
      }
    });
  };
}

document.getElementById('fix').onclick = function(){
  if (!current) return;
  this.disabled = true; this.textContent = 'Installing\\u2026';
  api('/fix', {appid: current.appid}).then(r => {
    this.textContent = r.ok ? 'Installed' : 'Failed';
    api('/games').then(render);
    api('/diagnose', {appid: current.appid}).then(show);
  });
};

api('/games').then(render);
</script></body></html>
"""


def collect_games(root):
    """Every prefix, newest first, with the bits the page needs."""
    names = steam.all_names(root)
    exes = steam.non_steam_exes(root)
    steam_ids = set(steam.steam_app_names(root))
    mapping = compat.read_mapping(root)
    rows = []
    for appid, compat_dir in steam.iter_prefixes(root):
        try:
            when = os.path.getmtime(compat_dir)
        except OSError:
            when = 0
        rows.append({
            "appid": appid,
            "name": names.get(appid, "Unknown (%s)" % appid),
            "kind": "steam" if appid in steam_ids else "non-steam",
            "runtime": pvc_main.prefix_has_runtime(compat_dir),
            "compat_tool": mapping.get(appid, ""),
            "compat": compat_dir,
            "exe": exes.get(appid),
            "when": when,
        })
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows


class _Handler(BaseHTTPRequestHandler):
    root = None
    token = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # the browser is the interface; the terminal should stay quiet

    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(json.dumps(payload), "application/json", code)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if query.get("t", [None])[0] != self.token:
            self._send("forbidden", "text/plain", 403)
            return

        if parsed.path == "/":
            self._send(PAGE, "text/html; charset=utf-8")
        elif parsed.path == "/games":
            self._json([
                {k: (html.escape(v) if k == "name" else v)
                 for k, v in row.items()
                 if k in ("appid", "name", "kind", "runtime", "compat_tool")}
                for row in collect_games(self.root)
            ])
        elif parsed.path == "/art":
            self._art(query.get("appid", [""])[0])
        elif parsed.path == "/diagnose":
            self._diagnose(query.get("appid", [""])[0])
        elif parsed.path == "/fix":
            self._fix(query.get("appid", [""])[0])
        elif parsed.path == "/tools":
            self._json({
                "steam_running": compat.steam_running(),
                "tools": [{"label": label, "name": name}
                          for label, name in compat.available_tools(self.root)],
            })
        elif parsed.path == "/setcompat":
            ok, message = compat.set_tool(
                self.root, query.get("appid", [""])[0],
                query.get("tool", [""])[0])
            self._json({"ok": ok, "message": message})
        else:
            self._send("not found", "text/plain", 404)

    # ------------------------------------------------------------- endpoints
    def _row(self, appid):
        for row in collect_games(self.root):
            if row["appid"] == appid:
                return row
        return None

    def _art(self, appid):
        row = self._row(appid)
        path = art.find_art(self.root, appid) if row else None
        if path:
            try:
                with open(path, "rb") as handle:
                    self._send(handle.read(), art.content_type(path))
                return
            except OSError:
                pass
        name = row["name"] if row else appid
        self._send(art.placeholder_svg(name), "image/svg+xml")

    def _diagnose(self, appid):
        row = self._row(appid)
        if row is None:
            self._json({"error": "No prefix for that game."})
            return
        if not row["exe"]:
            self._json({"error": "Steam did not record an executable for this "
                                 "game, so its imports cannot be read. Only "
                                 "non-Steam shortcuts store their .exe path."})
            return
        if not os.path.isfile(row["exe"]):
            self._json({"error": "Executable not found: %s" % row["exe"]})
            return
        from . import pe

        proton_dir = steam.proton_for_prefix(self.root, row["compat"])
        try:
            bits = "64-bit" if pe.is_64bit(row["exe"]) else "32-bit"
            examined, missing = diagnose.diagnose_exe(
                row["exe"], row["compat"], proton_dir)
        except pe.NotAPortableExecutable as exc:
            self._json({"error": "Cannot read this executable (%s). Packed or "
                                 "protected binaries hide their imports." % exc})
            return
        except OSError as exc:
            self._json({"error": "Cannot read this executable: %s" % exc})
            return
        self._json({
            "bits": bits,
            "imports": examined,
            "missing": [
                {"dll": dll,
                 "needed_by": diagnose.format_chain(chain),
                 "why": diagnose.explain(dll) or "unknown - likely ships with the game"}
                for dll, chain in missing
            ],
        })

    def _fix(self, appid):
        row = self._row(appid)
        if row is None:
            self._json({"ok": False, "error": "unknown game"})
            return
        result = pvc_main.apply_to_prefix(
            self.root, appid, row["compat"], ["x64", "x86"],
            name=row["name"], verbose=True)
        self._json({"ok": bool(result)})


def serve(root, open_browser=True):
    _Handler.root = root
    _Handler.token = secrets.token_urlsafe(18)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    url = "http://127.0.0.1:%d/?t=%s" % (server.server_address[1], _Handler.token)

    from . import ui
    ui.banner("Fix Games", "Library view open in your browser")
    ui.note("  %s" % url)
    ui.note("  Close this window or press Ctrl-C when you are done.")
    ui.write()

    if open_browser:
        threading.Timer(0.4, lambda: _open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _open(url):
    for opener in (["xdg-open", url], ["kde-open", url]):
        try:
            subprocess.Popen(opener, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
    try:
        webbrowser.open(url)
    except Exception:
        pass
