-- Run once through Supabase SQL Editor or the local setup script.
-- Application credentials live in the backend; browser roles have no direct access.
CREATE TABLE IF NOT EXISTS public.chart_studio_documents (
    document_id text PRIMARY KEY,
    manifest jsonb NOT NULL,
    summary jsonb NOT NULL,
    assets jsonb NOT NULL DEFAULT '{}'::jsonb,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
ALTER TABLE public.chart_studio_documents ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.chart_studio_documents FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.chart_studio_documents TO service_role;
CREATE INDEX IF NOT EXISTS chart_studio_documents_created_idx ON public.chart_studio_documents (created_at DESC);
NOTIFY pgrst, 'reload schema';
