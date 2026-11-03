# Backup and Upgrade Guide

This guide covers disaster recovery backups, restoring to a clean installation, and performing safe version upgrades for Hearth.

---

## 1. Backups

Hearth provides atomic, point-in-time backups using the **SQLite Online Backup API**. Backups can be safely performed while users are streaming, scanning, and updating playback progress—without taking the database offline or experiencing lock contention.

### What is Included in a Backup?
- Complete catalog database (`hearth.db` SQLite snapshot with WAL synced).
- Administrative settings and library configurations.
- User accounts, watch history, bookmarks, and scrobble credentials.
- Note: Media files and cached transcode chunks (`/transcode`) are **not** bundled in the backup archive.

### Method A: Admin Web UI & REST API

To generate and download a backup archive:
```bash
curl -X POST http://localhost:8000/api/v1/admin/backup \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -o hearth-backup-$(date +%Y%m%d%H%M%S).tar.gz
```

### Method B: CLI Command

Inside the container or via Docker Compose:
```bash
docker compose exec hearth hearth backup --output /config/backups/hearth-backup.tar.gz
```

### Automated Nightly Backups (Cron Example)

```bash
0 3 * * * docker compose -f /opt/hearth/docker/compose.yaml exec -T hearth hearth backup --output /config/backups/daily.tar.gz
```

---

## 2. Restoring a Backup

### Method A: Admin Web UI / API

On a running server, an administrator can restore from a backup file:
```bash
curl -X POST http://localhost:8000/api/v1/admin/restore \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "file=@hearth-backup-20261103.tar.gz"
```
*Note: The restore handler re-aligns database foreign keys and restarts internal connection pools cleanly.*

### Method B: Cold Restore on a Fresh Server

When migrating to a new host or rebuilding a destroyed volume:
1. Initialize the new directory structure:
   ```bash
   mkdir -p /opt/hearth/config /opt/hearth/media
   ```
2. Unpack the backup archive into the `/config` directory:
   ```bash
   tar -xzf hearth-backup-20261103.tar.gz -C /opt/hearth/config/
   ```
3. Start the Hearth stack:
   ```bash
   docker compose -f docker/compose.yaml up -d
   ```
4. Hearth detects the existing database, runs any pending schema migrations, and resumes operation with catalog and accounts intact.

---

## 3. Upgrades

Hearth applies database schema migrations automatically on startup using an advisory file lock (`/config/.migrations.lock`) to ensure the API server and worker do not execute migrations concurrently.

### Step-by-Step Upgrade Procedure

1. **Perform a backup**:
   ```bash
   docker compose exec hearth hearth backup --output /config/pre-upgrade.tar.gz
   ```

2. **Pull the latest image**:
   ```bash
   docker compose pull
   ```

3. **Recreate the containers**:
   ```bash
   docker compose down
   docker compose up -d
   ```

4. **Verify server health**:
   ```bash
   curl http://localhost:8000/api/v1/health
   ```
   Check that `database` is `"connected"`, `migrations` are `"up_to_date"`, and `version` matches the new release.

5. **Check logs**:
   ```bash
   docker compose logs -f --tail=100
   ```

---

## 4. Rollback Procedure

If a new version encounters unexpected issues:
1. Stop the running containers:
   ```bash
   docker compose down
   ```
2. Pin the image in your `.env` or `compose.yaml` to the prior version tag:
   ```bash
   HEARTH_IMAGE=ghcr.io/yourorg/hearth:v1.0.0
   ```
3. If the newer version applied forward-incompatible database migrations, restore the pre-upgrade database backup:
   ```bash
   tar -xzf /opt/hearth/config/pre-upgrade.tar.gz -C /opt/hearth/config/
   ```
4. Restart the previous version:
   ```bash
   docker compose up -d
   ```
