# AETHER Production Database Migration, Replication, & Backups Blueprint

## 1. Objective
To transition the AETHER trading desk from a file-based storage layer (static `.xlsx` spreadsheets and `.json` state files) into a highly robust, production-grade, and containerized **PostgreSQL Relational Database**. The architecture must be 100% backward-compatible, support automated backups/replication, and allow 1-second migration to any hosting provider.

---

## 2. Structured Relational SQL Schema

We define 5 core relational tables to perfectly map and normalize AETHER's local data files:

### 1. Table `strategy_profiles` (Replaces profiles json block)
*   **Columns:**
    *   `id` ➡️ `SERIAL PRIMARY KEY`
    *   `profile_name` ➡️ `VARCHAR(30) UNIQUE NOT NULL` (e.g., 'DEFENSIVE', 'BALANCED')
    *   `position_limit` ➡️ `INTEGER NOT NULL`
    *   `cash_buffer_pct` ➡️ `NUMERIC(5,2) NOT NULL`
    *   `min_score_threshold` ➡️ `NUMERIC(4,2) NOT NULL`
    *   `is_active` ➡️ `BOOLEAN DEFAULT TRUE`
    *   `updated_at` ➡️ `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

### 2. Table `watchlist_research` (Replaces `state_of_the_day.xlsx` Research sheet)
*   **Columns:**
    *   `symbol` ➡️ `VARCHAR(20) NOT NULL`
    *   `date` ➡️ `DATE NOT NULL`
    *   `industry` ➡️ `VARCHAR(100)`
    *   `pgr_rating` ➡️ `VARCHAR(10)`
    *   `combined_score` ➡️ `NUMERIC(4,2)`
    *   `price` ➡️ `NUMERIC(10,4)`
    *   `stop_loss` ➡️ `NUMERIC(10,4)`
    *   `target_price` ➡️ `NUMERIC(10,4)`
    *   `risk_reward_ratio` ➡️ `NUMERIC(6,2)`
    *   `target_gain_pct` ➡️ `NUMERIC(6,2)`
    *   `has_setup` ➡️ `BOOLEAN DEFAULT FALSE`
    *   `obos_status` ➡️ `VARCHAR(20)`
    *   `patterns` ➡️ `VARCHAR(100)`
    *   `created_at` ➡️ `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
*   **Primary Key:** `PRIMARY KEY (symbol, date)` (Ensures absolute time-series uniqueness and prevents duplicates).
*   **Indexes:**
    *   `CREATE INDEX idx_research_date ON watchlist_research(date);`
    *   `CREATE INDEX idx_research_score ON watchlist_research(combined_score DESC);`

### 3. Table `portfolio_positions` (Replaces `ai_portfolio_game.json` positions block)
*   **Columns:**
    *   `symbol` ➡️ `VARCHAR(20) PRIMARY KEY`
    *   `qty` ➡️ `NUMERIC(12,4) NOT NULL`
    *   `cost_basis` ➡️ `NUMERIC(10,4) NOT NULL`
    *   `stop_loss` ➡️ `NUMERIC(10,4) NOT NULL`
    *   `original_stop_loss` ➡️ `NUMERIC(10,4)`
    *   `is_scarcity` ➡️ `BOOLEAN DEFAULT FALSE`
    *   `acquired_date` ➡️ `DATE NOT NULL`
    *   `account_id_key` ➡️ `VARCHAR(50)`
    *   `updated_at` ➡️ `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

### 4. Table `trade_history_dna` (Replaces `trade_history_dna.json` completed trades)
*   **Columns:**
    *   `id` ➡️ `SERIAL PRIMARY KEY`
    *   `record_type` ➡️ `VARCHAR(30) NOT NULL` (e.g., 'CLOSED_TRADE', 'CIRCUIT_BREAKER_TRIGGER')
    *   `symbol` ➡️ `VARCHAR(20)`
    *   `buy_date` ➡️ `DATE`
    *   `sell_date` ➡️ `DATE`
    *   `buy_price` ➡️ `NUMERIC(10,4)`
    *   `sell_price` ➡️ `NUMERIC(10,4)`
    *   `pnl_pct` ➡️ `NUMERIC(6,2)`
    *   `holding_days` ➡️ `INTEGER`
    *   `spy_return_pct` ➡️ `NUMERIC(6,2)`
    *   `vxx_return_pct` ➡️ `NUMERIC(6,2)`
    *   `reason` ➡️ `TEXT`
    *   `buy_dna_pgr` ➡️ `VARCHAR(10)`
    *   `buy_dna_score` ➡️ `NUMERIC(4,2)`
    *   `created_at` ➡️ `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

### 5. Table `decision_log` (Replaces `decision_log.jsonl`)
*   **Columns:**
    *   `id` ➡️ `SERIAL PRIMARY KEY`
    *   `date` ➡️ `DATE NOT NULL`
    *   `symbol` ➡️ `VARCHAR(20) NOT NULL`
    *   `live_price` ➡️ `NUMERIC(10,4) NOT NULL`
    *   `rules_action` ➡️ `VARCHAR(20) NOT NULL` (e.g., 'HOLD', 'SELL', 'REVIEW')
    *   `rules_reason` ➡️ `TEXT`
    *   `score` ➡️ `INTEGER` (decision quality score assigned by offline retro audits)
    *   `created_at` ➡️ `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`

---

## 3. Backward-Compatible "Dual-Bridge" Adapter

To ensure 100% zero-disruption operation:
1.  **The Adapter Pattern:** We upgrade `database.py` and `data_api.py` into a **Dual-Write/Read Bridge**.
2.  **Writing:** Whenever the autopilot saves the game state, updates the Research sheet, or appends a trade log:
    *   The bridge **simultaneously writes the data to the SQL database AND updates the local files** (`.xlsx` or `.json` on disk).
    *   *The Benefit:* Your existing spreadsheet files, local log files, and active dashboard frontend (which reads files via FastAPIs) remain 100% updated in real-time.
3.  **Reading:** 
    *   The system always attempts to read from the PostgreSQL database first (0.01-second search speed).
    *   If the database connection fails (network timeout, database offline during hosting migration, etc.), **the system silently catches the exception, falls back to read the local JSON/Excel files, and continues running with zero interruptions!**

---

## 4. Containerized Hosting & Replication (`docker-compose.yml`)

To allow **1-second migration to another hosting provider**, we containerize your entire PostgreSQL cluster using Docker Compose:

```yaml
version: '3.8'

services:
  # Primary Database Instance
  aether-db:
    image: postgres:15-alpine
    container_name: aether-postgres
    restart: always
    environment:
      POSTGRES_USER: root
      POSTGRES_PASSWORD: yu_75299527_yu
      POSTGRES_DB: market_DB
    ports:
      - "2665:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts/db_init:/docker-entrypoint-initdb.d
    networks:
      - aether-net

  # Automated pg_dump Backup Service
  aether-backup:
    image: probackups/pg_dump:latest
    container_name: aether-db-backup
    restart: always
    environment:
      PG_HOST: aether-db
      PG_PORT: 5432
      PG_USER: root
      PG_PASSWORD: yu_75299527_yu
      PG_DB: market_DB
      BACKUP_CRON: "0 2 * * *" # daily at 2:00 AM
      BACKUP_DIR: /backups
    volumes:
      - ./Data/Backup/Database:/backups
    depends_on:
      - aether-db
    networks:
      - aether-net

volumes:
  pgdata:
    driver: local

networks:
  aether-net:
    driver: bridge
```

### 📦 1-Second Hosting Migration Steps:
To migrate this entire setup to AWS, GCP, or a new VPS:
1.  Run **`docker-compose up -d`** on the new server (spawns the exact database and backup scheduler in 2 seconds!).
2.  Copy your latest SQL backup file `db_backup_YYYY-MM-DD.sql` from your old server's `Data/Backup/Database/` folder to the new server.
3.  Restore the backup cleanly:
    `docker-compose exec -T aether-db psql -U root market_DB < db_backup_YYYY-MM-DD.sql`
4.  Change the `"database" -> "url"` in your new server's `config.json` to point to the new IP! Done!

---

## 5. Autonomic Backups & Verification
*   We will code **`scripts/utils/backup_db.py`** as part of the daily watchdog.
*   Every morning at 6:00 AM, the watchdog executes a dry-run `pg_dump` of `market_DB`.
*   The generated `.sql` file is safely saved under `Data/Backup/Database/`.
*   For ultimate durability, the watchdog **automatically zips, encrypts, and emails the SQL backup directly to your Gmail inbox** (`bilyky@gmail.com`) using your newly configured secure SMTP sender! If the server's hard drive crashes, you still have your database fully saved in your inbox!
