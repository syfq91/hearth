# Troubleshooting FAQ

Frequently encountered questions and step-by-step diagnostic solutions for Hearth.

---

## 1. Playback & Transcoding

### Video playback stutters, pauses frequently, or fails to start
1. **Check transcode mode**:
   Inspect whether the stream is Direct Playing or Transcoding. Look at `GET /api/v1/admin/transcoding/sessions` or the video player diagnostics panel.
2. **Check CPU vs GPU utilization**:
   If transcoding in software on a lower-power CPU, ffmpeg can bottleneck. Check `top` or `htop`.
3. **Inspect transcode directory space**:
   If the `/transcode` tmpfs volume fills up (free space < 50MB), Hearth rejects new transcode requests with HTTP 503 (`TranscodeCapacityExceededError`).
   Check disk usage with:
   ```bash
   docker compose exec hearth df -h /transcode
   ```
   Increase the tmpfs allocation in `docker/compose.yaml` (e.g. `/transcode:size=8g`) if needed.

### "vaInitialize failed (-1)" or GPU not used
- Ensure `/dev/dri/renderD128` exists on the host.
- Ensure container `group_add` matches the host render group GID (`stat -c '%g' /dev/dri/renderD128`).
- Check `docker compose exec hearth vainfo` for detailed driver output. See [HARDWARE_TRANSCODING.md](HARDWARE_TRANSCODING.md) for full setup instructions.

---

## 2. Media Scanner & Library

### Media files are missing from library after scanning
1. **File permissions**:
   The container runs as non-root user (UID 1000). Ensure files in your media directory are readable by UID 1000:
   ```bash
   ls -la /path/to/media/
   chmod -R o+r /path/to/media/
   ```
2. **Directory structure and naming**:
   Hearth parses filenames for release year and episode identifiers. Recommended formats:
   - Movies: `Movies/Inception (2010)/Inception (2010).mkv`
   - TV Series: `TV Shows/Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv`
3. **Corrupted media headers**:
   Files with damaged container atoms are flagged in `MediaFile.probe_error` and skipped to prevent crash loops. Check `GET /api/v1/admin/unmatched` or examine scan logs:
   ```bash
   docker compose logs hearth | grep -i "probe_error"
   ```

### Metadata and posters are missing
- Local `.nfo` and sidecar images (`poster.jpg`, `cover.jpg`) take priority.
- If using TMDB for online metadata, verify `HEARTH_TMDB_API_KEY` is set in your `.env` file. Without an API key, library items remain in pending state.

---

## 3. Database & Concurrency

### "sqlite3.OperationalError: database is locked"
- **Do not place `/config` on NFS or CIFS network shares**:
  SQLite relies on POSIX advisory locks and shared memory (`.db-shm` and `.db-wal`), which are unreliable over network-attached filesystems. `/config` must reside on local host storage (ext4, XFS, ZFS, btrfs).
- Hearth uses WAL (Write-Ahead Logging) mode and busy timeouts (5.0s) with serialized write locks. If a hard crash left orphaned file locks:
  ```bash
  docker compose down
  rm -f /config/hearth.db-shm /config/.migrations.lock
  docker compose up -d
  ```

---

## 4. Networking & Reverse Proxies

### Real-time notifications / scan progress not updating in browser
- Server-Sent Events at `/api/v1/events` require unbuffered proxying.
- If using Nginx, ensure `proxy_buffering off;` and `proxy_read_timeout 86400s;` are set.
- If using Caddy, set `flush_interval -1`.
- See [REVERSE_PROXY.md](REVERSE_PROXY.md) for full configuration templates.

### Backup restore fails with "413 Payload Too Large" or "Connection reset"
- Reverse proxies enforce default upload size ceilings (e.g. 1MB in Nginx).
- Configure your reverse proxy with `client_max_body_size 500M;` (or equivalent in Traefik/Caddy).

---

## 5. Administrative Diagnostics

### Viewing server logs
View real-time structured logs from the Docker container:
```bash
docker compose logs -f --tail=100 hearth
```
Or query the in-memory log buffer via the Admin API:
```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" "http://localhost:8000/api/v1/admin/logs?level=WARNING"
```

### Checking Prometheus metrics
If `HEARTH_METRICS_ENABLED=true` is enabled:
```bash
curl http://localhost:8000/metrics
```
Displays uptime, process memory, transcode stream concurrency, and request counters.
