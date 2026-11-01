-- Run this once in Supabase → SQL Editor → New query → Run.
-- It creates the shared tables for saved properties, notes and hidden listings.
-- Access is intentionally public (no logins): anyone with the site can read/write.

create table if not exists public.saved (
  id            text primary key,
  listing       jsonb not null,
  status        text not null default 'shortlisted',
  viewing_at    timestamptz,
  rating_lloyd  int,
  rating_meg    int,
  created_by    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists public.notes (
  id          bigint generated always as identity primary key,
  listing_id  text not null references public.saved(id) on delete cascade,
  author      text,
  body        text not null,
  created_at  timestamptz not null default now()
);

create table if not exists public.hidden (
  id          text primary key,
  created_by  text,
  created_at  timestamptz not null default now()
);

alter table public.saved  enable row level security;
alter table public.notes  enable row level security;
alter table public.hidden enable row level security;

drop policy if exists "public access" on public.saved;
drop policy if exists "public access" on public.notes;
drop policy if exists "public access" on public.hidden;
create policy "public access" on public.saved  for all to anon using (true) with check (true);
create policy "public access" on public.notes  for all to anon using (true) with check (true);
create policy "public access" on public.hidden for all to anon using (true) with check (true);

grant select, insert, update, delete on public.saved, public.notes, public.hidden to anon;
grant usage, select on all sequences in schema public to anon;
