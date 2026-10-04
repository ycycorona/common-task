-- irm.avlabo.com scrape database schema (SQLite)
-- Keep in sync with irm-avlabo-starter.md section 4.
-- Usage: sqlite3 data/irm.sqlite < irm-schema.sql

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE people(
  role TEXT NOT NULL CHECK(role IN ('actress','director','writer','reviewer')),
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  reading TEXT,
  aliases TEXT NOT NULL DEFAULT '[]',
  body_raw TEXT,
  height_cm INTEGER,
  bust_cm INTEGER,
  cup TEXT,
  waist_cm INTEGER,
  hips_cm INTEGER,
  birth_date TEXT,
  x_handle TEXT,
  work_count INTEGER,
  profile_url TEXT NOT NULL,
  image_url TEXT,
  source_id TEXT,
  raw_path TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(role, slug)
);

CREATE TABLE works(
  entry_id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  maker TEXT,
  series TEXT,
  release_date TEXT,
  delivery_date TEXT,
  site_score REAL,
  user_score REAL,
  user_count INTEGER,
  reviewer_role TEXT NOT NULL DEFAULT 'reviewer',
  reviewer_slug TEXT,
  body_md TEXT,
  summary_md TEXT,
  product_note TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  scores_json TEXT NOT NULL DEFAULT '{}',
  image_url TEXT,
  raw_path TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(reviewer_role, reviewer_slug) REFERENCES people(role, slug)
);

CREATE TABLE work_people(
  entry_id INTEGER NOT NULL REFERENCES works(entry_id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('actress','director','writer','reviewer')),
  person_slug TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('person_page','work_page')),
  PRIMARY KEY(entry_id, role, person_slug, source),
  FOREIGN KEY(role, person_slug) REFERENCES people(role, slug)
);
CREATE INDEX idx_work_people_person ON work_people(role, person_slug);
CREATE INDEX idx_work_people_entry ON work_people(entry_id);

CREATE TABLE work_comments(
  entry_id INTEGER NOT NULL REFERENCES works(entry_id) ON DELETE CASCADE,
  comment_id TEXT NOT NULL,
  parent_id TEXT,
  author TEXT,
  posted_at TEXT,
  body TEXT,
  rating REAL,
  raw_json TEXT NOT NULL,
  PRIMARY KEY(entry_id, comment_id)
);
CREATE INDEX idx_work_comments_entry ON work_comments(entry_id);

CREATE TABLE bbs_posts(
  board TEXT NOT NULL CHECK(board IN ('av_japan','av_world','doujin','av_shinpou','pw','experience','fuzoku','other')),
  comment_id TEXT NOT NULL,
  page_url TEXT NOT NULL,
  parent_id TEXT,
  root_id TEXT,
  depth INTEGER NOT NULL DEFAULT 0,
  author TEXT,
  posted_at TEXT,
  body TEXT NOT NULL,
  vote_count INTEGER NOT NULL DEFAULT 0,
  raw_path TEXT NOT NULL,
  PRIMARY KEY(board, comment_id)
);
CREATE INDEX idx_bbs_parent ON bbs_posts(board, parent_id);
CREATE INDEX idx_bbs_root ON bbs_posts(board, root_id);

CREATE TABLE work_links(
  entry_id INTEGER NOT NULL REFERENCES works(entry_id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('related','credit')),
  target_path TEXT NOT NULL,
  PRIMARY KEY(entry_id, kind, target_path)
);

CREATE TABLE fetch_log(
  url TEXT PRIMARY KEY,
  status INTEGER NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
  sha256 TEXT,
  raw_path TEXT,
  retry INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
