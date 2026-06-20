"""HPC Pilot Web UI -- FastAPI application for browser-based cluster management."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncGenerator
from typing import Any, cast

# FastAPI is an optional dependency -- ImportError is caught at use sites.
# This module is importable without it; runtime checks raise at the call site.

try:
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
except ImportError:
    FastAPI = None
    HTMLResponse = None
    RedirectResponse = None
    JSONResponse = None
    StreamingResponse = None
    Query = None
    Request = None
    HTTPException = None


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

_SESSION_TTL = 86400  # 24 hours


def _get_auth_secret() -> bytes:
    """Return the HMAC signing key from config or env, generated once."""
    secret = os.environ.get("HPC_PILOT_WEBUI_SECRET")
    if not secret:
        secret = os.environ.get("HPC_PILOT_HOME", "~/.hpc-pilot") + "/webui_secret"
        try:
            secret_path = os.path.expanduser(secret)
            if os.path.exists(secret_path):
                secret = open(secret_path).read().strip()
            else:
                import secrets
                secret = secrets.token_hex(32)
                os.makedirs(os.path.dirname(secret_path), exist_ok=True)
                with open(secret_path, "w") as f:
                    f.write(secret)
                os.chmod(secret_path, 0o600)
        except (OSError, ImportError):
            secret = "hpc-pilot-webui-fallback-secret-do-not-use-in-production"
    return secret.encode("utf-8")


_AUTH_SECRET: bytes | None = None


def _sign_token(data: str) -> str:
    global _AUTH_SECRET  # noqa: PLW0603
    if _AUTH_SECRET is None:
        _AUTH_SECRET = _get_auth_secret()
    sig = hmac.new(_AUTH_SECRET, data.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return f"{data}.{sig}"


def _verify_token(token: str) -> str | None:
    """Verify an HMAC-signed token and return the payload, or None."""
    global _AUTH_SECRET  # noqa: PLW0603
    if _AUTH_SECRET is None:
        _AUTH_SECRET = _get_auth_secret()
    if "." not in token:
        return None
    data, sig = token.rsplit(".", 1)
    expected = hmac.new(_AUTH_SECRET, data.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(expected, sig):
        return None
    parts = data.split(":")
    if len(parts) < 2:
        return None
    expiry = int(parts[1])
    if time.time() > expiry:
        return None
    return parts[0]  # identity


def _make_session_token(identity: str) -> str:
    expiry = int(time.time()) + _SESSION_TTL
    return _sign_token(f"{identity}:{expiry}")


def _get_identity(request: Request) -> str | None:
    """Extract authenticated identity from cookie or Authorization header."""
    token = request.cookies.get("session") or ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
    return _verify_token(token) if token else None


def _require_auth(request: Request) -> str:
    """Require authentication; raise 401 if missing."""
    identity = _get_identity(request)
    if identity is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return identity


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Any:
    """Construct and return the FastAPI application instance.

    Raises ImportError if FastAPI is not installed.
    """
    if FastAPI is None:
        raise ImportError(
            "FastAPI is required to use the web UI. "
            "Install with: pip install 'hpc-pilot[webui]'"
        ) from None

    from fastapi.middleware.cors import CORSMiddleware

    allowed_origins_str = os.environ.get("HPC_PILOT_WEBUI_ORIGINS", "http://127.0.0.1:8000")
    allowed_origins = [o.strip() for o in allowed_origins_str.split(",") if o.strip()]

    app = FastAPI(
        title="HPC Pilot Web UI",
        description="Browser-based HPC cluster management interface",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Chat HTML page (inlined for zero-dependency deployment)
    # ------------------------------------------------------------------

    _CHAT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HPC Pilot -- Chat</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background: #0d1117; color: #c9d1d9; height: 100vh; display: flex; flex-direction: column; }
  header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px; display: flex; align-items: center; gap: 12px; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; color: #58a6ff; }
  #chat { flex: 1; overflow-y: auto; padding: 16px 24px; display: flex; flex-direction: column; gap: 12px; }
  .message { max-width: 80%; padding: 10px 14px; border-radius: 8px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; }
  .user { align-self: flex-end; background: #1f6feb; color: #fff; }
  .agent { align-self: flex-start; background: #21262d; border: 1px solid #30363d; color: #c9d1d9; }
  .tool-call { align-self: flex-start; background: #1c2128; border: 1px solid #30363d; border-left: 3px solid #d29922; border-radius: 6px; padding: 8px 12px; font-family: "SF Mono", Consolas, monospace; font-size: 12px; color: #8b949e; }
  .tool-result { align-self: flex-start; background: #161b22; border: 1px solid #30363d; border-left: 3px solid #3fb950; border-radius: 6px; padding: 8px 12px; font-family: "SF Mono", Consolas, monospace; font-size: 12px; color: #8b949e; white-space: pre-wrap; max-width: 80%; }
  .error { align-self: flex-start; background: #3d1f1f; border: 1px solid #f85149; border-radius: 6px; padding: 8px 12px; color: #f85149; }
  #input-area { display: flex; gap: 8px; padding: 12px 24px; background: #161b22; border-top: 1px solid #30363d; }
  #input { flex: 1; padding: 10px 14px; border: 1px solid #30363d; border-radius: 6px; background: #0d1117; color: #c9d1d9; font-size: 14px; resize: none; outline: none; }
  #input:focus { border-color: #58a6ff; }
  #send-btn { padding: 8px 20px; background: #238636; color: #fff; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; }
  #send-btn:hover { background: #2ea043; }
  #send-btn:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }
  .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid #30363d; border-top-color: #58a6ff; border-radius: 50%; animation: spin 0.8s linear infinite; margin-left: 8px; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .thinking { color: #8b949e; font-style: italic; font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1>HPC Pilot</h1>
  <span style="font-size:12px;color:#8b949e;">AI cluster management</span>
</header>
<div id="chat"></div>
<div id="input-area">
  <textarea id="input" rows="1" placeholder="Ask about cluster status, drain a node, check jobs..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send();}"></textarea>
  <button id="send-btn" onclick="send()">Send</button>
</div>

<script>
  const chat = document.getElementById('chat');
  const input = document.getElementById('input');
  const btn = document.getElementById('send-btn');

  function addMsg(cls, html) {
    const el = document.createElement('div');
    el.className = cls;
    el.innerHTML = html;
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
    return el;
  }

  function addStreamMsg() {
    const el = document.createElement('div');
    el.className = 'message agent';
    chat.appendChild(el);
    return el;
  }

  function setThinking(on) {
    let el = document.querySelector('.thinking');
    if (on) {
      if (!el) {
        el = document.createElement('div');
        el.className = 'thinking';
        el.textContent = 'Thinking...';
        chat.appendChild(el);
      }
    } else {
      if (el) el.remove();
    }
    chat.scrollTop = chat.scrollHeight;
  }

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    btn.disabled = true;

    addMsg('user', escapeHtml(text));

    setThinking(true);

    try {
      const resp = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text}),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({detail: resp.statusText}));
        addMsg('error', 'Error: ' + escapeHtml(errData.detail || resp.statusText));
        btn.disabled = false;
        setThinking(false);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      setThinking(false);
      const msgEl = addStreamMsg();

      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (!payload) continue;
          try {
            const evt = JSON.parse(payload);
            if (evt.type === 'text') {
              msgEl.textContent += evt.content;
            } else if (evt.type === 'tool_call') {
              const tc = document.createElement('div');
              tc.className = 'tool-call';
              tc.textContent = '\\u21d2 ' + evt.name + ' ' + JSON.stringify(evt.args);
              chat.appendChild(tc);
            } else if (evt.type === 'tool_result') {
              const tr = document.createElement('div');
              tr.className = 'tool-result';
              const snippet = evt.content.length > 200 ? evt.content.slice(0, 200) + '...' : evt.content;
              tr.textContent = '\\u21d0 ' + snippet;
              chat.appendChild(tr);
            } else if (evt.type === 'error') {
              addMsg('error', 'Error: ' + escapeHtml(evt.content));
            }
          } catch { /* skip malformed events */ }
        }
        chat.scrollTop = chat.scrollHeight;
      }
    } catch (err) {
      addMsg('error', 'Connection error: ' + escapeHtml(String(err)));
    }

    btn.disabled = false;
    chat.scrollTop = chat.scrollHeight;
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }
</script>
</body>
</html>"""

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/")  # type: ignore[untyped-decorator]
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/chat")

    @app.get("/login")  # type: ignore[untyped-decorator]
    async def login_page(request: Request) -> HTMLResponse:
        identity = _get_identity(request)
        if identity:
            return RedirectResponse(url="/chat")
        html = (
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            "<title>HPC Pilot -- Login</title>"
            '<style>'
            "*,*::before,*::after{box-sizing:border-box}"
            "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
            "margin:0;background:#0d1117;color:#c9d1d9;height:100vh;"
            "display:flex;align-items:center;justify-content:center}"
            ".card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:32px;width:360px}"
            ".card h1{margin:0 0 8px;font-size:20px;color:#58a6ff}"
            ".card p{margin:0 0 20px;font-size:13px;color:#8b949e}"
            "label{display:block;font-size:13px;margin-bottom:6px;color:#8b949e}"
            "input[type=text]{width:100%;padding:10px;border:1px solid #30363d;"
            "border-radius:6px;background:#0d1117;color:#c9d1d9;font-size:14px;outline:none}"
            "input[type=text]:focus{border-color:#58a6ff}"
            "button{width:100%;margin-top:16px;padding:10px;background:#238636;color:#fff;"
            "border:none;border-radius:6px;font-size:14px;cursor:pointer}"
            "button:hover{background:#2ea043}"
            "</style></head><body>"
            '<div class="card">'
            "<h1>HPC Pilot</h1>"
            "<p>Sign in to access cluster management</p>"
            '<form action="/auth/login" method="post">'
            '<label for="identity">Identity</label>'
            '<input type="text" id="identity" name="identity" placeholder="admin" required>'
            '<button type="submit">Sign In</button>'
            "</form></div></body></html>"
        )
        return HTMLResponse(content=html)

    @app.post("/auth/login")  # type: ignore[untyped-decorator]
    async def auth_login(request: Request) -> RedirectResponse:
        """Validate identity and set a session cookie."""
        try:
            body = await request.form()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid form data") from None
        identity = str(body.get("identity", "")).strip()
        if not identity:
            raise HTTPException(status_code=400, detail="Identity is required")
        token = _make_session_token(identity)
        resp = RedirectResponse(url="/chat", status_code=302)
        resp.set_cookie(
            key="session",
            value=token,
            max_age=_SESSION_TTL,
            httponly=True,
            samesite="lax",
            secure=False,
        )
        return resp

    @app.get("/chat", response_class=HTMLResponse)  # type: ignore[untyped-decorator]
    async def chat_page(request: Request) -> HTMLResponse:
        _require_auth(request)
        return HTMLResponse(content=_CHAT_HTML)

    @app.post("/chat")  # type: ignore[untyped-decorator]
    async def chat_endpoint(request: Request) -> StreamingResponse:
        """Accept a user message and return a streaming SSE response."""
        identity = _require_auth(request)

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from None

        message: str = body.get("message", "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")

        import asyncio
        from hpc_pilot.agent import HpcAgent

        agent = HpcAgent(actor=identity)

        async def event_stream() -> AsyncGenerator[str, None]:
            """SSE event generator that streams the agent response.

            NOTE: Current implementation collects all callbacks and flushes
            at the end because ``HpcAgent.run_turn`` is synchronous
            (``subprocess.run`` with ``capture_output=True``).  To make this a
            true streaming endpoint, change ``run_turn`` to use
            ``Popen`` + line-buffered reads from ``hermes chat --json --stream``,
            yielding each SSE event in real time from those callbacks.
            """
            events: list[dict[str, Any]] = []
            tool_events: list[dict[str, Any]] = []
            text_chunks: list[str] = []

            def on_text(chunk: str) -> None:
                text_chunks.append(chunk)
                events.append({"type": "text", "content": chunk})

            def on_tool(name: str, args: dict[str, Any]) -> None:
                tool_events.append({"type": "tool_call", "name": name, "args": args})
                events.append({"type": "tool_call", "name": name, "args": args})

            def on_result(name: str, result: str) -> None:
                tool_events.append({"type": "tool_result", "content": result})
                events.append({"type": "tool_result", "content": result})

            try:
                await asyncio.to_thread(
                    agent.run_turn, message, [],
                    on_text=on_text, on_tool=on_tool, on_result=on_result,
                )
            except Exception as exc:
                events.append({"type": "error", "content": str(exc)})

            # Flush all events
            for evt in events:
                yield f"data: {json.dumps(evt)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/audit")  # type: ignore[untyped-decorator]
    async def audit_log(
        request: Request,
        actor: str | None = Query(None),
        tool: str | None = Query(None),
        role: str | None = Query(None),
        start: float | None = Query(None, alias="start_ts"),
        end: float | None = Query(None, alias="end_ts"),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Paginated audit log viewer with optional filters."""
        _require_auth(request)
        from hpc_pilot.paths import audit_log_path as _audit_log_path

        records: list[dict[str, Any]] = []
        path = _audit_log_path()
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Apply filters
                    if actor and rec.get("actor") != actor:
                        continue
                    if tool and rec.get("tool") != tool:
                        continue
                    if role and rec.get("role") != role:
                        continue
                    ts = rec.get("ts", 0)
                    if start is not None and ts < start:
                        continue
                    if end is not None and ts > end:
                        continue

                    records.append(rec)

        total = len(records)
        page = records[offset: offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "records": page,
        }

    @app.get("/skills")  # type: ignore[untyped-decorator]
    async def list_skills(request: Request) -> dict[str, Any]:
        _require_auth(request)
        """List available skills."""
        from hpc_pilot.skills.runner import list_skills as _list_skills

        return {"skills": _list_skills()}

    @app.get("/skills/{name}")  # type: ignore[untyped-decorator]
    async def skill_detail(name: str, request: Request) -> JSONResponse:
        _require_auth(request)
        """View a skill's YAML definition."""
        from hpc_pilot.skills.runner import hpc_skill_describe

        try:
            definition = hpc_skill_describe(name)
            return JSONResponse(content={"name": name, "definition": definition})
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Skill not found: {name!r}") from None

    @app.get("/approvals")  # type: ignore[untyped-decorator]
    async def list_approvals(request: Request) -> dict[str, Any]:
        _require_auth(request)
        """List pending approvals."""
        from hpc_pilot.approvals import list_pending

        pending = list_pending()
        return {
            "pending": [
                {
                    "id": req.id,
                    "tool": req.tool,
                    "args": req.args,
                    "requester_actor": req.requester_actor,
                    "requester_role": req.requester_role,
                    "cluster": req.cluster,
                    "risk_summary": req.risk_summary,
                    "created_at": req.created_at,
                    "expires_at": req.expires_at,
                    "status": req.status,
                }
                for req in pending
            ]
        }

    @app.post("/approvals/{approval_id}/approve")  # type: ignore[untyped-decorator]
    async def approve_approval(approval_id: str, request: Request) -> dict[str, Any]:
        """Approve a pending approval request."""
        identity = _require_auth(request)
        from hpc_pilot.approvals import approve_request

        try:
            req = approve_request(approval_id, approver=identity)
            return {"status": "approved", "id": req.id}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/approvals/{approval_id}/reject")  # type: ignore[untyped-decorator]
    async def reject_approval(approval_id: str, request: Request) -> dict[str, Any]:
        """Reject a pending approval request."""
        identity = _require_auth(request)
        from hpc_pilot.approvals import reject_request

        try:
            req = reject_request(approval_id, approver=identity)
            return {"status": "rejected", "id": req.id}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/clusters")  # type: ignore[untyped-decorator]
    async def clusters_list(request: Request) -> dict[str, Any]:
        _require_auth(request)
        """List configured clusters."""
        from hpc_pilot.clusters import list_clusters as _list_clusters

        return {"clusters": _list_clusters()}

    @app.get("/clusters/{name}/health")  # type: ignore[untyped-decorator]
    async def cluster_health(name: str, request: Request) -> dict[str, Any]:
        _require_auth(request)
        """Run a comprehensive health check on a cluster."""
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import get_role

        role = get_role()
        actor = os.environ.get("USER", "webui")
        try:
            result = invoke(
                "hpc_cluster_health_check",
                {"cluster": name},
                role=role,
                actor=actor,
            )
            # Parse the JSON result if possible
            try:
                data = cast(dict[str, Any], json.loads(result))
                return data
            except (json.JSONDecodeError, TypeError):
                return {"raw": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @app.get("/metrics")  # type: ignore[untyped-decorator]
    async def metrics() -> str:
        """Prometheus /metrics endpoint.

        Requires ``prometheus_client`` (optional dependency).
        Returns 404 with a hint if the library is not installed.
        """
        try:
            from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
            from hpc_pilot.metrics import REGISTRY
            data = generate_latest(REGISTRY)
            return data.decode("utf-8")
        except ImportError:
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                "# prometheus_client not installed. Run: pip install prometheus-client\n",
                status_code=404,
            )

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def run_webui(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Launch the web UI via uvicorn (required at runtime).

    Raises ImportError if uvicorn is not installed.
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "uvicorn is required to serve the web UI. "
            "Install with: pip install 'hpc-pilot[webui]'"
        ) from None

    # Ensure layout exists before starting
    from hpc_pilot.paths import ensure_layout
    ensure_layout()

    app = create_app()
    print(f"HPC Pilot Web UI starting on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
