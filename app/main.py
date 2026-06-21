import base64
import io
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Body, File, Form, UploadFile, HTTPException, Header, Depends
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .research_controller import ResearchController
from .agents.document_revision_agent import DocumentRevisionAgent
from .config import get_settings
from .schemas import ChatRequest
from .schemas.document_export import DocumentExportRequest, OutputFormat
from .services.deepseek_json_model import DeepSeekJSONModel
from .services.document_export_service import DocumentExportService
from .skills.document_export_skill import DocumentExportSkill, wants_document_export
from .store import SupabaseStore
from .frontend_event_adapter import to_frontend_event

load_dotenv()

settings = get_settings()

# ── Auth: validate Supabase JWT ─────────────────────────────────────────

async def get_current_user(authorization: str = Header("", alias="Authorization")):
    if not authorization or not authorization.startswith("Bearer "):
        return "anonymous"
    token = authorization[7:]
    if not token.strip():
        return "anonymous"
    try:
        from supabase import create_client
        sb = create_client(settings.supabase_url, settings.supabase_anon_key)
        user_resp = sb.auth.get_user(token)
        if user_resp and user_resp.user:
            return user_resp.user.id
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired auth token")
    raise HTTPException(status_code=401, detail="Invalid auth token")

# Production store - requires SUPABASE_URL and SUPABASE_ANON_KEY.
try:
    store = SupabaseStore(settings)
except ValueError as exc:
    import warnings
    warnings.warn(str(exc) + " — research sessions will NOT be persisted.")
    store = None

app = FastAPI(title="Orbit Legal IDE Agent")

# Allow simple local development from different ports (vite/dev server).
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create the generated artifacts directory and mount it for download
GENERATED_DIR = os.path.join(os.path.dirname(__file__), "..", "artifacts", "generated")
os.makedirs(GENERATED_DIR, exist_ok=True)

# Create the file upload directory
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Initialize document export service and skill with real LLM revision callback
try:
    deepseek_document_model = DeepSeekJSONModel()

    document_revision_agent = DocumentRevisionAgent(
        model=deepseek_document_model,
        max_repair_attempts=3,
    )

    document_export_service = DocumentExportService(
        revision_callback=document_revision_agent.revise,
    )
except RuntimeError:
    # DEEPSEEK_API_KEY not configured — run without revision callback
    document_export_service = DocumentExportService()

document_export_skill = DocumentExportSkill(export_service=document_export_service)

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/static/artifacts", StaticFiles(directory=GENERATED_DIR), name="artifacts")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health():
    return {
        "ok": store is not None,
        "model": settings.deepseek_model,
        "thinking": settings.deepseek_thinking,
        "has_deepseek_key": bool(settings.deepseek_api_key),
        "has_brave_key": bool(settings.brave_api_key),
        "has_groq_key": bool(settings.groq_api_key or settings.deepseek_api_key),
        "has_supabase": bool(settings.supabase_url and settings.supabase_anon_key),
    }


@app.get("/api/config")
async def client_config():
    """Public config for the frontend (safe to expose)."""
    return {
        "supabase_url": settings.supabase_url or "",
        "supabase_anon_key": settings.supabase_anon_key or "",
    }


@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file and return metadata for use in chat requests."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    file_id = f"file_{uuid4().hex}"
    safe_name = Path(file.filename).name
    target = Path(UPLOAD_DIR) / f"{file_id}_{safe_name}"

    with target.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    size_bytes = target.stat().st_size

    return {
        "id": file_id,
        "filename": safe_name,
        "mime_type": file.content_type or "application/octet-stream",
        "size_bytes": size_bytes,
    }


@app.post("/api/workspace/file-content")
async def read_workspace_file(request: dict):
    """
    Receive file content from the frontend and extract text.
    Accepts JSON: { "path": "...", "kind": "docx|pdf", "data": "<base64>" }
    Returns: { "path": "...", "content": "extracted text..." }
    """
    path = request.get("path", "")
    kind = request.get("kind", "")
    data = request.get("data", "")

    if not path or not data:
        return {"path": path, "error": "Missing path or data"}

    import base64
    import io

    try:
        raw = base64.b64decode(data)
    except Exception as e:
        return {"path": path, "error": f"Base64 decode failed: {e}"}

    content = ""

    if kind == "docx":
        try:
            import docx
            doc = docx.Document(io.BytesIO(raw))
            content = "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            content = f"[DOCX parse error: {e}]"
    elif kind == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            pages = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages.append(t)
            content = "\n\n".join(pages)
            if not content.strip():
                # Try alternate extraction
                content = f"[PDF: {len(reader.pages)} pages, no extractable text]"
        except Exception as e:
            content = f"[PDF parse error: {e}]"
    else:
        try:
            content = raw.decode("utf-8", errors="replace")
        except Exception as e:
            content = f"[Decode error: {e}]"

    return {"path": path, "content": content}


@app.post("/api/audio/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribes an uploaded audio file using Groq's Whisper API."""
    groq_key = settings.groq_api_key or settings.deepseek_api_key
    if not groq_key:
        return {"error": "Groq transcription API credential missing. Set GROQ_API_KEY or DEEPSEEK_API_KEY in .env."}
        
    try:
        file_bytes = await file.read()
        files = {"file": (file.filename, file_bytes, file.content_type)}
        data = {"model": "whisper-large-v3-turbo"}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openapi/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files=files,
                data=data,
                timeout=30.0
            )
            
        if response.status_code == 200:
            return {"transcript": response.json().get("text", "")}
        return {"error": f"Groq API error: {response.text}"}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/documents/export")
async def export_document(payload: DocumentExportRequest = Body(...)):
    result = await document_export_service.export(payload)
    return result.model_dump(mode="json")


@app.get("/api/documents/download/{document_id}/{filename}")
async def download_document(document_id: str, filename: str):
    base = Path("data/artifacts/generated").resolve()
    target = (base / document_id / filename).resolve()

    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(target),
        filename=filename,
        media_type="application/octet-stream",
    )


@app.post("/api/chat")
async def chat(request: ChatRequest):
    query = request.normalized_query
    if not query:
        return StreamingResponse(
            iter([f"data: {json.dumps({'type': 'error', 'text': 'Message is required.'}, ensure_ascii=False)}\n\n"]),
            media_type="text/event-stream",
        )

    async def stream_events():
        if store is None:
            yield json.dumps({
                "type": "error",
                "label": "Configuration Error",
                "summary": "Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY in .env.",
            }, ensure_ascii=False) + "\n\n"
            return

        try:
            if settings.sheria_research_engine in ("react_v2", "sheria_react"):
                # New ReAct architecture with real-time SSE streaming
                if settings.sheria_research_engine == "sheria_react":
                    # Policy-led ReAct agent runtime
                    controller = ResearchController(settings, store)
                    async for item in controller.run(request):
                        frontend_event = to_frontend_event(item)
                        if frontend_event is not None:
                            yield f"data: {json.dumps(frontend_event, ensure_ascii=False)}\n\n"
                else:
                    # react_v2 bounded ReAct architecture
                    import asyncio
                    from .controllers.research_controller import ResearchController as ReactV2Controller
                    from .deepseek_client import DeepSeekClient
                    from .brave_search import BraveSearchClient
                    from .events import EventEmitter

                    llm = DeepSeekClient(settings)
                    brave = BraveSearchClient(settings)

                    event_queue: asyncio.Queue[dict] = asyncio.Queue()
                    emitter = EventEmitter(queue=event_queue)
                    controller = ReactV2Controller(
                        llm_client=llm,
                        settings=settings,
                        brave_client=brave,
                    )
                    controller.set_document_export_skill(document_export_skill)

                    # Run the controller in a background task
                    async def run_and_signal():
                        try:
                            return await controller.run_loop(
                                user_prompt=query,
                                emitter=emitter,
                            )
                        finally:
                            await event_queue.put(None)

                    runner_task = asyncio.create_task(run_and_signal())

                    # Stream events in real-time as they arrive on the queue
                    while True:
                        event_data = await event_queue.get()
                        if event_data is None:
                            break
                        frontend_event = to_frontend_event(event_data)
                        if frontend_event is not None:
                            yield f"data: {json.dumps(frontend_event, ensure_ascii=False)}\n\n"

                    await runner_task
            else:
                # Legacy research controller
                from .research_controller import ResearchController as LegacyResearchController
                controller = LegacyResearchController(settings, store)
                final_answer_parts: list[str] = []
                async for item in controller.run(request):
                    # Accumulate answer text for potential document export
                    if item.get("type") == "answer_token":
                        token = (
                            item.get("payload", {}).get("token")
                            or item.get("text")
                            or ""
                        )
                        final_answer_parts.append(token)

                    frontend_event = to_frontend_event(item)
                    if frontend_event is not None:
                        yield f"data: {json.dumps(frontend_event, ensure_ascii=False)}\n\n"

                # After pipeline finishes, check if user wants document export
                if wants_document_export(query):
                    final_answer = "".join(final_answer_parts)
                    if final_answer.strip():
                        try:
                            export_result = await document_export_skill.run(
                                user_request=query,
                                grounded_answer=final_answer,
                            )
                            export_event = {
                                "type": "document_export_finished",
                                "document_id": export_result.document_id,
                                "ok": export_result.ok,
                                "payload": {
                                    "docx_path": export_result.docx_path,
                                    "pdf_path": export_result.pdf_path,
                                    "manifest_path": export_result.artifact_manifest_path,
                                    "document_id": export_result.document_id,
                                    "warnings": export_result.warnings,
                                    "constraint_report": (
                                        export_result.constraint_report.model_dump(mode="json")
                                        if export_result.constraint_report
                                        else None
                                    ),
                                },
                            }
                            frontend_event = to_frontend_event(export_event)
                            if frontend_event is not None:
                                yield f"data: {json.dumps(frontend_event, ensure_ascii=False)}\n\n"
                        except Exception as export_err:
                            error_event = {
                                "type": "error",
                                "summary": f"Document export failed: {export_err}",
                            }
                            frontend_event = to_frontend_event(error_event)
                            if frontend_event is not None:
                                yield f"data: {json.dumps(frontend_event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            import logging
            logging.getLogger("sheria.main").exception(
                "Research run failed: %s", exc
            )
            payload = {
                "type": "error",
                "label": "Runtime error",
                "summary": "An internal error occurred during the run.",
                "payload": {
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
            }
            frontend_event = to_frontend_event(payload)
            if frontend_event is not None:
                yield f"data: {json.dumps(frontend_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream_events(), media_type="text/event-stream")


@app.post("/api/conversations/messages")
async def save_message(session_id: str = "default", role: str = "user", content: str = ""):
    if store and content.strip():
        store.add_message(session_id, role, content)
    return {"ok": True}


@app.get("/api/conversations/sessions")
async def list_sessions():
    if not store:
        return []
    # Use store abstraction; SupabaseStore implements list_sessions and LocalFileStore does too.
    try:
        return store.list_sessions(limit=20)
    except Exception:
        return []


@app.get("/api/conversations/messages/{session_id}")
async def get_messages(session_id: str, limit: int = 20):
    if not store:
        return []
    msgs = store.recent_messages(session_id, limit=limit)
    return msgs


# ── Workspace Sync Pipeline (Local-to-Cloud Markdown) ──────────────────

async def _workspace_store():
    """Return store or raise 503."""
    if store is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Supabase not configured")
    return store


@app.post("/api/workspace/sync")
async def workspace_sync(request: dict, _auth_user: str = Depends(get_current_user)):
    try:
        s = await _workspace_store()
    except Exception as e:
        return {"error": str(e)}

    user_id = _auth_user if _auth_user != "anonymous" else request.get("user_id", "default")
    tier = request.get("tier", "free")
    try:
        s.ensure_user_quota(user_id, tier)
        diff = s.compute_workspace_diff(user_id, request.get("local_manifest", []))
        quota = s.get_user_quota(user_id)
        diff["quota"] = quota
        return diff
    except Exception as e:
        return {"error": f"Sync failed: {e}. Run the workspace migration SQL in Supabase."}


@app.post("/api/workspace/upload-chunks")
async def workspace_upload_chunks(request: dict, _auth_user: str = Depends(get_current_user)):
    try:
        s = await _workspace_store()
    except Exception as e:
        return {"error": str(e)}

    user_id = _auth_user if _auth_user != "anonymous" else request.get("user_id", "default")
    file_info = request.get("file", {})
    file_path = file_info.get("file_path", "")
    chunks = request.get("chunks", [])
    markdown_size = request.get("markdown_size", sum(len(c) for c in chunks))

    if not file_path or not chunks:
        return {"error": "Missing file_path or chunks"}
    try:
        if not s.check_and_reserve_quota(user_id, markdown_size):
            q = s.get_user_quota(user_id)
            return {
                "error": "Quota exceeded",
                "quota": q,
                "needed": markdown_size,
                "available": q["max_bytes"] - q["used_bytes"],
            }
        s.delete_workspace_file(user_id, file_path)
        s.upsert_workspace_file(
            user_id=user_id, file_path=file_path,
            file_name=file_info.get("file_name", ""),
            file_size=file_info.get("file_size", 0),
            file_hash=file_info.get("file_hash", ""),
            last_modified=file_info.get("last_modified", 0),
            markdown_size=markdown_size,
        )
        total_chars = s.insert_workspace_chunks(user_id, file_path, chunks)
        return {"ok": True, "markdown_size": total_chars, "chunks_uploaded": len(chunks)}
    except Exception as e:
        return {"error": f"Upload failed: {e}. Run the workspace migration SQL in Supabase."}


@app.post("/api/workspace/delete")
async def workspace_delete_file(request: dict, _auth_user: str = Depends(get_current_user)):
    try:
        s = await _workspace_store()
    except Exception as e:
        return {"error": str(e)}
    user_id = _auth_user if _auth_user != "anonymous" else request.get("user_id", "default")
    file_path = request.get("file_path", "")
    if not file_path:
        return {"error": "Missing file_path"}
    try:
        s.delete_workspace_file(user_id, file_path)
        return {"ok": True, "file_path": file_path}
    except Exception as e:
        return {"error": f"Delete failed: {e}"}


@app.get("/api/workspace/manifest")
async def workspace_manifest_list(_auth_user: str = Depends(get_current_user), user_id: str = "default"):
    uid = _auth_user if _auth_user != "anonymous" else user_id
    if store is None:
        return []
    try:
        return store.list_workspace_files(uid)
    except Exception:
        return []


@app.get("/api/workspace/quota")
async def workspace_quota(_auth_user: str = Depends(get_current_user), user_id: str = "default"):
    uid = _auth_user if _auth_user != "anonymous" else user_id
    if store is None:
        return {"tier": "free", "max_bytes": 52428800, "used_bytes": 0}
    try:
        return store.get_user_quota(uid)
    except Exception:
        return {"tier": "free", "max_bytes": 52428800, "used_bytes": 0}
