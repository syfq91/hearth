# Reverse Proxy Configuration Guide

When exposing Hearth beyond your local home network, placing a reverse proxy in front of Hearth is recommended for TLS/HTTPS termination, domain routing, and certificate management.

---

## Key Reverse Proxy Requirements

Hearth requires several specific reverse proxy behaviors:
1. **Server-Sent Events (SSE)** at `/api/v1/events`: The proxy must **disable response buffering** and immediately flush chunks to connected clients.
2. **HTTP Range Requests** (`Range: bytes=...` / `206 Partial Content`): Required for video seeking, direct audio streaming, and comic book page reading. Proxies must pass through `Range` and `Content-Range` headers without caching partial chunks prematurely.
3. **Large Request Body Limits**: Admin backup restoration (`POST /api/v1/admin/restore`) may upload SQLite archives several hundred megabytes in size. Set the proxy's maximum request body size appropriately (e.g. 500 MB+).
4. **Header Forwarding**: Hearth relies on `X-Forwarded-For` and `X-Forwarded-Proto` to know client IPs for rate-limiting and logging.

---

## 1. Caddy (Recommended)

Caddy provides automatic HTTPS with Let's Encrypt, zero-config HTTP/2, and modern defaults.

### Example `Caddyfile`

```caddy
hearth.example.com {
    encode gzip zstd

    # Disable buffering for Server-Sent Events (SSE)
    @events path /api/v1/events*
    handle @events {
        reverse_proxy hearth:8000 {
            flush_interval -1
        }
    }

    # Pass all other requests to Hearth API and Web UI
    handle {
        reverse_proxy hearth:8000 {
            header_up X-Forwarded-Proto {scheme}
            header_up X-Forwarded-For {remote_host}
        }
    }

    # Request body ceiling for backup restores (500MB)
    request_body {
        max_size 500MB
    }
}
```

### Docker Compose with Caddy

```yaml
services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp" # HTTP/3 QUIC
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - hearth

volumes:
  caddy_data:
  caddy_config:
```

---

## 2. Traefik

Traefik is a cloud-native reverse proxy configured via Docker container labels.

### Example `compose.traefik.yaml`

```yaml
services:
  traefik:
    image: traefik:v3.1
    command:
      - "--providers.docker=true"
      - "--providers.docker.exposedbydefault=false"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
      - "--entrypoints.web.http.redirections.entrypoint.to=websecure"
      - "--entrypoints.web.http.redirections.entrypoint.scheme=https"
      - "--certificatesresolvers.myresolver.acme.tlschallenge=true"
      - "--certificatesresolvers.myresolver.acme.email=admin@example.com"
      - "--certificatesresolvers.myresolver.acme.storage=/letsencrypt/acme.json"
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock:ro"
      - "./letsencrypt:/letsencrypt"

  hearth:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.hearth.rule=Host(`hearth.example.com`)"
      - "traefik.http.routers.hearth.entrypoints=websecure"
      - "traefik.http.routers.hearth.tls.certresolver=myresolver"
      - "traefik.http.services.hearth.loadbalancer.server.port=8000"
      - "traefik.http.services.hearth.loadbalancer.responseforwarding.flushinterval=100ms"
```

---

## 3. Nginx

If using standard Nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name hearth.example.com;

    ssl_certificate /etc/letsencrypt/live/hearth.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hearth.example.com/privkey.pem;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Range request support
        proxy_cache off;
        proxy_http_version 1.1;
    }

    # Disable proxy buffering for real-time Server-Sent Events (SSE)
    location /api/v1/events {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
```
