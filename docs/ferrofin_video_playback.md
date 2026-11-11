# Video playback — the client/server contract, and how to reimplement it

Ferrofin serves video to unmodified Jellyfin clients. That means its playback
surface is not "an API we invented"; it is a wire contract with a very specific
state machine:

1. the client **asks** what to do (`PlaybackInfo`),
2. the server **decides** a play method and hands back a URL,
3. the client **opens that URL** (direct file, progressive stream, or HLS),
4. the client **reports** progress and keep-alives while it plays,
5. the server **reaps** the transcode when the client stops.

This document walks that path end to end and is written so you can implement the
same behaviour in another app. It focuses on the **protocol and the decision
logic**, not on ffmpeg argument construction.

> Companion document: [`docs/transcoding.md`](transcoding.md) covers probing,
> encoder selection, hardware acceleration, and the exact ffmpeg/transcode-plan
> internals. Read that when you need to know *how the bytes are produced*. Read
> this when you need to know *what the client sends, what the server answers,
> and who is responsible for what at each step*.

Code references point at this repository:

| Area | Where |
| --- | --- |
| Playback negotiation | `crates/ferrofin-api/src/handlers/media_info.rs` |
| Direct play / progressive | `crates/ferrofin-api/src/handlers/streaming.rs`, `handlers/videos.rs` |
| HLS routes | `crates/ferrofin-api/src/handlers/hls.rs` |
| HLS orchestration | `crates/ferrofin-hls/src/hls_stream_manager.rs` |
| Playlist format | `crates/ferrofin-hls/src/master_playlist.rs`, `dynamic_hls_playlist_generator.rs` |
| Session / progress | `crates/ferrofin-api/src/handlers/playstate.rs` |
| Subtitles | `crates/ferrofin-api/src/handlers/subtitles.rs`, `crates/ferrofin-model/src/dlna/stream_info.rs` |
| Trickplay | `crates/ferrofin-api/src/handlers/trickplay.rs`, `crates/ferrofin-core/src/trickplay_manager.rs` |
| Intro/outro segments | `crates/ferrofin-api/src/handlers/media_segments.rs` |
| Planning / seeking | `apps/ferrofin-server/src/planner.rs` |

---

## 1. The one-paragraph mental model

A client posts a **device profile** (a declarative statement of what containers,
video codecs, audio codecs, and subtitle formats it can handle) to
`POST /Items/{id}/PlaybackInfo`. The server compares that profile against the
probed `MediaSourceInfo` and returns the same source annotated with a
**`PlayMethod`** decision and, when transcoding is needed, a **`TranscodingUrl`**.
The client then treats one URL as its media source:

- `SupportsDirectPlay = true` → fetch `TranscodingUrl` (or construct
  `/videos/{id}/stream?Static=true&…`) as a normal ranged file download.
- `SupportsDirectPlay = false`, HLS sub-protocol → fetch the master `.m3u8` and
  play it as HLS.
- `SupportsDirectPlay = false`, HTTP sub-protocol → fetch the progressive
  `stream.{container}` URL and read the growing body.

Everything else — seeking, track switching, subtitles, trickplay, resume — is
either a query parameter on that URL or a separate, orthogonal endpoint.

---

## 2. Ground rules

### 2.1 Time is in ticks

One tick is 100 ns; there are **10,000,000 ticks per second**. Every duration and
position field is an `i64` tick count unless the name says otherwise
(`SegmentLength` and `MinSegments` are seconds).

```
1 s      = 10_000_000 ticks
1 min    = 600_000_000 ticks
1 h      = 36_000_000_000 ticks
2.5 h    = 90_000_000_000 ticks
```

### 2.2 Authentication

Clients normally authenticate with a user token:

```
X-Emby-Token: <access token>
```

`ApiKey`/`api_key` query parameters are also accepted, including on URLs the
server itself generated (`TranscodingUrl`, subtitle delivery, trickplay tiles).
Two practical rules:

- Use a **user** token for `PlaybackInfo` when posting a device profile. A bare
  API key with no user and a profile can fail with `400` (`Guid can't be empty`)
  because the server needs an effective user to resolve per-user resume state and
  policy.
- The token can appear in generated URLs. Treat those URLs as secrets and do
  not log them at info level.

### 2.3 Paths and query keys are case-insensitive

Jellyfin clients are inconsistent about casing (`/Videos/...` vs `/videos/...`,
`videos/...`, `VideoBitrate` vs `videoBitRate`). Ferrofin matches route paths
case-insensitively and binds query keys case-insensitively
(`crates/ferrofin-api/src/routes.rs`, `crates/ferrofin-api/src/extract/query`).
Build URLs with the casing the server gave you; do not "fix" it.

### 2.4 IDs

- Item IDs and media-source IDs are UUIDs; the canonical form is hyphenated
  lowercase (`8f3e…`). A **dashless** form (`8f3e…` without hyphens) appears in
  trickplay and some generated URLs.
- `PlaySessionId` is a **32-hex-digit, dashless** GUID minted fresh on every
  `PlaybackInfo` call (`Uuid::new_v4().simple()`). Echo it exactly; it is the
  join key between playback progress and the transcode job.
- `DeviceId` is the client's stable device identifier, sent on most requests.
  It scopes transcode kill/keep-alive operations.

### 2.5 Base URL

`TranscodingUrl` and subtitle `DeliveryUrl` values are **relative** (they start
with `/videos/...` or `/Videos/...`). Resolve them against the server's base
URL. Never assume the host in the URL is reachable from the client; a reverse
proxy or `BaseUrl` setting may change the externally visible prefix.

---

## 3. Endpoint map

Video playback touches these endpoints. Everything except `PlaybackInfo` and
`Sessions/*` is generally a GET the media player consumes directly.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/Items/{itemId}/PlaybackInfo` | Negotiate with a device profile |
| `GET` | `/Items/{itemId}/PlaybackInfo` | Static sources, no negotiation |
| `GET`/`HEAD` | `/Videos/{itemId}/stream` | Direct play (`Static=true`) or progressive transcode |
| `GET`/`HEAD` | `/Videos/{itemId}/stream.{container}` | Same, with an explicit output container |
| `GET`/`HEAD` | `/Videos/{itemId}/master.m3u8` | HLS master playlist |
| `GET` | `/Videos/{itemId}/main.m3u8` | HLS variant playlist (VOD) |
| `GET` | `/Videos/{itemId}/live.m3u8` | HLS playlist for a live/segmented source |
| `GET` | `/Videos/{itemId}/hls1/{playlistId}/{segmentId}` | HLS media segment / fMP4 init |
| `GET` | `/Videos/{itemId}/hls/{playlistId}/{segmentId}` | Legacy HLS segment route |
| `GET` | `/Videos/{itemId}/hls/{playlistId}/stream.m3u8` | Legacy HLS playlist route |
| `GET` | `/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/{startTicks}/Stream.{format}` | External subtitle |
| `GET` | `/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/subtitles.m3u8` | HLS subtitle playlist |
| `GET` | `/Videos/{itemId}/{mediaSourceId}/Attachments/{index}` | Embedded font/attachment |
| `GET` | `/Videos/{itemId}/Trickplay/{width}/tiles.m3u8` | Scrubbing thumbnail playlist |
| `GET` | `/Videos/{itemId}/Trickplay/{width}/{index}.jpg` | Thumbnail tile |
| `GET` | `/MediaSegments/{itemId}` | Intro/outro/recap/commercial segments |
| `POST` | `/Sessions/Playing` | Playback started |
| `POST` | `/Sessions/Playing/Progress` | Playback progress + transcode keep-alive |
| `POST` | `/Sessions/Playing/Ping` | Keep-alive only |
| `POST` | `/Sessions/Playing/Stopped` | Playback stopped; kill transcode |
| `DELETE` | `/Videos/ActiveEncodings` | Explicitly stop an encoding |

---

## 4. Step 1 — negotiate with `PlaybackInfo`

### 4.1 GET vs POST

`GET /Items/{id}/PlaybackInfo` returns the item's probe results as-is. It does
**not** negotiate: every source keeps its static/direct-play shape and there is
no `TranscodingUrl`. (The response still carries a `PlaySessionId`, but nothing
has been negotiated against it yet, so it is not a useful playback session on
its own.)

Use the `POST` form for actual playback. It is the only form that:

- takes the `DeviceProfile`,
- decides `DirectPlay` vs `Transcode`,
- fills `TranscodingUrl`, `TranscodingContainer`, `TranscodingSubProtocol`,
- mints the `PlaySessionId` the client must echo later,
- stamps per-subtitle `DeliveryMethod`/`DeliveryUrl`.

### 4.2 Request

```
POST /Items/{itemId}/PlaybackInfo
Content-Type: application/json
X-Emby-Token: <token>

{
  "DeviceProfile": { ... },
  "MaxStreamingBitrate": 8000000,
  "MediaSourceId": "<optional version id>",
  "AudioStreamIndex": 1,
  "SubtitleStreamIndex": -1,
  "StartTimeTicks": 0,
  "MaxAudioChannels": 2,
  "EnableDirectPlay": true,
  "EnableDirectStream": true,
  "EnableTranscoding": true,
  "AllowVideoStreamCopy": true,
  "AllowAudioStreamCopy": true,
  "AutoOpenLiveStream": false,
  "AlwaysBurnInSubtitleWhenTranscoding": false
}
```

The same fields can be supplied as query parameters; **query wins over body**
(`crates/ferrofin-api/src/handlers/media_info.rs`, `flags_from` and
`post_playback_info`). All capability booleans default to `true`.

Key inputs:

| Field | Meaning |
| --- | --- |
| `DeviceProfile` | What the client can decode. Required for real negotiation; without it the server uses the profile the device last reported via `/Sessions/Capabilities`, else returns static sources. |
| `MaxStreamingBitrate` | The client's ceiling in bit/s. The server caps video/audio encoders and may pick a different variant. |
| `MediaSourceId` | Pick a specific version (a movie can have several files). Omit to let the server sort and pick the first. |
| `AudioStreamIndex` | Desired audio track (media-stream `Index`, not ordinal). |
| `SubtitleStreamIndex` | Desired subtitle track; `-1` means "off". |
| `StartTimeTicks` | Resume offset. Threaded into the generated URL for both HLS and progressive playback. |
| `MaxAudioChannels` | Downmix ceiling; drives `-ac` for the encoder. |
| `EnableDirectPlay` / `EnableDirectStream` / `EnableTranscoding` | Hard vetoes. If all are false (or policy forbids them), the server returns an unplayable source rather than silently transcoding. |
| `AllowVideoStreamCopy` / `AllowAudioStreamCopy` | Whether a transcode may copy rather than re-encode that stream. |
| `AlwaysBurnInSubtitleWhenTranscoding` | Force the selected subtitle into the video whenever any transcode happens. |

### 4.3 Response

```json
{
  "MediaSources": [
    {
      "Protocol": "File",
      "Id": "a1b2c3d4e5f6...",
      "Path": "/media/movies/example.mkv",
      "Container": "mkv",
      "Size": 1234567890,
      "Name": "Example",
      "RunTimeTicks": 72000000000,
      "Bitrate": 12345678,
      "SupportsDirectPlay": false,
      "SupportsDirectStream": false,
      "SupportsTranscoding": true,
      "TranscodingUrl": "/videos/a1b2.../master.m3u8?DeviceId=dev&MediaSourceId=a1b2...&VideoCodec=h264&AudioCodec=aac&VideoBitrate=8000000&AudioBitrate=192000&PlaySessionId=3f2c...&ApiKey=...&SegmentContainer=ts&TranscodeReasons=ContainerNotSupported",
      "TranscodingContainer": "ts",
      "TranscodingSubProtocol": "hls",
      "MediaStreams": [ ... ],
      "DefaultAudioStreamIndex": 1,
      "DefaultSubtitleStreamIndex": null,
      "HasSegments": true,
      "ETag": "..."
    }
  ],
  "PlaySessionId": "3f2c9a...32hex..."
}
```

(Only the interesting fields are shown; the real DTO has ~60 fields. See
Appendix B.)

### 4.4 How the play method is chosen

The exact pipeline (`set_device_specific_data` in
`crates/ferrofin-api/src/handlers/media_info.rs`) is:

1. **Resolve the item and its sources.** Get the item's `MediaSourceInfo`
   records, narrowed to `MediaSourceId`/`LiveStreamId` if given.
2. **Stamp `HasSegments`.** The server asks the media-segment store whether the
   item has intro/outro rows; web clients will not even fetch
   `/MediaSegments/{id}` unless this is true.
3. **Mint `PlaySessionId`.** Fresh per call, dashless 32-hex.
4. **Apply client vetoes to the source**:
   - `EnableDirectPlay=false` → `SupportsDirectPlay=false`.
   - `EnableDirectStream=false` or `AllowVideoStreamCopy=false` →
     `SupportsDirectStream=false`.
   - `EnableTranscoding=false` or policy forbids it → `SupportsTranscoding=false`
     and clear `TranscodingUrl`.
   - Video sources additionally require the user's remux permission for
     direct-stream.
5. **Build a `MediaOptions`** with the profile, stream picks, bitrate cap, and
   `enable_direct_stream = false` (see 4.5).
6. **Ask the `StreamBuilder` for the optimal stream.** It evaluates the
   direct-play profiles first, then transcoding profiles, collecting
   `TranscodeReasons` for every failed match.
7. **Branch on the result**:
   - `DirectPlay` → mark `SupportsDirectPlay`/`SupportsDirectStream`, stamp
     subtitle delivery, stop.
   - otherwise, video → force `PlayMethod::Transcode`, mark direct play/stream
     false, build the `TranscodingUrl` from the negotiated profile, append copy
     vetoes and subtitle/burn-in parameters, stamp subtitle delivery.
8. **Sort the sources** so the item's own version, direct-playable files, then
   direct-streamable/transcodable files come first, with bitrate fit as a
   tiebreaker.
9. **Return the first source plus the play session id.**

### 4.5 The important asymmetry: video is DirectPlay or Transcode

For video, `enable_direct_stream` is forced off during PlaybackInfo negotiation
(`media_info.rs`: "direct-stream http streaming is currently broken"). The
practical consequence for implementers:

- A compatible file is **DirectPlay**. The client gets a static file URL.
- An incompatible file is **Transcode**. It is negotiated against the client's
  *transcoding* profiles, so the container and codecs come from the profile,
  not from the source. The server may still *copy* the video stream and only
  re-encode audio — but the wire method is `Transcode`.
- `DirectStream` remains meaningful for audio; do not build video logic that
  expects a remux-only HTTP endpoint. Treat "video needs remux" as a transcode
  whose video codec is `copy`.

### 4.6 Reading the `TranscodingUrl`

`StreamInfo::to_url` (`crates/ferrofin-model/src/dlna/stream_info.rs`) builds the
URL. It is relative, uses `/videos/{id}/…` (lowercase), and appends parameters in
this order:

```
/videos/{itemId}/master.m3u8
  ?DeviceProfileId=…              # when the client supplied one
  &DeviceId=…
  &MediaSourceId=…
  &Static=true                    # only for a direct-stream URL
  &VideoCodec=h264,hevc
  &AudioCodec=aac,mp3
  &AudioStreamIndex=1
  &SubtitleStreamIndex=2
  &VideoBitrate=8000000
  &AudioBitrate=192000
  &AudioSampleRate=48000
  &MaxFramerate=30
  &MaxWidth=1920
  &MaxHeight=1080
  &StartTimeTicks=600000000
  &SegmentContainer=ts
  &SegmentLength=6
  &MinSegments=1
  &PlaySessionId=3f2c…
  &ApiKey=…
  &LiveStreamId=…
  &TranscodingMaxAudioChannels=2
  &RequireAvc=false
  &EnableAudioVbrEncoding=true
  &Tag=…
  &SubtitleMethod=Encode
  &SubtitleCodec=…
  &TranscodeReasons=ContainerNotSupported,VideoCodecNotSupported
```

Not every optional field appears. Booleans such as `RequireNonAnamorphic`,
`EnableSubtitlesInManifest`, `EnableMpegtsM2TsMode`, `EstimateContentLength`,
and `CopyTimestamps` are emitted only when true; `TranscodeSeekInfo` only when
not `Auto`. `RequireAvc` and `EnableAudioVbrEncoding` are always emitted on a
non-direct URL. The exact set is what `StreamInfo::to_url` writes — do not
reconstruct it by hand.

Notes:

- The URL may be a **progressive** URL instead:
  `/videos/{id}/stream.{container}?…`. That happens when the chosen transcoding
  profile's protocol is `http`, or when no profile was chosen but the source can
  still be transcoded.
- `Static=true` only appears for direct-stream (audio in practice).
- `SubtitleStreamIndex` is emitted unless the delivery method is `External`
  (in which case the client uses the subtitle `DeliveryUrl` instead), and `-1`
  is treated as "off".
- `TranscodingUrl` is the source of truth. Do not reconstruct it from
  `SupportsDirectPlay` alone; the query carries negotiated caps (bitrate,
  channels, segment container) that the server will enforce.

### 4.7 Decision table for the client

| Response | What to do |
| --- | --- |
| `SupportsDirectPlay = true` | Build `/videos/{id}/stream?Static=true&MediaSourceId=…&ApiKey=…` (or `stream.{container}`). Direct play does **not** get a `TranscodingUrl`. |
| `SupportsDirectPlay = false`, `TranscodingSubProtocol = "hls"` | Use `TranscodingUrl` → master playlist. |
| `SupportsDirectPlay = false`, `TranscodingSubProtocol = "http"` | Use `TranscodingUrl` → progressive stream. |
| `SupportsTranscoding = false` and `SupportsDirectPlay = false` | Nothing playable. Show the client's "cannot play" error; do not retry with a guessed URL. |
| `ErrorCode = "NoCompatibleStream"` | Surface the error; the server has already decided. |

---

## 5. Step 2 — direct play

### 5.1 URL and headers

```
GET /videos/{itemId}/stream?Static=true&MediaSourceId={sourceId}&ApiKey={token}
```

`Static=true` is what selects the direct path. Without it the same route falls
through to a transcode (`videos.rs` → `static_or_transcode`).

The response is a normal file response:

- `200 OK` with the whole file when there is no `Range`,
- `206 Partial Content` with `Content-Range` when `Range` is present,
- `HEAD` returns headers only,
- `Accept-Ranges: bytes` (unless the route is explicitly range-less),
- `Content-Type` from the container/extension (e.g. `video/mp4`,
  `video/x-matroska`, `video/mp2t`).

The server delegates this to a `ServeFile`-style handler
(`serve_static_file` in `handlers/streaming.rs`), so standard HTTP range
semantics apply. A client can seek by issuing a new ranged request.

### 5.2 Remote sources

If the media source's `Protocol` is `Http`/`Https`, the server relays the remote
target rather than reading a local file (`serve_remote_static`). It forwards the
client's `Range` header and mirrors the upstream status; if the upstream does not
advertise ranges, the response advertises `Accept-Ranges: none` and ignores
inbound range requests (`200`, whole body). Do not assume range support just
because the source is HTTP.

### 5.3 Live / infinite sources

A source with `IsInfiniteStream = true` (an in-progress recording, a live
buffer) is served as a **growing** body even on the static path: the server reads
the file as it is written, polls on empty reads, and ends the response when the
producer exits. The content type comes from `output_container` (default `ts`) or
the file extension.

### 5.4 Disc folders

A `BluRay`/`Dvd` source cannot be served as a plain file: `Static=true` still
falls through to the transcode path because only ffmpeg can read the disc
structure. This is a deliberate exception in `static_or_transcode`.

---

## 6. Step 3 — progressive transcode

### 6.1 When you get it

A video transcode is progressive when the negotiated transcoding profile's
sub-protocol is `http` rather than `hls`, or when no profile was chosen but the
source is still transcodable. The URL is:

```
GET /videos/{itemId}/stream.{container}?VideoCodec=…&AudioCodec=…
    &VideoBitrate=…&AudioBitrate=…&PlaySessionId=…&ApiKey=…
    &StartTimeTicks=…&TranscodeReasons=…
```

### 6.2 Semantics

The server starts ffmpeg writing to a file and serves that file while it grows
(`serve_growing_transcode` in `handlers/streaming.rs`):

- `Accept-Ranges: none`.
- No `Content-Length`; the body ends when the producer exits (or after a 30 s
  quiet period when there is no job observer).
- Empty reads poll every 50 ms; this is not a bug — it is how the server waits
  for the next chunk.
- `HEAD` returns an empty body with `Accept-Ranges: none`.

### 6.3 Seeking

Progressive playback cannot be ranged. To seek, the client closes the current
response and issues a **new** request with `StartTimeTicks` set to the target
position. The server plans a fresh ffmpeg job starting there (clamped to
`[0, runtime − 5 s]`; `planner.rs`, `fast_seek_ticks`).

The transcode output path is keyed on item, source, play session, device,
codecs, and container — **not** on `StartTimeTicks`
(`planner.rs`, `output_id`). A seek that reuses the same play session can
therefore resolve to the existing output file instead of starting a new
encoder. The reliable sequence is:

1. cancel the old HTTP request,
2. stop the old transcode (`POST /Sessions/Playing/Stopped`, or start from a
   fresh `PlaybackInfo` / `PlaySessionId`),
3. open a new request with the new `StartTimeTicks`,
4. keep sending progress/ping for the new session.

On the server side, if you implement this contract yourself, either key the
output path on the seek offset or explicitly kill the old job when
`StartTimeTicks` changes for the same session.

### 6.4 Keep-alive

Progressive jobs have a **10 s** ping timeout
(`PROGRESSIVE_PING_TIMEOUT_MS` in
`crates/ferrofin-mediaencoding/src/transcoding/manager.rs`). A progress report
refreshes it; if the client goes quiet for 10 s, the idle reaper kills the job.

---

## 7. Step 4 — HLS

HLS is the default segmented path for video transcodes and the path browsers
and TV clients use. It is a **VOD playlist over a live-growing segment cache**:
the playlist describes the whole timeline up front, while segments are produced
on demand.

### 7.1 Master playlist

```
GET /Videos/{itemId}/master.m3u8?<the TranscodingUrl query>
```

Response content type: `application/vnd.apple.mpegurl` (no charset on the HLS
route; trickplay adds `; charset=utf-8`). The master response also sets
`Expires: 0`. `HEAD` works and returns an empty body after resolving state (so a
missing item is still a `404`).

Shape (`master_playlist.rs`, `build_master_playlist`):

```m3u8
#EXTM3U
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",DEFAULT=YES,FORCED=NO,AUTOSELECT=YES,URI="a1b2.../Subtitles/2/subtitles.m3u8?SegmentLength=30&ApiKey=...",LANGUAGE="eng"
#EXT-X-STREAM-INF:BANDWIDTH=8192000,AVERAGE-BANDWIDTH=8192000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1920x1080,FRAME-RATE=23.976,SUBTITLES="subs"
main.m3u8?DeviceId=dev&MediaSourceId=a1b2...&VideoCodec=h264&AudioCodec=aac&...
#EXT-X-IMAGE-STREAM-INF:BANDWIDTH=...,RESOLUTION=320x180,CODECS="jpeg",URI="Trickplay/320/tiles.m3u8?MediaSourceId=a1b2...&ApiKey=..."
```

Details that matter:

- There is **no** `#EXT-X-VERSION` in the master.
- `BANDWIDTH`/`AVERAGE-BANDWIDTH` are the negotiated output bitrates
  (video + audio). A zero/absent encoder bitrate yields `BANDWIDTH=0`.
- `CODECS` is the output codec list (`avc1.<profile><level>`, `hvc1.…`,
  `av01.…`, `mp4a.40.2`, `ac-3`, `ec-3`, …). When the video is copied, the
  source codec string is reused.
- `RESOLUTION` and `FRAME-RATE` are emitted only when known. Frame rate is
  rounded to three decimals.
- `SUBTITLES="subs"` is present when the master also emits an
  `#EXT-X-MEDIA` subtitle group (selected HLS subtitle, or
  `EnableSubtitlesInManifest=true`). Burn-in (`SubtitleMethod=Encode`)
  suppresses the group.
- HDR streams may add `VIDEO-RANGE` and `SUPPLEMENTAL-CODECS`, plus extra
  SDR-compatibility variants (AV1/HEVC/H.264) that force a re-encode. Pure
  Dolby Vision may add a `dvh1`/`dav1` variant before the main one.
- Trickplay entries are `#EXT-X-IMAGE-STREAM-INF` lines, only when the item has
  trickplay data and `EnableTrickplay` is true (default).

### 7.2 Variant playlist

```
GET /Videos/{itemId}/main.m3u8?<query>
```

`main.m3u8` is the VOD media playlist for the normal timeline. For a live or
segmented source the master points at `live.m3u8` instead.

Shape (`dynamic_hls_playlist_generator.rs`, `create_main_playlist`):

```m3u8
#EXTM3U
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:6.000000, nodesc
hls1/main/0.ts?<query>&runtimeTicks=0&actualSegmentLengthTicks=60000000
#EXTINF:6.000000, nodesc
hls1/main/1.ts?<query>&runtimeTicks=60000000&actualSegmentLengthTicks=60000000
...
#EXT-X-ENDLIST
```

For fMP4 (`SegmentContainer=mp4`) the version is `7` and the playlist starts
with an init segment map:

```m3u8
#EXT-X-MAP:URI="hls1/main/-1.mp4?<query>&runtimeTicks=0&actualSegmentLengthTicks=0"
```

Other facts:

- `#EXT-X-TARGETDURATION` is the ceiling of the longest segment.
- `#EXTINF` has six decimal places and the literal `nodesc` title.
- Segment URLs carry the **verbatim original query** plus two parameters:
  - `runtimeTicks` — the segment's start time on the playlist timeline,
  - `actualSegmentLengthTicks` — its exact duration.
  These two are how the server knows which timeline point a segment request
  means; do not strip them.
- For a remuxed video with keyframe data, segment boundaries follow keyframes,
  so `#EXTINF` values can vary. For a re-encode they are equal length.
- Defaults: **3 s** per segment when re-encoding, **6 s** when copying video
  (and 3 s for live on non-Apple agents); `SegmentLength` overrides.
  `SegmentContainer` defaults to `ts`.

### 7.3 Segments

```
GET /Videos/{itemId}/hls1/{playlistId}/{segmentId}.{container}
```

Examples:

```
/Videos/a1b2.../hls1/main/0.ts?DeviceId=dev&...&runtimeTicks=0&actualSegmentLengthTicks=60000000
/Videos/a1b2.../hls1/main/-1.mp4?DeviceId=dev&...   # fMP4 init segment
```

- `playlistId` is `main` for the VOD variant and `live` for a live playlist.
- `segmentId` is the playlist segment index; `-1` is the fMP4 init segment.
- MIME types (`mime_for_extension` in `hls_stream_manager.rs`):
  - `.m3u8` → `application/vnd.apple.mpegurl`
  - `.ts` → `video/mp2t`
  - `.mp4` / `.m4s` → `video/mp4`
  - `.aac` → `audio/aac`
  - `.mp3` → `audio/mpeg`
  - otherwise the generic MIME table by extension.
- Segments are served from the transcode cache with normal range/HEAD support.

### 7.4 How a segment request is served

This is the heart of the design (`resolve_dynamic_segment` in
`hls_stream_manager.rs`):

1. **Init segment (`-1`)**: serve the fMP4 init the transcode wrote (via
   `-hls_fmp4_init_filename`). If it does not exist, start a transcode at the
   resume segment so the cached init matches the segments the client will play.
2. **Fast path**: if `{playlist}/{index}.{ext}` already exists on disk, serve it
   immediately. Mark the consumer active so the idle reaper does not kill the
   job mid-read.
3. **Serialize the decision** per playlist (a lock) so two concurrent requests
   cannot spawn two ffmpeg jobs writing the same files.
4. **Re-check** the fast path after acquiring the lock.
5. **If a job is already running**:
   - Find the front of its current segment run (`-start_number` and the files on
     disk, including in-progress `.tmp` files).
   - If the requested index is within `SEGMENT_WAIT_GAP = 2` of that front,
     **wait** for the running job to produce it (this is normal read-ahead; no
     restart).
   - Otherwise treat it as a **seek**: plan the new job, kill the old one
     *without deleting its produced segments*, and start from the requested
     index. Later backward seeks are served from the disk cache.
6. **Start a new job** at the requested index and wait for the segment file.
   - The job uses `-ss` for input seek, `-start_number` for the segment number,
     and `-output_ts_offset` so the segment's timestamps align with its playlist
     position.
7. **Restart circuit breaker**: after 3 consecutive dead transcodes for the same
   playlist, fail fast with a `5xx` for a 10 s cooldown instead of spawning
   ffmpeg per request. `5xx` (not `404`) is intentional: HLS clients retry
   `5xx` with backoff, whereas they skip a `404` and walk the entire playlist,
   spawning a doomed job per segment.

### 7.5 Live playlists

`GET /Videos/{itemId}/live.m3u8` is used when the source is a segmented live
stream (live TV). The server starts an HLS job (EVENT-style) and serves the
playlist ffmpeg writes, waiting until at least `MinSegments` segments exist
before answering. It does **not** regenerate the playlist; ffmpeg owns it. A
later request reuses an existing file without spawning.

### 7.6 Seeking in HLS

HLS is simpler for the client than progressive transcode: the variant playlist
contains the full timeline, so the player can seek locally and request the
matching segment. The server's restart logic (7.4) handles jumping the encoder
to that point. Client rules:

- Keep the `runtimeTicks`/`actualSegmentLengthTicks` query on every segment URL.
- Do not reorder or rewrite segment URLs; the two tick parameters are used to
  compute the segment's end time and keep-alive accounting.
- A resume (`StartTimeTicks`) also needs the **fMP4 init** to be produced from
  the resume segment; the server handles that when `-1` is requested, provided
  `StartTimeTicks` was carried in the query. This is why the server emits
  `StartTimeTicks` even for HLS (Jellyfin itself omits it there).

---

## 8. Session lifecycle and keep-alive

A transcode is a server-side process with a finite lifetime. The client must
tell the server when playback starts, continues, and ends.

### 8.1 Start

```
POST /Sessions/Playing
Content-Type: application/json
X-Emby-Token: <token>

{
  "ItemId": "…",
  "MediaSourceId": "…",
  "PlaySessionId": "3f2c…",
  "PlayMethod": "Transcode",
  "PositionTicks": 0,
  "CanSeek": true,
  "IsPaused": false,
  "IsMuted": false,
  "AudioStreamIndex": 1,
  "SubtitleStreamIndex": 2,
  "PlaybackStartTimeTicks": 0,
  "VolumeLevel": 100
}
```

Returns `204 No Content`.

`PlayMethod` is validated: if the client claims `Transcode` but no live job
exists for that `PlaySessionId`, the server records `DirectPlay` instead
(`validate_play_method`). So send the method you actually use, and send the
right session id.

### 8.2 Progress

```
POST /Sessions/Playing/Progress
Content-Type: application/json
X-Emby-Token: <token>

{
  "ItemId": "…",
  "MediaSourceId": "…",
  "PlaySessionId": "3f2c…",
  "PositionTicks": 1234567890,
  "IsPaused": false,
  "IsMuted": false,
  "PlayMethod": "Transcode",
  "CanSeek": true,
  "AudioStreamIndex": 1,
  "SubtitleStreamIndex": 2
}
```

Returns `204 No Content`.

This does two things:

1. Records user data / resume position.
2. **Pings the transcode** (`ping_transcoding_job`), refreshing its kill timer.
   Without it, the idle reaper kills a healthy HLS transcode after 60 s.

Report progress every ~5–10 s while playing. Include `IsPaused`; the server
records it on the job. It does not replace the keep-alive — keep reporting or
pinging while the transcode should stay alive.

### 8.3 Ping

```
POST /Sessions/Playing/Ping?playSessionId=3f2c…
X-Emby-Token: <token>
```

Returns `204`. This only refreshes the transcode timer; it does not update the
resume position. Use it when the player is between segments or intentionally
not reporting progress (e.g. buffering, menu open) but the transcode should stay
alive. A ping for a session with no live job is a successful no-op.

### 8.4 Stop

```
POST /Sessions/Playing/Stopped
Content-Type: application/json
X-Emby-Token: <token>

{
  "ItemId": "…",
  "MediaSourceId": "…",
  "PlaySessionId": "3f2c…",
  "PositionTicks": 1234567890,
  "Failed": false
}
```

Returns `204`. This:

- kills the transcode job(s) for the session (and for the device when no play
  session id is given), deleting partial files,
- records the final position and watched state.

Always send this. Relying on the ping timeout leaves ffmpeg encoding to the end
of the item for nobody.

### 8.5 Explicit encoding stop

```
DELETE /Videos/ActiveEncodings?deviceId={deviceId}&playSessionId={playSessionId}
X-Emby-Token: <token>
```

Returns `204`. Kills that device/session's transcode and deletes partial files.
This is equivalent to the transcode-cleaning half of
`/Sessions/Playing/Stopped` and is useful when a client wants to stop the
encoder without also recording a final playback position.

### 8.6 Timeouts

| Job type | Idle timeout | Refreshed by |
| --- | --- | --- |
| HLS / DASH | **60 s** | progress, ping, or a segment request (consumer guard) |
| Progressive | **10 s** | progress or ping |

A segment request itself also marks the job active for the duration of the
request, so a client that is actively pulling segments does not need a
separate ping on every segment.

### 8.7 Device capabilities

Clients normally register their profile once:

```
POST /Sessions/Capabilities/Full
{ "DeviceProfile": { ... }, "PlayableMediaTypes": ["Video"], ... }
```

If `PlaybackInfo` is posted without a `DeviceProfile`, the server falls back to
the profile the device last reported here. If neither exists, negotiation
degrades to the static sources. Send the profile in every `PlaybackInfo` request
if you want deterministic behaviour.

---

## 9. Seeking and resume in detail

There are three different seek mechanisms; pick the one that matches the
transport.

### 9.1 Direct play

Use HTTP `Range` on the same `Static=true` URL. The file is immutable, so
seeking is free and instant.

### 9.2 HLS

The variant playlist has the full timeline. Seek locally and request the target
segment. The server restarts the encoder if the target is more than two segments
ahead of the running job; earlier segments are served from the disk cache. No
`StartTimeTicks` is needed on segment requests; it is only needed on the
**initial** master/variant request to seed a resume-aware fMP4 init.

### 9.3 Progressive

Close the response and re-request `stream.{container}` with
`StartTimeTicks={target}`. The server clamps the seek to
`[0, runtime − 5 s]`. See 6.3 for the play-session caveat: stop the old
transcode or re-negotiate with a fresh session so the reused output path does
not serve the old growing file.

### 9.4 Resume

To resume at `P` seconds:

1. Post `PlaybackInfo` with `StartTimeTicks = P × 10_000_000`.
2. Play the returned URL.
3. The server places `StartTimeTicks` in the URL so the transcode starts at
   `P` (and, for fMP4, so the init segment matches).
4. For direct play, `StartTimeTicks` is irrelevant to the file bytes; the client
   still range-seeks to `P`. Send the same offset in the first progress report.

### 9.5 Clamping

`fast_seek_ticks` (`apps/ferrofin-server/src/planner.rs`) clamps every seek to
`[0, runtime − 5 s]` when the runtime is known. Do not be surprised if a seek to
the last second lands 5 s earlier.

---

## 10. Track switching

### 10.1 Audio

- Direct play: the file has one or more audio streams; the client selects one
  locally if the container supports it. If it needs a different mix/stream than
  the file can provide, it must re-negotiate.
- Transcode: `AudioStreamIndex` in `PlaybackInfo` and in the transcode URL
  selects the source stream. Switching audio usually requires a new
  `PlaybackInfo` (or at least a new transcode URL) because the encoder's `-map`
  is fixed at job start. The HLS client should stop the old job and request a
  new master/variant with the new index.

### 10.2 Subtitles

Subtitle switching is the least disruptive:

- `SubtitleStreamIndex` in `PlaybackInfo` selects the default/selected track and
  determines the delivery method.
- External/HLS subtitles can be switched **without restarting video** by loading
  the new subtitle URL/playlist directly.
- Burn-in (`Encode`) is part of the video encode: switching to/from burn-in
  requires a new transcode (new PlaybackInfo / new URL).
- `-1` means "subtitles off".

### 10.3 Video

Video track switching (different resolution/bitrate) is an ABR concern. The
master playlist may contain multiple `#EXT-X-STREAM-INF` variants (SDR
compatibility, HEVC duplicate, adaptive bitrate). Let the HLS player choose
among them. For a non-HLS client, re-negotiate with a different
`MaxStreamingBitrate`/`MaxWidth`/`MaxHeight`.

---

## 11. Subtitles

### 11.1 Delivery methods

`SubtitleDeliveryMethod` is `Encode`, `Embed`, `External`, `Hls`, or `Drop`.
The server picks one per subtitle stream during negotiation
(`StreamBuilder::get_subtitle_profile`):

1. Prefer an **Embed** profile whose container/format matches the output and
   whose language matches (text or convertible text only).
2. Otherwise try an **External** profile whose format matches the stream as-is,
   then one where the stream can be converted to the profile's format.
3. Otherwise fall back to **Encode** (burn-in).

For an HLS output, embedded delivery is not considered
(`can_consider_embed_subtitle` short-circuits a transcode with an HLS
sub-protocol). The server instead matches a subtitle profile with method `Hls`
(or `External`), which yields the separate subtitle playlist described below.
If no profile matches, it falls back to `Encode`.

Each subtitle stream in `MediaSources[].MediaStreams` is stamped with:

```json
{
  "Type": "Subtitle",
  "Index": 2,
  "Codec": "subrip",
  "Language": "eng",
  "IsDefault": false,
  "IsForced": false,
  "DeliveryMethod": "External",
  "DeliveryUrl": "/Videos/a1b2.../srcId/Subtitles/2/600000000/Stream.vtt?ApiKey=...",
  "IsExternalUrl": false
}
```

If the source subtitle is itself an external HTTP URL in the same format, the
server can return it directly and set `IsExternalUrl = true`.

### 11.2 External subtitles

```
GET /Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/{startTicks}/Stream.{format}
```

- `startTicks` is the playback position when the request was negotiated (the
  server includes it so text subtitles can be time-shifted for a transcode that
  does not copy timestamps). Use the URL exactly as delivered.
- `format` is typically `vtt`, `srt`, `ass`, `ssa`, or `ttml` depending on the
  client profile and the source codec.
- `ApiKey` is appended when the server minted the URL.
- The route also exists without the start-position segment:
  `/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/Stream.{format}`.
- Container-scoped conversion routes exist under
  `/Videos/{itemId}/{container}/Subtitles/{index}/{format}` and with a trailing
  start-position segment; these are for clients that ask for a converted track
  directly.

The response is the converted subtitle text with a suitable text MIME type.

### 11.3 HLS subtitle playlist

```
GET /Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/subtitles.m3u8?SegmentLength=30&ApiKey=…
```

Shape:

```m3u8
#EXTM3U
#EXT-X-TARGETDURATION:30
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:30,
stream.vtt?CopyTimestamps=true&AddVttTimeMap=true&StartPositionTicks=0&EndPositionTicks=300000000&ApiKey=...
#EXTINF:30,
stream.vtt?CopyTimestamps=true&AddVttTimeMap=true&StartPositionTicks=300000000&EndPositionTicks=600000000&ApiKey=...
#EXT-X-ENDLIST
```

- Segment length defaults to **30 s** in the master playlist
  (`SUBTITLE_SEGMENT_LENGTH = 30`).
- The VTT requests carry `CopyTimestamps=true`, `AddVttTimeMap=true`, and the
  segment's start/end ticks, so the player gets correctly timed cues.
- The playlist is not supported for an item with unknown runtime; the server
  returns `400`.
- The master playlist advertises this URL in its `#EXT-X-MEDIA` subtitle group
  when HLS subtitles are selected or manifest subtitles are enabled.

### 11.4 Burn-in

`SubtitleMethod=Encode` (and `SubtitleStreamIndex`) on the transcode URL makes
the encoder render the subtitle into the video. Consequences:

- No separate subtitle stream is needed.
- The master playlist suppresses the subtitle group.
- Changing subtitles requires a new transcode.
- `AlwaysBurnInSubtitleWhenTranscoding=true` forces burn-in whenever the video
  is re-encoded, even if the client could render the subtitle externally.

### 11.5 Fallback fonts and attachments

For ASS/SSA and styled subtitles, the renderer may need fonts:

- `GET /FallbackFont/Fonts` lists available fallback fonts (when
  `FallbackFontPath` is configured).
- `GET /Videos/{itemId}/{mediaSourceId}/Attachments/{index}` extracts an
  embedded attachment (typically a font) with its MIME type. This route is
  intentionally token-agnostic (fonts are not secret) but still checks that the
  item is visible.

---

## 12. Trickplay (scrubbing thumbnails)

Trickplay is a separate image HLS playlist, advertised from the master playlist
as `#EXT-X-IMAGE-STREAM-INF`.

```
GET /Videos/{itemId}/Trickplay/{width}/tiles.m3u8?mediaSourceId=…&ApiKey=…
GET /Videos/{itemId}/Trickplay/{width}/{index}.jpg?mediaSourceId=…&ApiKey=…
```

Playlist shape (`trickplay_manager.rs`):

```m3u8
#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-VERSION:7
#EXT-X-MEDIA-SEQUENCE:1
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-IMAGES-ONLY
#EXTINF:120,
#EXT-X-TILES:RESOLUTION=320x180,LAYOUT=2x2,DURATION=30
0.jpg?MediaSourceId=8f3e...&ApiKey=...
#EXTINF:120,
#EXT-X-TILES:RESOLUTION=320x180,LAYOUT=2x2,DURATION=30
1.jpg?MediaSourceId=8f3e...&ApiKey=...
#EXT-X-ENDLIST
```

- `width` is the requested thumbnail width; the server must have pre-generated
  trickplay at that resolution, else `404`.
- `RESOLUTION` is the **single thumbnail** size; `LAYOUT` is the tile grid
  (`columns x rows`); `DURATION` is the seconds each thumbnail represents.
- `#EXTINF` is the total duration of the tile (individual duration ×
  thumbnails in that tile).
- `#EXT-X-TARGETDURATION` is the **tile count**, not the tile duration — a
  quirk of the upstream generator that this implementation preserves.
- Tile images are served as JPEG with `Content-Disposition: attachment` and
  **no** range support (a `Range` request returns the whole tile with `200`).
- `mediaSourceId` selects the version; it defaults to the item id.
- Content type for the playlist is
  `application/vnd.apple.mpegurl; charset=utf-8` (unlike the media HLS routes,
  which omit the charset).

---

## 13. Intro/outro skip segments

```
GET /MediaSegments/{itemId}?includeSegmentTypes=Intro,Outro
```

Response (a `QueryResult<MediaSegmentDto>`):

```json
{
  "Items": [
    { "Id": "…", "ItemId": "…", "Type": "Intro", "StartTicks": 0, "EndTicks": 900000000 }
  ],
  "TotalRecordCount": 1,
  "StartIndex": 0
}
```

- `includeSegmentTypes` may be repeated or comma-delimited; names are
  case-insensitive and unknown names are ignored.
- Supported types include `Intro`, `Outro`, `Recap`, `Commercial`, `Preview`,
  and `Unknown`.
- The client should only fetch this when `MediaSources[].HasSegments = true`
  from `PlaybackInfo`; web clients gate the skip button on that flag.
- No media is served here; the client seeks past the segment.

---

## 14. Error semantics

| Situation | Status | What the client should do |
| --- | --- | --- |
| Unknown item / no permission | `404` | Stop; do not retry. |
| Item has no playable source | `200` with `ErrorCode: "NoCompatibleStream"` | Show "cannot play". |
| Source cannot be played with the allowed methods (policy or client vetoes) | `200` with `SupportsDirectPlay`, `SupportsDirectStream`, and `SupportsTranscoding` all false | Show a "cannot play" / permission error. |
| Invalid/missing token | `401` | Refresh auth. |
| Missing required parameter (e.g. `SegmentLength` for subtitles) | `400` | Fix the request; do not retry unchanged. |
| Transcode could not start / died | `5xx` | Retry with backoff; HLS clients must not treat this as a skippable segment. |
| Segment not yet produced but job alive | request waits | No action; the server blocks until the file appears. |
| Trickplay/subtitle asset absent | `404` | Hide the feature; do not retry in a loop. |
| Restart breaker open (3 dead transcodes) | `5xx` | Back off for ~10 s; the source may be unreadable. |

A `404` on an HLS **segment** is different from a `404` on the playlist: the
server deliberately returns `5xx` for a failed segment so players retry rather
than walk the whole playlist.

---

## 15. Client implementation checklist

Negotiation:

- [ ] Register/send a `DeviceProfile` with direct-play, direct-stream (audio),
      and transcoding profiles.
- [ ] Post `PlaybackInfo` with `StartTimeTicks`, stream picks, and bitrate cap.
- [ ] Keep the returned `PlaySessionId` for the whole playback.
- [ ] Read `SupportsDirectPlay`, `TranscodingSubProtocol`, and `TranscodingUrl`
      together; do not infer from codecs alone.
- [ ] Handle `ErrorCode` and `SupportsTranscoding = false`.

Direct play:

- [ ] Use the `Static=true` URL.
- [ ] Send `Range` for seeking; accept `206`.
- [ ] Do not assume range support for remote HTTP sources.

Progressive:

- [ ] Re-request with `StartTimeTicks` to seek.
- [ ] Send progress every ≤10 s.
- [ ] Expect no `Content-Length` and `Accept-Ranges: none`.

HLS:

- [ ] Fetch the master, then the chosen variant.
- [ ] Preserve `runtimeTicks`/`actualSegmentLengthTicks` exactly.
- [ ] Fetch `-1.mp4` when `#EXT-X-MAP` is present.
- [ ] Keep `StartTimeTicks` in the initial URL for resume.
- [ ] Let the player choose among variants.

Session:

- [ ] `POST /Sessions/Playing` on start.
- [ ] `POST /Sessions/Playing/Progress` every ~5–10 s.
- [ ] `POST /Sessions/Playing/Ping` when you are not reporting progress but the
      transcode must live.
- [ ] `POST /Sessions/Playing/Stopped` on stop/exit/failure.
- [ ] Optionally `DELETE /Videos/ActiveEncodings` for an immediate kill.

Extras:

- [ ] Load external subtitle `DeliveryUrl`s when `DeliveryMethod=External`.
- [ ] Add the HLS subtitle playlist to the player when `DeliveryMethod=Hls`.
- [ ] Do not offer burn-in switching without re-negotiating.
- [ ] Fetch trickplay only when the master advertises it.
- [ ] Fetch `/MediaSegments/{id}` only when `HasSegments` is true.

---

## 16. Server implementation blueprint

If you are building the server side in another language, this is the portable
sequence. It mirrors the Rust modules but does not depend on them.

### 16.1 Data you need

```
MediaSource {
  id, path, protocol, container, runTimeTicks, bitrate, etag,
  supportsDirectPlay, supportsDirectStream, supportsTranscoding,
  mediaStreams[], defaultAudioStreamIndex, defaultSubtitleStreamIndex,
  transcodingContainer, transcodingSubProtocol, transcodingUrl,
  isInfiniteStream, hasSegments, ...
}

MediaStream {
  index, type, codec, language, isDefault, isForced,
  width, height, bitRate, channels, sampleRate,
  profile, level, realFrameRate, videoRange,
  isExternal, deliveryMethod, deliveryUrl, ...
}
```

### 16.2 Negotiation

```
resolve_sources(item, user, mediaSourceId)
stamp_has_segments()
playSessionId = random_hex_32()

for each source:
  apply_client_vetoes(source, flags, policy)
  options = {
    profile, bitrateCap, audioIndex, subtitleIndex, maxAudioChannels,
    enableDirectStream: false,       # video
    allowVideoCopy, allowAudioCopy
  }
  stream = optimal_stream(options, source)

  if stream.playMethod == DirectPlay:
    source.supportsDirectPlay = true
    source.supportsDirectStream = true
    stamp_subtitles(source, stream)
  else if video:
    source.supportsDirectPlay = false
    source.supportsDirectStream = false
    stream.playMethod = Transcode
    source.transcodingUrl = build_url(stream, playSessionId, token)
    source.transcodingContainer = stream.container
    source.transcodingSubProtocol = stream.subProtocol
    stamp_subtitles(source, stream)

sort_sources()
return PlaybackInfoResponse { mediaSources, playSessionId }
```

`build_url` must include every negotiated limit and the play session id; the
transcoder reads them back and enforces them.

### 16.3 Serving

```
if request.static and source can be read as a file:
  serve_file(path)                 # Range, HEAD, 206
else if output is HLS:
  master = build_master(plan(query))
  variant = build_variant(plan(query))
  segment(index, query) {
    if index == -1: serve_init()
    if file exists: serve_file()
    if running job and index within 2: wait_then_serve()
    else: kill_if_seek(); start_job(index); wait_then_serve()
  }
else:
  start_progressive_job(startTimeTicks)
  serve_growing_file()
```

### 16.4 Lifecycle

```
on_start:    validate playMethod; record session
on_progress: update position; ping_transcode(playSessionId)
on_ping:     ping_transcode(playSessionId)
on_stop:     kill_transcode(playSessionId); record final position
on_delete_encoding: kill_transcode(deviceId, playSessionId)
```

The transcode manager must have:

- a per-job idle timer (60 s HLS, 10 s progressive),
- a consumer guard so an in-flight segment read refreshes the timer,
- kill-on-stop that deletes partial files,
- a restart breaker per playlist.

### 16.5 Minimum viable HLS generator

If you only implement the common path:

- master: `#EXTM3U` + one `#EXT-X-STREAM-INF` + `main.m3u8?query`.
- variant: `#EXTM3U`, `#EXT-X-PLAYLIST-TYPE:VOD`, `#EXT-X-VERSION:3`,
  `#EXT-X-TARGETDURATION`, `#EXT-X-MEDIA-SEQUENCE:0`, `#EXTINF` + segment URLs,
  `#EXT-X-ENDLIST`.
- segment URL: `hls1/main/{i}.ts?{query}&runtimeTicks={start}&actualSegmentLengthTicks={len}`.
- segment serve: wait for the file, serve with `video/mp2t`.

Add fMP4 (`#EXT-X-MAP` + `-1.mp4`), subtitles, trickplay, and variants only
when you need them.

---

## 17. Pitfalls that cost real debugging time

1. **Forgetting `PlaySessionId`.** The transcode exists under that id. Omit it
   from progress reports and the job looks orphaned; omit it from the URL and
   `DELETE /Videos/ActiveEncodings` cannot find it.
2. **Treating video `DirectStream` as a thing.** It is not used for video
   negotiation. "Remux" is a transcode with `VideoCodec=copy`.
3. **Stripping query parameters from segment URLs.** `runtimeTicks` and
   `actualSegmentLengthTicks` drive keep-alive accounting and segment timing.
4. **Assuming `Accept-Ranges` on progressive.** It is `none`; seeking means a
   new request with `StartTimeTicks`.
5. **Assuming `404` is harmless for HLS segments.** It is not; the server uses
   `5xx` so players retry instead of walking the playlist.
6. **Not sending progress.** HLS jobs die after 60 s of silence; progressive
   after 10 s.
7. **Not sending stop.** The encoder runs to EOF, wasting CPU and filling the
   segment cache.
8. **Reusing a `PlaySessionId` across unrelated playback.** Mint a new one from
   each `PlaybackInfo`; a stale id can point at a dead job and make progress
   reports fail validation.
9. **Ignoring `HasSegments`.** The skip-intro UI will never appear if you fetch
   `/MediaSegments/{id}` without checking it.
10. **Assuming the master playlist has `#EXT-X-VERSION`.** It does not.
11. **Assuming the variant is live.** It is VOD with `#EXT-X-ENDLIST`; the
    segments are produced lazily behind it.
12. **Seeking within 2 segments ahead.** The server waits for the running job
    instead of restarting; that is intended and fast. A larger jump restarts.
13. **Reusing a progressive play session for a seek.** The output path does not
    include `StartTimeTicks`; stop the old transcode or re-negotiate with a fresh
    play session, otherwise you may be served the old growing file.
14. **Using an API key without a user for `PlaybackInfo` + device profile.**
    It can fail with `400` (`Guid can't be empty`). Use a user token.
15. **Rewriting relative URLs against the wrong base.** `TranscodingUrl` is
    relative and may already contain a reverse-proxy prefix expectation.
16. **Assuming HLS subtitles work for unknown runtime.** The server returns
    `400`; fall back to external or burn-in.

---

## Appendix A — URL and query reference

### A.1 Playback URL variants

```
Direct play (preferred):
  /videos/{itemId}/stream?Static=true&MediaSourceId={src}&ApiKey={token}

Direct play (explicit container):
  /videos/{itemId}/stream.{container}?Static=true&MediaSourceId={src}&ApiKey={token}

Progressive transcode:
  /videos/{itemId}/stream.{container}?VideoCodec=…&AudioCodec=…
    &VideoBitrate=…&AudioBitrate=…&PlaySessionId=…&ApiKey=…
    &StartTimeTicks=…&TranscodeReasons=…

HLS:
  /videos/{itemId}/master.m3u8?…           # master
  /videos/{itemId}/main.m3u8?…             # VOD variant
  /videos/{itemId}/live.m3u8?…             # live variant
  /Videos/{itemId}/hls1/{main|live}/{n}.{ts|mp4}?…&runtimeTicks=…&actualSegmentLengthTicks=…
  /Videos/{itemId}/hls1/{main|live}/-1.mp4?…   # fMP4 init
```

### A.2 `TranscodingUrl` query keys

| Key | Type | Meaning |
| --- | --- | --- |
| `DeviceProfileId` | string | The profile id, when the client supplied one |
| `DeviceId` | string | Client device id |
| `MediaSourceId` | string | Chosen version |
| `Static` | bool | Direct file serving |
| `VideoCodec` | csv | Output video codec(s), or `copy` |
| `AudioCodec` | csv | Output audio codec(s), or `copy` |
| `AudioStreamIndex` | int | Source audio stream index |
| `SubtitleStreamIndex` | int | Source subtitle stream index; `-1` = off |
| `VideoBitrate` / `VideoBitRate` | int | Video cap in bit/s |
| `AudioBitrate` / `AudioBitRate` | int | Audio cap in bit/s |
| `AudioSampleRate` | int | Output sample rate |
| `MaxFramerate` | float | Output frame-rate cap |
| `MaxWidth` / `MaxHeight` | int | Output dimension caps |
| `StartTimeTicks` | i64 | Resume/seek offset |
| `SegmentContainer` | string | `ts` or `mp4` |
| `SegmentLength` | int | Segment length in seconds |
| `MinSegments` | int | Live startup segment count |
| `PlaySessionId` | string | 32-hex session id |
| `ApiKey` | string | Token |
| `LiveStreamId` | string | Open live stream |
| `TranscodingMaxAudioChannels` | int | Downmix cap |
| `EnableSubtitlesInManifest` | bool | Add subtitle group to master |
| `EnableMpegtsM2TsMode` | bool | M2TS timestamps |
| `EstimateContentLength` | bool | Ask the encoder to estimate |
| `TranscodeSeekInfo` | enum | `Auto`/`Bytes` |
| `CopyTimestamps` | bool | Preserve source timestamps |
| `RequireAvc` | bool | Force H.264 |
| `EnableAudioVbrEncoding` | bool | Allow VBR audio |
| `Tag` | string | Source ETag |
| `SubtitleMethod` | enum | `Encode`/`Embed`/`External`/`Hls`/`Drop` |
| `SubtitleCodec` | csv | Output subtitle codecs |
| `TranscodeReasons` | csv | Why the source cannot direct-play |

Keys bind case-insensitively; `VideoBitrate` and `videoBitRate` are equivalent.

### A.3 Subtitle URLs

```
External:
  /Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/{startTicks}/Stream.{format}?ApiKey=…
  /Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/Stream.{format}?ApiKey=…

HLS:
  /Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/subtitles.m3u8?SegmentLength=30&ApiKey=…
  (VTT segments) stream.vtt?CopyTimestamps=true&AddVttTimeMap=true&StartPositionTicks=…&EndPositionTicks=…&ApiKey=…

Converted (container-scoped):
  /Videos/{itemId}/{container}/Subtitles/{index}/{format}
  /Videos/{itemId}/{container}/Subtitles/{index}/{format}/{startTicks}
```

### A.4 Trickplay and segments

```
Playlist: /Videos/{itemId}/Trickplay/{width}/tiles.m3u8?mediaSourceId=…&ApiKey=…
Tile:     /Videos/{itemId}/Trickplay/{width}/{index}.jpg?mediaSourceId=…&ApiKey=…

Segments: /MediaSegments/{itemId}?includeSegmentTypes=Intro,Outro
```

---

## Appendix B — DTO field reference

### B.1 `MediaSourceInfo` (response)

Fields that matter to a player:

| Field | Notes |
| --- | --- |
| `Protocol` | `File`, `Http`, `Https`, … |
| `Id` | Media source id |
| `Path` | Local path or remote URL |
| `Container` | Source container (e.g. `mkv`) |
| `RunTimeTicks` | Duration |
| `Bitrate` | Source bitrate |
| `SupportsDirectPlay` | Can send the file unchanged |
| `SupportsDirectStream` | Can remux over HTTP (audio in practice) |
| `SupportsTranscoding` | A transcode is permitted and possible |
| `TranscodingUrl` | The URL to play when not direct play |
| `TranscodingContainer` | Negotiated output container |
| `TranscodingSubProtocol` | `hls` or `http` |
| `MediaStreams` | Video/audio/subtitle streams |
| `DefaultAudioStreamIndex` | Track to start with |
| `DefaultSubtitleStreamIndex` | Track to start with, or null |
| `HasSegments` | Intro/outro data exists |
| `ETag` | Cache key for the source |
| `IsInfiniteStream` | Growing/live file |
| `VideoType` | `BluRay`/`Dvd`/`VideoFile`/… |
| `RequiredHttpHeaders` | Headers the player must send |

### B.2 `MediaStream`

| Field | Notes |
| --- | --- |
| `Index` | Stable stream index used in picks |
| `Type` | `Video`, `Audio`, `Subtitle`, `EmbeddedImage`, … |
| `Codec` | e.g. `h264`, `hevc`, `aac`, `subrip` |
| `Language` | ISO code |
| `IsDefault`, `IsForced` | Flags |
| `Width`, `Height` | Video dimensions |
| `BitRate` | Stream bitrate |
| `Channels`, `SampleRate` | Audio layout |
| `Profile`, `Level` | Video codec profile/level |
| `RealFrameRate`, `AverageFrameRate` | Frame rate |
| `VideoRange`/`VideoRangeType` | SDR/HDR/Dolby Vision |
| `DeliveryMethod` | Subtitle delivery |
| `DeliveryUrl` | Subtitle URL |
| `IsExternalUrl` | Subtitle URL points off-server |
| `IsExternal` | Subtitle lives in a sidecar |

### B.3 `PlaybackInfoResponse`

```json
{
  "MediaSources": [ ... ],
  "PlaySessionId": "32hex",
  "ErrorCode": "NoCompatibleStream" | "NotAllowed" | null
}
```

### B.4 Progress / start / stop

`PlaybackStartInfo` is a type alias of `PlaybackProgressInfo`; both accept:

`ItemId`, `SessionId`, `MediaSourceId`, `AudioStreamIndex`,
`SubtitleStreamIndex`, `IsPaused`, `IsMuted`, `PositionTicks`,
`PlaybackStartTimeTicks`, `VolumeLevel`, `Brightness`, `AspectRatio`,
`PlayMethod`, `LiveStreamId`, `PlaySessionId`, `RepeatMode`, `PlaybackOrder`,
`NowPlayingQueue`, `PlaylistItemId`, `CanSeek`.

`PlaybackStopInfo` accepts `ItemId`, `SessionId`, `MediaSourceId`,
`PositionTicks`, `LiveStreamId`, `PlaySessionId`, `Failed`, `NextMediaType`,
`PlaylistItemId`, `NowPlayingQueue`.

---

## Appendix C — a minimal device profile

Most clients do not need every field. This is enough to negotiate a typical
video:

```json
{
  "Name": "Example Client",
  "MaxStreamingBitrate": 8000000,
  "DirectPlayProfiles": [
    {
      "Container": "mp4",
      "VideoCodec": "h264,hevc,av1",
      "AudioCodec": "aac,mp3,ac3,eac3",
      "Type": "Video"
    }
  ],
  "TranscodingProfiles": [
    {
      "Container": "ts",
      "Type": "Video",
      "VideoCodec": "h264",
      "AudioCodec": "aac",
      "Protocol": "hls",
      "Context": "Streaming",
      "MaxAudioChannels": "2",
      "MinSegments": 1,
      "BreakOnNonKeyFrames": true
    }
  ],
  "SubtitleProfiles": [
    { "Format": "vtt", "Method": "External" },
    { "Format": "srt", "Method": "External" },
    { "Format": "ass", "Method": "External" },
    { "Format": "pgssub", "Method": "Encode" }
  ]
}
```

Real-world examples are checked into
`crates/ferrofin-model/tests/data/DeviceProfile-*.json` (Android, Android TV,
Safari, Roku, Tizen, WebOS, Jellyfin Media Player). Reading one is the fastest
way to understand what a production client advertises.

---

## Appendix D — constants

| Constant | Value | Source |
| --- | --- | --- |
| Ticks per second | `10_000_000` | `ferrofin-hls`, `ferrofin-mediaencoding` |
| Default segment length (re-encode) | `3 s` | `planner.rs` |
| Default segment length (copied video) | `6 s` | `planner.rs` |
| Subtitle segment length | `30 s` | `master_playlist.rs` |
| HLS idle timeout | `60 s` | `transcoding/manager.rs` |
| Progressive idle timeout | `10 s` | `transcoding/manager.rs` |
| Segment wait gap before restart | `2` segments | `hls_stream_manager.rs` |
| Restart breaker | 3 failures / 10 s cooldown | `hls_stream_manager.rs` |
| Progressive poll interval | `50 ms` | `live_tv.rs` |
| Progressive no-job timeout | `30 s` | `live_tv.rs` |
| Seek clamp | `runtime − 5 s` | `planner.rs` |

---

## Appendix E — minimal end-to-end traces

### E.1 Direct play

```
POST /Items/{id}/PlaybackInfo  →  200
  MediaSources[0].SupportsDirectPlay = true
  MediaSources[0].TranscodingUrl = null

GET /videos/{id}/stream?Static=true&MediaSourceId=…&ApiKey=…  →  200 (Accept-Ranges: bytes)
Range: bytes=0-  →  206

POST /Sessions/Playing          → 204
POST /Sessions/Playing/Progress → 204 (every 5–10 s)
POST /Sessions/Playing/Stopped  → 204
```

### E.2 HLS transcode

```
POST /Items/{id}/PlaybackInfo  →  200
  MediaSources[0].SupportsDirectPlay = false
  MediaSources[0].TranscodingSubProtocol = "hls"
  MediaSources[0].TranscodingUrl = "/videos/{id}/master.m3u8?…&PlaySessionId=…"

GET /videos/{id}/master.m3u8?…   → 200 #EXTM3U + #EXT-X-STREAM-INF
GET /videos/{id}/main.m3u8?…     → 200 VOD playlist with segment URLs
GET /Videos/{id}/hls1/main/0.ts?…&runtimeTicks=0&actualSegmentLengthTicks=… → 200 video/mp2t
GET /Videos/{id}/hls1/main/1.ts?…                                        → 200
...

POST /Sessions/Playing          → 204
POST /Sessions/Playing/Progress → 204 (also pings the transcode)
POST /Sessions/Playing/Stopped  → 204 (kills the job)
```

### E.3 Resume

```
POST /Items/{id}/PlaybackInfo
{ "StartTimeTicks": 600000000, "DeviceProfile": {…} }  → 200
  TranscodingUrl contains StartTimeTicks=600000000

GET /videos/{id}/master.m3u8?…&StartTimeTicks=600000000  → 200
GET /videos/{id}/main.m3u8?…&StartTimeTicks=600000000    → 200
GET /Videos/{id}/hls1/main/-1.mp4?…                       → 200 (init at resume segment)
GET /Videos/{id}/hls1/main/{resumeIndex}.ts?…             → 200
```

### E.4 Subtitle switch

```
GET /Videos/{id}/{src}/Subtitles/2/0/Stream.vtt?ApiKey=…  → 200 text/vtt
```

or, for HLS:

```
GET /Videos/{id}/{src}/Subtitles/2/subtitles.m3u8?SegmentLength=30&ApiKey=…  → 200
GET .../subtitles.m3u8 segments → stream.vtt?CopyTimestamps=true&AddVttTimeMap=true&…
```

---

## Where to look in this repository

- `crates/ferrofin-api/src/handlers/media_info.rs` — the whole negotiation,
  `PlaybackRequest`, `PlaybackFlags`, `StreamSelection`, `set_device_specific_data`,
  `sort_media_sources`, `apply_subtitle_delivery`.
- `crates/ferrofin-api/src/handlers/streaming.rs` — `static_or_transcode`,
  `serve_static_source`, `serve_static_file`, `serve_remote_static`,
  `serve_growing_transcode`.
- `crates/ferrofin-api/src/handlers/videos.rs` — `/Videos/{id}/stream` and
  `stream.{container}` wiring.
- `crates/ferrofin-api/src/handlers/hls.rs` — routes, query parsing,
  `master_playlist_response`, `served_file_response`,
  `stop_encoding_process`, attachments.
- `crates/ferrofin-hls/src/hls_stream_manager.rs` — `resolve_dynamic_segment`,
  `should_wait_for_running_job`, restart breaker, MIME mapping,
  `build_variant_playlist`, `live_playlist`.
- `crates/ferrofin-hls/src/master_playlist.rs` — `build_master_playlist`,
  `append_playlist`, `add_subtitles`, codec/range/framerate fields.
- `crates/ferrofin-hls/src/dynamic_hls_playlist_generator.rs` —
  `create_main_playlist`, exact `#EXTINF`/URL formatting.
- `crates/ferrofin-model/src/dlna/stream_info.rs` — `to_url`,
  `get_subtitle_profiles`.
- `crates/ferrofin-model/src/dlna/stream_builder.rs` — direct-play/transcode
  selection and `get_subtitle_profile`.
- `crates/ferrofin-api/src/handlers/playstate.rs` — start/progress/ping/stop,
  `validate_play_method`, `kill_session_transcodes`.
- `crates/ferrofin-mediaencoding/src/transcoding/manager.rs` — job registry,
  ping timeouts, kill semantics.
- `apps/ferrofin-server/src/planner.rs` — request → `TranscodePlan`, seek
  arithmetic, segment length defaults.
- `crates/ferrofin-model/tests/data/DeviceProfile-*.json` — production device
  profiles.
