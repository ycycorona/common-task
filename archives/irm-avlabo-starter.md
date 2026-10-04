# irm.avlabo.com 采集项目启动文档

调研日期：2026-10-01。站点自述约 5,750 标题、16,798 条作品评论、9,770 条掲示板投稿。

本文档是新项目的唯一启动依据：目标、范围、存储结构、数据库选型、建表语句、执行顺序、完成标准都在这里。
建库以同目录 `irm-schema.sql` 为准，本文件 §4 内嵌同一份 DDL，两边必须保持一致。

## 0. 目标与非目标

目标两块：

1. 所有人员身份，以及每人名下的全部作品。作品页要有介绍、评分、影评、用户评论。
2. 掲示板 8 个板全量。

非目标：

- 不要扩成全站镜像，不要下样品视频。
- 不要碰 `/manage/`、`/cms/`、`/account/`。`/api/` 目录 403，但 `/api/comments.php` 是公开页在用的，可以调。

## 1. 站点事实

- 公开作品页是 Astro 构建出的静态 HTML，nginx 直接发文件。`.php` 只是旧站 URL 尾巴，主体在 HTML 里。会变的评论数、评论列表另走接口。
- 已核对索引：
  - `https://irm.avlabo.com/json/entry-map.json` 约 953KB。作品 id → `[path, title, image_url, date, flag]`。
  - `https://irm.avlabo.com/json/actress-map.json` 约 150KB。女优内部 id → `[name, reading, image_url_or_null, extra_or_null]`。没有监督版、作者版。
  - `https://irm.avlabo.com/sitemap.xml`
  - `https://irm.avlabo.com/bbs/sitemap.xml` 约 76 条分页，不是帖子 permalink。
- 作品页稳定 id 在收藏节点，不在 URL 里：

```html
<div id="fav_app" data-id="17986" data-url="/archives/2024/04/04_084155.php" data-img="...">
```

`data-id` 就是评论接口的 `entry_id`。示例作品：`/archives/2024/04/04_084155.php`，entry_id 17986，
女优 slug `kimino_ayumi`，メーカー アタッカーズ，监督 二代目乱田舞，评论员 にくまらGT。

## 2. 人员身份

站点把「人」和「机构」分开。人要全抓，机构只当作作品上的一个字段，不建人员。

| 角色 role | 索引 | 个人页 | 规模（2026-10-01） | 说明 |
|---|---|---|---|---|
| actress 女优 | `/actress/` | `/archives/<slug>.php` | 页上写 1,698 名 | slug 与 map 里的 `a1000049` 不是同一套 id |
| director 监督 | `/director/` | `/director/<name>.php` | 索引 HTML 里约 559 条个人链接 | 名字做 URL，日文要编码。例 `/director/TOHJIRO.php`、`/director/%E9%8E%96%E7%BE%85%E9%AC%BC.php` |
| writer 作家 | `/writer/` | `/writer/<name>.php` | 14 | 同人作者，不是 AV 监督。例 `/writer/Fue.php` |
| reviewer 站内评论员 | `/reviewer/` | `/reviewer/<数字>.php` | 约 20 | 写影评的人，不是片中演员。例 `/reviewer/1.php` 所长，页上数字 2,969 是評数不是 id |

- 没有男优索引，导航和这几个列表里都没有。作品页上如果出现男优名，只当作正文里的字符串，不要单开角色。
- 不是人，不要进 `people`：
  - メーカー：`/maker/`，个人页实际在 `/archives/cat24.php`、`/archives/kmpmillion.php` 这类。作品页上的「アタッカーズ」存进 `works.maker` 即可。
  - シリーズ：`/series/`，系列名，不是人，存 `works.series`。
  - 平台、标签、评分列表：不是人。
- 同一人若同时是女优和监督，插两条 `people`，用 role 分开，不要合并。
- 监督索引有「もっと見る（残り439）」。先把 `/director/` 里所有 `/director/*.php` 抽出来；若可见人数 + 439 对不上链接数，再看按钮是不是另有一页。作家索引没有这个按钮。
- 个人页作品列表规则相同：
  - 默认発売日順。不要抓 `?sort=rating10`、`?sort=mina`，那是同一批作品的重排，规范化 URL 时直接丢掉 `sort` 参数。
  - 页上有「Nタイトル」「ページ 1 / M」。必须翻页。锁罗鬼 169 标题、3 页；真咲南朋 59 标题、1 页。
  - 作品链接收 `/archives/YYYY/MM/...php`、`/archives/<id>.php`、`/dojinav/<id>.php`。
- 女优页额外字段：H1 `きみと歩実（きみとあゆみ）` 括号内是读音；外形一行 `T149cm B80(D)-W58-H82　1994.3.7`；
  可能有 X 账号。监督页、作家页通常只有名字、可选 X、作品列表。

## 3. 存储与数据库选型

### 3.1 分层：文件存证据，库存视图

```text
data/
  entry-map.json
  actress-map.json
  sitemap.xml
  bbs-sitemap.xml
  raw/works/<entry_id>.html          # 按 data-id 命名，不按 path（避开 %编码和重名）
  raw/people/actress/<slug>.html
  raw/people/director/<slug>.html
  raw/people/writer/<slug>.html
  raw/people/reviewer/<id>.html
  raw/bbs/<board>/<page>.html
  raw/comments/<entry_id>.json       # /api/comments.php 原始响应
  irm.sqlite
```

原则：`raw/` 永不覆盖解析结果；`irm.sqlite` 可从 `raw/` 重建；`fetch_log.sha256` 对得上才算抓成功。

### 3.2 数据库：SQLite 单文件 `data/irm.sqlite`

- 数据量小（结构化后几十 MB），写入是单 Agent 串行（并发 1，间隔 1 秒），SQLite 的 WAL 模式完全够用。
- 查询是关系型（人-作品多对多、评论树、计数校验），`JOIN + GROUP BY` 比 Mongo/纯 JSON 文件方便。
- Python 标准库自带 `sqlite3`，macOS 即用，单文件好备份、好搬运。断点续爬只查 `fetch_log` 一张表。
- Postgres/MySQL 是为多机并发写准备的，这里用不上；DuckDB 适合只读分析，不适合做爬虫状态机；
  Mongo 会把关联和去重推给应用层。以后要做分析，可以再从 SQLite 导出到 DuckDB。

建库固定：`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;`

### 3.3 表一览

- `people`：`(role, slug)` 主键。`slug` 存 URL 上那段原样（director 的百分号编码不解码），
  显示名存 `name`；女优 map 的 `a1000049` 存 `source_id`，评论员数字 id 也存 `source_id`；
  `body_raw` 存外形原文，解析出的身高三围生日另起可空列；reviewer 的 `work_count` 是評数，校验时别当作品数。
- `works`：`entry_id` 主键，`path` 唯一。`site_score/user_score/user_count` 提成独立列方便排序，
  12 个イラマ分项全量进 `scores_json`；缺失存 NULL，不填 0。
- `work_people`：`(entry_id, role, person_slug, source)` 主键，`source` 区分 `person_page` / `work_page`，
  两边都有就两条都留，查询去重。
- `work_comments`：`(entry_id, comment_id)` 主键，接口原始 JSON 整条进 `raw_json`。
- `bbs_posts`：`(board, comment_id)` 主键，`parent_id` 指最近的 `id^="comment-"` 祖先，无则 NULL 即楼主。
- `work_links`：存「近い作品」「出演作品」链接，`kind` 只取 `related` / `credit`。
- `fetch_log`：`url` 主键（规范化后，丢掉 `sort` 参数），`4xx` 除 429 外标死不再抓。

## 4. DDL（与 irm-schema.sql 一致）

```sql
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
```

需要全文检索时再另加 `works_fts`（FTS5），首版不强制。

## 5. 任务 A：人 + 作品

1. 下载两个 JSON 和 sitemap。
2. 抓四个索引，写入 `people` 队列：`/actress/`、`/director/`、`/writer/`、`/reviewer/`。旁边的数字是作品数或評数，不是主键。
3. 抓每个人的个人页和全部分页，作品链接写入 `work_people`，`source='person_page'`。
4. 作品队列 = 上述链接 ∪ `entry-map.json` 的 path ∪ sitemap 里 `/archives/` 作品页和 `/dojinav/`。人页会漏无演职员的条目。
5. 抓作品 HTML。用 `#fav_app[data-id]` 对齐 entry-map。页上的女优链接、监督链接、作家链接再写一条 `work_people`，`source='work_page'`。
6. 评论：先 `GET /api/comments.php?counts=id,id`（先试 50 个 id）。`count>0` 再 `?entry_id=`。
   缺参返回 400 `{"error":"entry_id required"}`。空评论是正常值。响应里有 `limit`/`offset`，
   第一条非空结果定字段，需要再翻页。原始 JSON 留 `raw_json`。

作品页要取的字段：

- 标题、entry_id、规范 path、封面 URL
- 発売日、配信日、メーカー、监督、女优、レビュアー
- 站内総合評価、みんなの評価和人数
- 分项：イラマチオ総合評価、女優ランク、イラマ本数、イラマ発射数、イラマタイム、えずき汁量、
  えずき声・音、えずき反応、スピード、深さ、男根レベル、ゲロ量。页面没写就空，不要把缺失填成 0
- メーカーコメント/あらすじ
- `イラマチオ レビュー` 到 `まとめ` 之前为正文，`まとめ` 下为总结
- 标签
- 「近い作品」「出演作品」只存链接（进 `work_links`）

## 6. 任务 B：掲示板

| 板 | 入口 |
|---|---|
| イラマAV【日本人】 | `/bbs/av_japan.php`（5,084 件，58 页） |
| イラマAV【外国人】 | `/bbs/av_world.php` |
| イラマ同人【2D/3D】 | `/bbs/doujin.php` |
| AV新法関連 | `/bbs/av_shinpou.php` |
| イラマ相談室 | `/bbs/pw.php` |
| 体验谈 | `/bbs/experience.php` |
| 风俗 | `/bbs/fuzoku.php` |
| 其他 | `/bbs/other.php` |

- 每个板是嵌在分页 HTML 里的评论树，不是一帖一页。
- `/bbs/<board>.php` 是最新页，`/bbs/<board>/1.php` 是最古。发现以 `bbs/sitemap.xml` 为准，再用最新页的「N/N」核对漏页。
- 帖子主键 `comment-51771`，作者 `.comment-name`，时间 `.comment-date small`，正文 `.comment-body`，赞 `.vote-n`。
  回复在子 `ul` 里，父 id 取最近的 `id^="comment-"` 祖先。`.limitH` 只是折叠，全文已在 HTML 里。
- 不要抓 `/bbs/c<id>.php`。写入时回填 `root_id`（楼主 id），方便一帖拉全树。

## 7. 执行顺序和限速

索引 → 四类个人页（含分页）→ 作品 HTML → 评论接口 → 掲示板分页 → §8 校验。

- 并发 1，间隔 1 秒。失败重试 3 次，退避 5s / 20s / 60s。4xx 除 429 外标死。
- 断点只看 `fetch_log`。User-Agent 固定。

## 8. 完成标准（含校验 SQL）

- actress 接近 1,698，director 接近索引里的 `/director/*.php` 数（约 559，以实抽为准），
  writer = 14，reviewer 等于 `/reviewer/` 的个人链接数。差超过 2% 就停。
- entry-map 里每个 path 都抓过或标 404：

```sql
SELECT COUNT(*) FROM fetch_log
WHERE url LIKE '%/archives/%' AND status = 404;
```

- 抽 10 个监督页、5 个女优页，页上「Nタイトル」等于该人 `work_people` 里 `source='person_page'` 的去重作品数。
  锁罗鬼这种多页必须对得上 169，不能只等于第一页：

```sql
SELECT work_count FROM people WHERE role = 'director' AND slug = '<锁罗鬼slug>';
SELECT COUNT(DISTINCT entry_id) FROM work_people
WHERE role = 'director' AND person_slug = '<锁罗鬼slug>' AND source = 'person_page';
```

- 抽 20 个作品，`data-id`、标题、正文非空，且页上每个女优/监督链接都有 `work_people`：

```sql
SELECT entry_id, title, body_md FROM works WHERE body_md IS NULL OR title = '';
```

- 抽 5 个有评论的 entry_id，行数等于接口 `count`。
- `av_japan` 的 `comment_id` 去重后接近 5,084：

```sql
SELECT COUNT(DISTINCT comment_id) FROM bbs_posts WHERE board = 'av_japan';
```

- `fetch_log` 无未处理 5xx：

```sql
SELECT url, status, retry FROM fetch_log WHERE status BETWEEN 500 AND 599;
```

## 9. 增量

每周重下两个 JSON、四个索引、bbs sitemap。新 slug、新作品 path、掲示板最新页的新 comment_id 才抓。
作品正文不随评论变化，不必全量重抓 HTML，只补 `counts` 变了的评论。

## 10. 新项目起步步骤

1. 新建空目录，拷入本文件和 `irm-schema.sql`。
2. `sqlite3 data/irm.sqlite < irm-schema.sql` 建库。
3. 先跑通索引页 → 写 `people` → 跑通 1 个女优分页 + 1 个作品页 + 1 个评论接口 + 1 个 BBS 分页，再全量。
4. 全量前先把 §8 的校验 SQL 存成脚本，每轮跑完自动对数。
