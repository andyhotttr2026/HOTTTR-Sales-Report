-- Run this in Supabase > SQL Editor
-- Creates the table that stores daily creator stats

create table creator_stats (
  id             bigserial primary key,
  run_date       date        not null,
  creator_name   text        not null,
  platform       text        not null,  -- 'instagram' or 'tiktok'
  today_count    int,
  today_stories  int,                -- Instagram only
  week_count     int,
  week_views     bigint,
  week_likes     bigint,
  week_comments  bigint,
  week_shares    bigint,                -- TikTok only, null for Instagram
  month_count    int,
  month_views    bigint,
  month_likes    bigint,
  created_at     timestamptz default now()
);

-- Allows two runs per day (morning + evening) without conflicts
create unique index creator_stats_unique
  on creator_stats (run_date, run_slot, creator_name, platform);
