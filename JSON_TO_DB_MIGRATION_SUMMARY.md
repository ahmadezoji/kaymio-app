# Instagram State JSON → Database Migration

## Overview
Migrated all Instagram state management from JSON files to MySQL database, following the new architecture with centralized database as the single source of truth.

## Files Migrated

### 1. **instagram_story_scheduler.json**
**Purpose:** Tracks last scheduled story publish date to prevent duplicate runs
**Structure:** `{"last_run_date": "YYYY-MM-DD"}`
**Database Table:** `instagram_scheduler_state`

### 2. **instagram_story_routes.json**
**Purpose:** Maps Instagram media IDs to product/affiliate info for story/reel replies
**Structure:** `{media_id: {product_id, affiliate_link, title, description, ...}}`
**Database Table:** `instagram_media_reply_routes`

### 3. **instagram_comment_reply_state.json**
**Purpose:** Tracks Instagram comment reply status and metadata
**Structure:** `{comment_id: {status, created_at, updated_at, ...}}`
**Database Table:** `instagram_comment_reply_states`

## Database Schema

### InstagramSchedulerState
```
- key (VARCHAR 191, PK): "instagram_story_scheduler"
- value (TEXT): JSON-encoded state dict
- updated_at (DATETIME): Last update timestamp
```

### InstagramMediaReplyRoute
```
- media_id (VARCHAR 191, PK): Instagram media ID
- product_id (VARCHAR 191): WooCommerce product ID
- affiliate_link (TEXT): Amazon/Shein/etc affiliate URL
- product_url (TEXT): Original product URL
- title (TEXT): Product title
- description (TEXT): Product description
- reply_surface (VARCHAR 64): "story", "reel", "feed", etc
- published_at (TEXT): ISO timestamp when posted
- created_at (DATETIME): Record creation time
- updated_at (DATETIME): Record update time
```

### InstagramCommentReplyState
```
- comment_id (VARCHAR 191, PK): Instagram comment ID
- reply_data (TEXT): JSON-encoded state object
- created_at (DATETIME): Record creation time
- updated_at (DATETIME): Record update time
```

## Code Changes

### New Files
- **kaymio/database/instagram_state.py** (245 lines)
  - `load_instagram_scheduler_state()`: Load from DB
  - `save_instagram_scheduler_state()`: Save to DB
  - `load_instagram_media_reply_routes()`: Load all routes as dict
  - `save_instagram_media_reply_routes()`: Replace all routes
  - `upsert_instagram_media_reply_route()`: Insert/update single route
  - `load_instagram_comment_reply_states()`: Load all states as dict
  - `save_instagram_comment_reply_states()`: Replace all states
  - `upsert_instagram_comment_reply_state()`: Insert/update single state
  - Plus `migrate_*_from_json()` functions for backward compatibility

### Modified Files

#### kaymio/database/models.py
Added three new ORM models:
- `InstagramSchedulerState`
- `InstagramMediaReplyRoute`
- `InstagramCommentReplyState`

#### kaymio/database/db.py
- Added `_migrate_instagram_states_from_json()` function
- Called from `init_db()` on startup for automatic backward compatibility
- Auto-migrates JSON files if:
  - Files exist in `data/` directory
  - Database tables are empty

#### kaymio/app.py
- Imported all functions from `kaymio.database.instagram_state`
- Updated `_load_story_scheduler_state()` → delegates to database
- Updated `_save_story_scheduler_state()` → delegates to database
- Updated `_load_instagram_media_reply_route_state()` → delegates to database
- Updated `_save_instagram_media_reply_route_state()` → delegates to database
- Updated `_load_instagram_comment_reply_state()` → delegates to database
- Updated `_save_instagram_comment_reply_state()` → delegates to database
- Updated `_update_instagram_comment_reply_state()` → uses `upsert_instagram_comment_reply_state()`
- Updated `_register_instagram_media_reply_route()` → uses `upsert_instagram_media_reply_route()`

#### kaymio/models.py
- No changes needed (wrapper functions still delegate to app.py)

## Backward Compatibility

When the app starts (`init_db()` is called):

1. Creates all new tables if they don't exist
2. Runs `_migrate_instagram_states_from_json()` which:
   - Checks if JSON files exist in `data/` directory
   - Checks if database tables are empty
   - If both true, migrates data from JSON → DB
   - If DB already has data, skips migration
   - Handles errors gracefully with logging

## Benefits

✅ **Single Source of Truth:** All state in MySQL, no scattered JSON files
✅ **Queryable:** Can inspect state via SQL: `SELECT * FROM instagram_media_reply_routes;`
✅ **Transactional:** Database ensures data consistency
✅ **Reliable:** No file I/O issues, proper ACID guarantees
✅ **Scalable:** Database is already the app's central store
✅ **Standard Architecture:** Follows modern Flask best practices
✅ **Zero Downtime:** Automatic migration on first run

## Deployment Steps

1. **Build new containers:**
   ```bash
   docker compose down
   docker compose up -d --build
   ```

2. **Automatic migration:**
   - entrypoint.sh calls `init_db()`
   - `init_db()` calls `_migrate_instagram_states_from_json()`
   - JSON files are auto-migrated to DB if DB is empty

3. **Verify migration:**
   ```bash
   # Check scheduler state
   docker compose exec db mysql -u kaymio -p$MYSQL_PASSWORD kaymio_dev \
     -e "SELECT * FROM instagram_scheduler_state;"

   # Check media routes
   docker compose exec db mysql -u kaymio -p$MYSQL_PASSWORD kaymio_dev \
     -e "SELECT COUNT(*) as total FROM instagram_media_reply_routes;"

   # Check comment reply states
   docker compose exec db mysql -u kaymio -p$MYSQL_PASSWORD kaymio_dev \
     -e "SELECT COUNT(*) as total FROM instagram_comment_reply_states;"
   ```

4. **Old JSON files are safe:**
   - Migration only runs if DB is empty
   - JSON files can be kept as backup or deleted after verification
   - No data loss; JSON files are never deleted by the app

## Future Improvements

- Add database query helpers in `instagram_state.py` for analytics (e.g., "routes published today")
- Add cascade delete rules if routes/states need to link to products in the future
- Monitor query performance if tables grow large (add indexes on `media_id`, `comment_id`)
