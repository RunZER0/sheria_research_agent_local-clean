-- ============================================================
-- Orbit Legal IDE — Workspace Sync Pipeline Schema
-- Run this in Supabase SQL Editor AFTER the base migration.
-- ============================================================

-- Per-user storage quota (atomic tracking)
CREATE TABLE IF NOT EXISTS public.user_quotas (
    user_id TEXT PRIMARY KEY,              -- session_id or user identifier
    tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'pro')),
    max_bytes BIGINT NOT NULL DEFAULT 52428800,  -- 50 MB free, 100 MB pro
    used_bytes BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Workspace manifest: one row per synced file
CREATE TABLE IF NOT EXISTS public.workspace_manifest (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES public.user_quotas(user_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,               -- relative path within workspace, e.g. "judgments/njoroge_v_kcb.txt"
    file_name TEXT NOT NULL,
    file_size BIGINT NOT NULL,             -- original file size in bytes
    file_hash TEXT NOT NULL,               -- SHA-256 of original file content for dedup
    last_modified BIGINT NOT NULL,         -- file.lastModified timestamp
    markdown_size BIGINT NOT NULL DEFAULT 0,  -- size of extracted markdown in bytes
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, file_path)
);

-- Workspace chunks: one row per file, stores the markdown text
CREATE TABLE IF NOT EXISTS public.workspace_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES public.user_quotas(user_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,               -- matches workspace_manifest.file_path
    chunk_index INT NOT NULL DEFAULT 0,
    chunk_text TEXT NOT NULL,              -- markdown text for this chunk
    char_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, file_path, chunk_index)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ws_manifest_user ON public.workspace_manifest(user_id);
CREATE INDEX IF NOT EXISTS idx_ws_chunks_user_file ON public.workspace_chunks(user_id, file_path);
CREATE INDEX IF NOT EXISTS idx_ws_chunks_fulltext ON public.workspace_chunks USING gin(to_tsvector('english', chunk_text));

-- ============================================================
-- Auto-initialize quota for new users (called by application)
-- ============================================================
CREATE OR REPLACE FUNCTION public.ensure_user_quota(p_user_id TEXT, p_tier TEXT DEFAULT 'free')
RETURNS TABLE(user_id TEXT, tier TEXT, max_bytes BIGINT, used_bytes BIGINT)
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO public.user_quotas (user_id, tier, max_bytes)
    VALUES (p_user_id, p_tier, CASE WHEN p_tier = 'pro' THEN 104857600 ELSE 52428800 END)
    ON CONFLICT (user_id) DO NOTHING;
    RETURN QUERY SELECT q.user_id, q.tier, q.max_bytes, q.used_bytes
                 FROM public.user_quotas q WHERE q.user_id = p_user_id;
END;
$$;

-- ============================================================
-- Atomic quota check + update (called per-file before upload)
-- Returns TRUE if quota allows the new bytes
-- ============================================================
CREATE OR REPLACE FUNCTION public.check_and_reserve_quota(
    p_user_id TEXT,
    p_new_bytes BIGINT
) RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_used BIGINT;
    v_max BIGINT;
BEGIN
    SELECT used_bytes, max_bytes INTO v_used, v_max
    FROM public.user_quotas WHERE user_id = p_user_id
    FOR UPDATE;  -- row lock to prevent race conditions

    IF v_used IS NULL THEN
        RETURN false;
    END IF;

    IF (v_used + p_new_bytes) > v_max THEN
        RETURN false;
    END IF;

    UPDATE public.user_quotas
    SET used_bytes = used_bytes + p_new_bytes,
        updated_at = NOW()
    WHERE user_id = p_user_id;

    RETURN true;
END;
$$;

-- ============================================================
-- Release quota (when a file is deleted or replaced)
-- ============================================================
CREATE OR REPLACE FUNCTION public.release_quota(
    p_user_id TEXT,
    p_bytes BIGINT
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE public.user_quotas
    SET used_bytes = GREATEST(0, used_bytes - p_bytes),
        updated_at = NOW()
    WHERE user_id = p_user_id;
END;
$$;

-- ============================================================
-- Compare local manifest against server; return diff
-- Expects JSON array of {file_path, file_name, file_size, file_hash, last_modified}
-- ============================================================
CREATE OR REPLACE FUNCTION public.compute_workspace_diff(
    p_user_id TEXT,
    p_local_manifest JSONB
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_result JSONB;
BEGIN
    WITH local_files AS (
        SELECT
            elem->>'file_path' AS file_path,
            elem->>'file_name' AS file_name,
            (elem->>'file_size')::BIGINT AS file_size,
            elem->>'file_hash' AS file_hash,
            (elem->>'last_modified')::BIGINT AS last_modified
        FROM jsonb_array_elements(p_local_manifest) AS elem
    ),
    server_files AS (
        SELECT file_path, file_hash, last_modified, markdown_size
        FROM public.workspace_manifest
        WHERE user_id = p_user_id
    ),
    to_add AS (
        SELECT l.* FROM local_files l
        LEFT JOIN server_files s ON l.file_path = s.file_path
        WHERE s.file_path IS NULL
    ),
    to_update AS (
        SELECT l.* FROM local_files l
        JOIN server_files s ON l.file_path = s.file_path
        WHERE l.file_hash <> s.file_hash OR l.last_modified > s.last_modified
    ),
    to_delete AS (
        SELECT s.file_path FROM server_files s
        LEFT JOIN local_files l ON s.file_path = l.file_path
        WHERE l.file_path IS NULL
    )
    SELECT jsonb_build_object(
        'files_to_add', COALESCE((SELECT jsonb_agg(to_add.*) FROM to_add), '[]'::jsonb),
        'files_to_update', COALESCE((SELECT jsonb_agg(to_update.*) FROM to_update), '[]'::jsonb),
        'files_to_delete', COALESCE((SELECT jsonb_agg(file_path) FROM to_delete), '[]'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;
