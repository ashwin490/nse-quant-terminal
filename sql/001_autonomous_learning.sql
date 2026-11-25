-- Execute ONCE in the ORIGINAL Supabase project SQL Editor before enabling
-- NSE autonomous workers. No existing predictions/options_journal records touched.
-- Private service-role only: do not add anonymous SELECT policies.

create table if not exists public.nse_ai_samples (
  ticker text not null,
  session_date date not null,
  horizon integer not null,
  feature_version integer not null,
  features jsonb not null,
  entry_price numeric not null,
  outcome text not null check (outcome in ('TARGET','STOP','TIME')),
  target_first boolean not null,
  days_to_exit integer not null,
  net_return_pct numeric not null,
  created_at timestamptz not null default now(),
  primary key (ticker, session_date, horizon, feature_version)
);
create index if not exists nse_samples_date_idx on public.nse_ai_samples(session_date desc);

create table if not exists public.nse_ai_forecasts (
  forecast_for date not null,
  ticker text not null,
  model_id text not null,
  entry_price numeric not null,
  target_price numeric not null,
  stop_loss numeric not null,
  target_first_probability numeric not null check (target_first_probability between 0 and 1),
  conservative_ev_pct numeric not null,
  time_estimate text not null,
  horizon_sessions integer not null,
  decision text not null,
  note text not null,
  created_at timestamptz not null default now(),
  primary key (forecast_for,ticker)
);
create index if not exists nse_forecast_recent_idx on public.nse_ai_forecasts(forecast_for desc);

create table if not exists public.nse_ai_models (
  model_id text primary key,
  object_path text not null,
  sha256 text not null,
  trained_until date not null,
  tested_until date not null,
  metrics jsonb not null,
  baseline jsonb not null,
  champion_metrics jsonb,
  n_train integer not null,
  n_test integer not null,
  created_at timestamptz not null default now()
);

create table if not exists public.nse_ai_settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.nse_ai_runs (
  run_id text primary key,
  kind text not null,
  status text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  details jsonb not null default '{}'::jsonb,
  error_class text
);
create index if not exists nse_runs_latest_idx on public.nse_ai_runs(kind,finished_at desc);

create table if not exists public.nse_ai_monitor_marks (
  ticker text not null,
  predicted_date date not null,
  observed_at timestamptz not null,
  last_price numeric not null,
  quote_age_minutes numeric not null,
  status text not null,
  primary key (ticker,predicted_date)
);

create table if not exists public.nse_ai_monitor_events (
  event_id text primary key,
  ticker text not null,
  predicted_date date not null,
  observed_at timestamptz not null,
  last_price numeric not null,
  quote_age_minutes numeric not null,
  status text not null
);

-- Weekly, versioned snapshots of your ORIGINAL 106+/21+ paper ledgers.
insert into storage.buckets (id,name,public)
values ('nse-ai-backups','nse-ai-backups',false)
on conflict (id) do nothing;

-- Private model bucket: never make models public. Model artifacts are separate
-- from existing models/lgbm_stock_ranker.pkl and do not rewrite them.
insert into storage.buckets (id,name,public)
values ('nse-ai-models','nse-ai-models',false)
on conflict (id) do nothing;

do $$
declare name text;
begin
  foreach name in array array[
    'nse_ai_samples','nse_ai_forecasts','nse_ai_models',
    'nse_ai_settings','nse_ai_runs','nse_ai_monitor_marks','nse_ai_monitor_events'
  ] loop
    execute format('alter table public.%I enable row level security', name);
    execute format('revoke all on public.%I from anon, authenticated', name);
    execute format('grant select, insert, update, delete on public.%I to service_role', name);
  end loop;
end $$;

-- Service-role bypasses RLS; keep the service-role key ONLY in GitHub Actions
-- secrets and server-side Streamlit secrets, never in Git or browser code.
-- To allow public/anon dashboards, implement authenticated, user-scoped RLS;
-- do NOT make these research tables anonymously readable by default.
