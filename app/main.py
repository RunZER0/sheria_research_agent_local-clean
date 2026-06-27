import base64
import json
import os
import re
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Body, HTTPException, Header, Depends
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
