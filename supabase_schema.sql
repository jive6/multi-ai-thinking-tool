create table if not exists public.ai_history (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  question text not null,
  question_summary text not null default '',
  results jsonb not null,
  comparison jsonb
);

create index if not exists ai_history_created_at_idx
  on public.ai_history (created_at desc);

alter table public.ai_history enable row level security;

revoke all on table public.ai_history from anon, authenticated;
grant select, insert on table public.ai_history to service_role;
