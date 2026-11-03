# Hardware Transcoding Guide (VAAPI)

Hearth supports hardware-accelerated video decoding, scaling, tonemapping, and encoding via **VA-API** (Video Acceleration API) on Linux hosts. This document explains how to identify your GPU, configure Docker container permissions, verify functionality, and troubleshoot common issues.

---

## 1. Requirements & Supported Hardware

Hearth's container image includes:
- `libva2` and standard VA-API runtime libraries
- `intel-media-va-driver-non-free` (Intel iHD driver for Gen 8+ GPUs)
- `mesa-va-drivers` (AMD `radeonsi` driver and older Intel `i965` driver)
- Modern `ffmpeg` compiled with `--enable-vaapi`

### Hardware Support Matrix

| Vendor | Generation / Model | VA-API Driver | Supported Codecs (Decode / Encode) |
| :--- | :--- | :--- | :--- |
| **Intel** | Gen 8–11 (Broadwell through Ice Lake) | `iHD` | H.264, HEVC 8/10-bit decode & H.264 encode |
| **Intel** | Gen 12+ (Tiger Lake, Alder Lake, Raptor Lake, Arc) | `iHD` | H.264, HEVC 10-bit, VP9, AV1 (Gen 12.5+) |
| **Intel** | Gen 7–7.5 (Haswell, Skylake legacy) | `i965` | H.264 decode/encode |
| **AMD** | GCN 3rd gen+, RDNA 1/2/3, Ryzen APUs | `radeonsi` | H.264, HEVC decode & H.264 encode |

---

## 2. Host Setup & Device Permissions

VA-API uses the Linux Direct Rendering Manager (DRM) render nodes, typically located at `/dev/dri/renderD128`.

### Step 1: Verify Host Device Node

Check that the DRM render node exists on the host:
```bash
ls -l /dev/dri
```
Expected output:
```text
crw-rw----+ 1 root video  226,   0 Nov  1 10:00 card0
crw-rw----+ 1 root render 226, 128 Nov  1 10:00 renderD128
```

### Step 2: Determine Render and Video Group IDs (GIDs)

Docker containers must run with supplemental groups matching the host render device permissions.

Run the following commands on the host:
```bash
# Get host render group ID
getent group render | cut -d: -f3
# Or inspect the render node directly
stat -c '%g' /dev/dri/renderD128

# Get host video group ID (needed especially on AMD)
getent group video | cut -d: -f3
```

- Common render GID on Debian / Ubuntu: `105` or `107`
- Common render GID on Arch / Fedora / Alpine: `989` or `108`

Export these in your `.env` file:
```bash
RENDER_GID=105
VIDEO_GID=44
RENDER_DEVICE=/dev/dri/renderD128
```

---

## 3. Docker Compose Configuration

### Standard Intel Setup (Default)

Enabled out of the box in `docker/compose.yaml`:

```bash
docker compose -f docker/compose.yaml up -d
```

For older Intel processors (Haswell through Skylake), uncomment `LIBVA_DRIVER_NAME: i965` in `docker/compose.yaml`.

### AMD Radeon Setup

AMD GPUs require passing both `/dev/dri/renderD128` and `/dev/kfd` (Kernel Fusion Driver) and assigning both `render` and `video` groups.

Uncomment the AMD settings in `docker/compose.yaml`:
```yaml
  environment:
    LIBVA_DRIVER_NAME: radeonsi
  devices:
    - ${RENDER_DEVICE:-/dev/dri/renderD128}:/dev/dri/renderD128
    - /dev/kfd:/dev/kfd
  group_add:
    - ${RENDER_GID:-105}
    - ${VIDEO_GID:-44}
```

Then start the stack:
```bash
docker compose -f docker/compose.yaml up -d
```

### CPU-Only Fallback (No GPU)

If your host has no compatible GPU, comment out the `devices` and `group_add` sections in `docker/compose.yaml`:
```bash
docker compose -f docker/compose.yaml up -d
```
Transcoding runs purely in software (`libx264 veryfast`).

---

## 4. Verification with `vainfo`

Verify VA-API acceleration inside the running container:

```bash
docker compose exec hearth vainfo
```

### Expected Intel Output

```text
vainfo: VA-API version: 1.17 (libva 2.12.0)
vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 23.1.1
vainfo: Supported profile and entrypoints
      VAProfileH264Main               :	VAEntrypointVLD
      VAProfileH264Main               :	VAEntrypointEncSlice
      VAProfileH264High               :	VAEntrypointVLD
      VAProfileH264High               :	VAEntrypointEncSlice
      VAProfileHEVCMain               :	VAEntrypointVLD
      VAProfileHEVCMain10             :	VAEntrypointVLD
```

Key indicators:
- `VAEntrypointVLD`: Hardware decoding supported for that codec profile.
- `VAEntrypointEncSlice`: Hardware encoding supported (e.g. H.264 encode).

### Verify via Hearth API

Query the server's transcode capability probe endpoint:
```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://localhost:8000/api/v1/admin/transcoding/capabilities
```
Returns:
```json
{
  "vaapi_available": true,
  "driver_name": "Intel iHD driver",
  "device_path": "/dev/dri/renderD128",
  "profiles": ["H264Main", "H264High", "HEVCMain", "HEVCMain10"],
  "test_encode_passed": true
}
```

---

## 5. Troubleshooting Common Errors

### 1. `vaInitialize failed (-1)` or `unknown libva error`
- **Cause**: Container process cannot access `/dev/dri/renderD128` due to permission mismatch.
- **Solution**: Check host node ownership with `stat -c '%g' /dev/dri/renderD128` and ensure `group_add` in compose matches the host GID.

### 2. `vaGetDriverNameByIndex() failed`
- **Cause**: No matching VA-API driver found for your GPU PCI ID, or `LIBVA_DRIVER_NAME` is invalid.
- **Solution**:
  - For Intel: Try `LIBVA_DRIVER_NAME=i965` if on an older Gen 7/8 CPU.
  - For AMD: Ensure `LIBVA_DRIVER_NAME=radeonsi`.

### 3. `Failed to create VAAPI device: -19 (No such device)`
- **Cause**: The device `/dev/dri/renderD128` is not passed into the container or does not exist on the host.
- **Solution**: Run `ls -l /dev/dri` on the host. If your GPU node is `/dev/dri/renderD129`, set `RENDER_DEVICE=/dev/dri/renderD129`.

### 4. HDR to SDR Tonemapping Artifacts or Crash
- **Cause**: VAAPI tonemap filter requires OpenCL or driver support (`tonemap_vaapi`).
- **Solution**: Hearth's decision engine automatically falls back to software transcoding if hardware filter initialization fails. Check active session logs at `GET /api/v1/admin/transcoding/sessions/{id}/logs`.

### 5. `TranscodeCapacityExceededError` (HTTP 503)
- **Cause**: Free disk space in `/transcode` dropped below 50 MB, or active session limits reached.
- **Solution**: Increase the tmpfs size in `docker/compose.yaml` (e.g., `/transcode:size=8g`) or clear orphaned files.
