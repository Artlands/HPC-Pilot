"""HPC Pilot Web UI -- FastAPI application for browser-based cluster management."""
from __future__ import annotations

import json
import os
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

    app = FastAPI(
        title="HPC Pilot Web UI",
        description="Browser-based HPC cluster management interface",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
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

    @app.get("/chat", response_class=HTMLResponse)  # type: ignore[untyped-decorator]
    async def chat_page() -> HTMLResponse:
        return HTMLResponse(content=_CHAT_HTML)

    @app.post("/chat")  # type: ignore[untyped-decorator]
    async def chat_endpoint(request: Request) -> StreamingResponse:
        """Accept a user message and return a streaming SSE response."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from None

        message: str = body.get("message", "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")

        from hpc_pilot.agent import HpcAgent

        agent = HpcAgent()

        async def event_stream() -> AsyncGenerator[str, None]:
            """SSE event generator that streams the agent response."""
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
                agent.run_turn(
                    message,
                    history=[],
                    on_text=on_text,
                    on_tool=on_tool,
                    on_result=on_result,
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
        actor: str | None = Query(None),
        tool: str | None = Query(None),
        role: str | None = Query(None),
        start: float | None = Query(None, alias="start_ts"),
        end: float | None = Query(None, alias="end_ts"),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Paginated audit log viewer with optional filters."""
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
    async def list_skills() -> dict[str, Any]:
        """List available skills."""
        from hpc_pilot.skills.runner import list_skills as _list_skills

        return {"skills": _list_skills()}

    @app.get("/skills/{name}")  # type: ignore[untyped-decorator]
    async def skill_detail(name: str) -> JSONResponse:
        """View a skill's YAML definition."""
        from hpc_pilot.skills.runner import hpc_skill_describe

        try:
            definition = hpc_skill_describe(name)
            return JSONResponse(content={"name": name, "definition": definition})
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Skill not found: {name!r}") from None

    @app.get("/approvals")  # type: ignore[untyped-decorator]
    async def list_approvals() -> dict[str, Any]:
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
    async def approve_approval(approval_id: str) -> dict[str, Any]:
        """Approve a pending approval request."""
        from hpc_pilot.approvals import approve_request

        try:
            req = approve_request(approval_id, approver="webui")
            return {"status": "approved", "id": req.id}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/approvals/{approval_id}/reject")  # type: ignore[untyped-decorator]
    async def reject_approval(approval_id: str) -> dict[str, Any]:
        """Reject a pending approval request."""
        from hpc_pilot.approvals import reject_request

        try:
            req = reject_request(approval_id, approver="webui")
            return {"status": "rejected", "id": req.id}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/clusters")  # type: ignore[untyped-decorator]
    async def clusters_list() -> dict[str, Any]:
        """List configured clusters."""
        from hpc_pilot.clusters import list_clusters as _list_clusters

        return {"clusters": _list_clusters()}

    @app.get("/clusters/{name}/health")  # type: ignore[untyped-decorator]
    async def cluster_health(name: str) -> dict[str, Any]:
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
