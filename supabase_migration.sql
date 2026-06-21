-- ============================================================
-- Sheria Research Agent — Supabase Schema Migration
-- Execute this script in the Supabase SQL Editor to provision
-- the required tables for cloud-backed research persistence.
-- ============================================================

-- Parent Research Session Mapping Table
CREATE TABLE IF NOT EXISTS public.research_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- Core ReAct Operational Logging State Table
CREATE TABLE IF NOT EXISTS public.research_states (
    run_id UUID PRIMARY KEY,
    session_id TEXT REFERENCES public.research_sessions(session_id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    query_intent TEXT NOT NULL DEFAULT 'standard',
    deep_research_mode BOOLEAN NOT NULL DEFAULT FALSE,
    basis_strength TEXT NOT NULL DEFAULT 'unknown',
    final_answer TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- Chat Message History (conversation context for drafting)
CREATE TABLE IF NOT EXISTS public.research_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT REFERENCES public.research_sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON public.research_messages(session_id, id);

-- Grounded Evidence Ledger Storage
CREATE TABLE IF NOT EXISTS public.evidence_ledger (
    card_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES public.research_states(run_id) ON DELETE CASCADE,
    source_title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    authority_level TEXT NOT NULL,
    excerpt TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- Document Artifact Manifest Compilation Audits
CREATE TABLE IF NOT EXISTS public.artifact_manifests (
    artifact_id TEXT PRIMARY KEY,
    run_id UUID REFERENCES public.research_states(run_id) ON DELETE CASCADE,
    verification_passed BOOLEAN NOT NULL,
    generated_outputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    warning TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- ============================================================
-- Agent Execution History (turn-level short-term memory)
-- Maps directly to LoopTurnRecord from the in-memory
-- ObservabilityLedger.  Persisted so the agent can recover
-- its short-term memory across stateless HTTP requests and
-- survive process crashes.
-- ============================================================
CREATE TABLE IF NOT EXISTS public.agent_execution_history (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES public.research_sessions(session_id) ON DELETE CASCADE,
    turn_number INT NOT NULL,
    selected_action VARCHAR(100) NOT NULL,
    exact_tool_input JSONB,
    raw_tool_status VARCHAR(100),
    normalized_observation TEXT,
    error_details TEXT,
    source_ids_affected TEXT[],
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,

    -- Ensure chronological uniqueness per session
    UNIQUE (session_id, turn_number)
);

CREATE INDEX IF NOT EXISTS idx_exec_history_session_turn
    ON public.agent_execution_history (session_id, turn_number DESC);
