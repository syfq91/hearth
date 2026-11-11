# Transcoding — how Ferrofin works, and how to port it

Ferrofin speaks Jellyfin's HTTP API, so its transcoding stack exists to make
one promise to a client: *give me a `DeviceProfile`, I will tell you exactly
how to play this file, and hand you a URL that plays it*. Everything below is
portable as a design, independent of Rust, Jellyfin, or Ferrofin's crate
layout. Code references are to this repo.

Scope: **opening the file for reading** (probing) and **transforming it on
demand** (direct play, remux, progressive transcode, segmented HLS). It does
not cover metadata scanning, trickplay thumbnails, or subtitle extraction
except where they touch the transcode path.

> For the end-to-end video playback protocol — `PlaybackInfo` negotiation,
> direct/progressive/HLS URLs, session keep-alive, seeking, subtitles,
> trickplay, and intro segments — see
> [`docs/VIDEO_PLAYBACK.md`](VIDEO_PLAYBACK.md). This document is the encoder
> and plan reference underneath it.

## Mental model

```
probe (scan time)            negotiate (PlaybackInfo)         serve (stream request)
─────────────────            ─────────────────────────        ─────────────────────────
ffprobe JSON ──────▶ MediaSourceInfo (streams, container,      StreamInfo (PlayMethod,
                     bitrate, persisted to the DB)             TranscodeReasons, codecs,
                      │                                       bitrates, TranscodingUrl)
                      └──────────────▶ DeviceProfile ──┤             │
                    (client capability spec)            ▼             ▼
                                                 HlsStreamRequest ─▶ plan ─▶ ffmpeg argv
                                                              (StreamStatePlanner)   │
                                                                          ┌───────┤
                                                                          ▼       ▼
                                                               TranscodeManager  HLS playlist
                                                               (spawns ffmpeg)   generator
```

Three decisions, in order:

1. **What is the file?** — once, at scan time (ffprobe + normalization).
2. **How should it be played?** — per request, from the client's
   `DeviceProfile` (`StreamBuilder` / `StreamInfo`).
3. **How do I turn it into that?** — one ffmpeg command per job
   (planner builds `argv`, runtime babysits the process).

> The planner does **not** re-decide playback. It *renders* the negotiated
> `StreamInfo` into an ffmpeg command. All capability policy lives in step 2.

---

## 1. Probing: ffprobe → `MediaSourceInfo`

Probing happens **before the client ever asks**. The scan runs ffprobe and
persists the result as stream rows; PlaybackInfo later joins those rows into
`MediaSourceInfo` without touching the file.

- Invocation: `MediaEncoderImpl::get_media_info_full`
  (`crates/ferrofin-mediaencoding/src/encoder/media_encoder.rs:651`). Args:
  `-i <input> -threads <n> -v warning -print_format json -show_streams
  -show_format [-show_chapters] [-show_frames -only_first_vframe]`
  (`probe_arguments`, `media_encoder.rs:400`). both `streams` and `format`
  null ⇒ error (ffprobe prints `{}` for unreadable input).
- DTOs: `probing/dtos.rs` — `InternalMediaInfoResult { streams, format,
  chapters, frames }`, `MediaStreamInfo` with flexible bool/int deserializers
  for ffprobe's string-encoded numbers.
- Normalization: `ProbeResultNormalizer::get_media_info`
  (`probing/probe_result_normalizer.rs:96`). Lowercase all tag keys first
  (case-insensitive lookup), then extract:
  - per-stream codec, resolution, avg/real framerate, bit depth (explicit or
    inferred from `pix_fmt`), aspect ratio, anamorphic (SAR/DAR/display
    matrix), ref frames, color space/transfer/primaries, interlaced,
    embedded-image classification, dispositions (default/forced/hearing
    impaired/original), audio channels/sample rate/layout, subtitle
    codec/language;
  - Dolby Vision side-data (`DOVI configuration record` → profile/level/
    rpu/el/bl/compat id) and HDR10+ frame side-data;
  - attachments from attachment-typed streams;
  - container (`format_name` normalized), total bitrate (container −
    estimated audio, single-video-stream only);
  - tags merged (first video/audio stream's tags overlaid by format tags):
    genres, title, season/episode/year, album/artists, MusicBrainz ids.
- `MediaEncoder::get_media_info` is the narrower `media_source` slice;
  `get_media_info_full` adds embedded metadata for the music scanner.

At playback time, `FerrofinMediaSourceManager::static_source`
(`crates/ferrofin-core/src/media_source_manager.rs:832`) assembles
`MediaSourceInfo` from the item row + persisted stream rows (dashless-GUID
`Id`, `Path`, container, size, runtime, bitrate, `Protocol=File`). The
three `Supports*` flags start true; server policy + client vetoes narrow
them before negotiation.

**Porting note:** decide your "what is the file" shape once — `MediaSource`
{ id, container, bitrate, streams[], … }, `MediaStream` { type, codec,
profile, level, width/height, fps, bitrate, channels, sample_rate,
disposition, … }. Every later stage reads only this shape, never ffprobe
output directly.

---

## 2. The decision: `StreamBuilder` (`PlayMethod` + `TranscodeReason`)

`StreamBuilder` (`crates/ferrofin-model/src/dlna/stream_builder.rs:41`) is a
**pure function**: `MediaOptions { profile, media_sources, picks, caps,
copy vetoes } → Option<StreamInfo>`. Two entries:
`get_optimal_video_stream` / `get_optimal_audio_stream` (`:96`, `:132`).
Filter sources by pinned `media_source_id`, build a candidate per source,
pick the winner by sort key `(prefer local-file direct play, prefer direct
over transcode, prefer file protocol, closest bitrate to cap)`.

### 2.1 `PlayMethod` — three outcomes

```rust
Transcode    // re-encode video and/or audio
DirectStream // remux: copy streams into a compatible container, no re-encode
DirectPlay   // send the file as-is
```

(Defined in `ferrofin-model/src/session.rs:34`.) The server forces
`enable_direct_stream = false` for video ("direct-stream http streaming is
currently broken", `crates/ferrofin-api/src/handlers/media_info.rs:986`),
so in practice video is DirectPlay or Transcode; audio still remuxes.

### 2.2 `TranscodeReason` — why not direct?

28 bitflags (`session.rs:416`, bitflags at `:482`). Each names one failed
check, e.g. `ContainerNotSupported`, `VideoCodecNotSupported`,
`AudioCodecNotSupported`, `VideoProfileNotSupported`,
`VideoLevelNotSupported`, `VideoResolutionNotSupported`,
`VideoBitDepthNotSupported`, `VideoFramerateNotSupported`,
`AnamorphicVideoNotSupported`, `InterlacedVideoNotSupported`,
`AudioChannelsNotSupported`, `AudioProfileNotSupported`,
`AudioSampleRateNotSupported`, `AudioBitDepthNotSupported`,
`ContainerBitrateExceedsLimit`, `VideoRangeTypeNotSupported`,
`VideoCodecTagNotSupported`, `StreamCountExceedsLimit`,
`VideoRotationNotSupported`, `DirectPlayError`. A response carries the
**union** — the client sees *every* reason, not just the first.

Reason groups (`stream_builder.rs:47`) — this is the remux rule:

```text
DIRECT_STREAM_REASONS = AUDIO_REASONS ∪ CONTAINER_NOT_SUPPORTED ∪ VIDEO_CODEC_TAG_NOT_SUPPORTED
```

Video is remux-eligible iff the failure set minus this group is empty.
`VideoCodecNotSupported`, resolution/level/profile, bitrate-exceeds-limit,
etc. are **not** remux-tolerant ⇒ full transcode.

### 2.3 DeviceProfile — the capability spec

A client sends (or the server stores via `POST /Sessions/Capabilities`):

```rust
DeviceProfile {
  name, id,
  max_streaming_bitrate, max_static_bitrate,        // caps, default 8M
  music_streaming_transcoding_bitrate,              // default 128k
  max_static_music_bitrate,
  direct_play_profiles: [ { container, video_codec?, audio_codec?, type } ],  // empty = wildcard
  transcoding_profiles: [ { container, video_codec, audio_codec, protocol: http|hls,
                            context: Streaming|Static, max_audio_channels,
                            segment_length, min_segments, copy_timestamps, … } ],
  container_profiles: [ { container?, conditions } ],      // container-level gates
  codec_profiles:     [ { codec_type: Video|VideoAudio|Audio, conditions,
                          apply_conditions, codec?, container? } ],  // codec-level gates
  subtitle_profiles:  [ { format?, method: Encode|Embed|External|Hls|Drop, … } ],
}
```

(`device_profile.rs:29`, `transcoding_profile.rs:21`, `codec_profile.rs:14`,
`profile_condition.rs:11`.) Lists are comma-delimited (`webm`,
`h264,hevc`, `-mp4` to negate). `ProfileCondition { condition: Equals |
NotEquals | LessThanEqual | GreaterThanEqual | EqualsAny, property:
AudioChannels | VideoProfile | VideoLevel | VideoRangeType | …,
value, is_required }` gates streams (e.g. `LessThanEqual VideoLevel 52`).

Real clients ship rich profiles (Chrome: webm+mp4 direct play, HLS mp4/ts
transcodes at `MaxAudioChannels: 2`, codec conditions on profile/level/
range/framerate). Your server must consume the profile, not hardcode around
it.

### 2.4 Video algorithm (`build_video_item`, `stream_builder.rs:432`)

1. **Pick streams.** Subtitle: requested index or highest-scored default.
   Audio: requested index, else default; candidate audio expands when
   `DefaultAudioIndexSource` includes `NONE` (all audio) / `LANGUAGE`
   (same language, then default-within-language) / `DEFAULT`.
2. **Eligibility gates.** `is_bitrate_limit_exceeded` = not-remote and
   `bitrate (default 40 Mbps when unknown) > cap` where cap resolves as
   request `maxStreamingBitrate` → profile `MaxStreamingBitrate`
   (`MediaOptions::get_max_bitrate`, `media_options.rs:102`). DVD/BluRay
   never direct-play; HLS-manifest containers never direct-play. If exceeded
   ⇒ reason `ContainerBitrateExceedsLimit` and both direct paths closed.
3. **Direct-play analysis** (`get_video_direct_play_profile`, `:1159`).
   Compute three failure sets: container-profile failures
   (`get_compatibility_container`), video-codec-profile failures
   (`get_compatibility_video_codec`, only profiles whose `apply_conditions`
   pass), per-candidate-audio failures (`get_compatibility_audio_codec_direct`
   incl. `AudioIsExternal`). Subtitle: if it has no `Drop|External|Embed`
   profile ⇒ `SubtitleCodecNotSupported`. For each direct-play profile in
   order, add `ContainerNotSupported` / `VideoCodecNotSupported` /
   `AudioCodecNotSupported` (no candidate audio with a supported codec) as
   applicable, union the sets, then:
   `failure_reasons.empty ⇒ DirectPlay`, else `failure_reasons &
   !DIRECT_STREAM_REASONS .empty ⇒ DirectStream`, else none. Rank survivors
   (play-method, then severity rank, then profile order) and take the
   first; if none has a method, take the first non-container entry's reasons
   (or `DirectPlayError`). This is why reasons are a union: the entry that
   gets "closest" dictates what the client sees.
4. **If direct won:** pin codecs to input, normalize container. DirectStream
   additionally runs `build_stream_video_item` with the profile's container
   and codec lists, and resolves subtitles for that method.
5. **Else transcode-profile ranking** (`get_video_transcode_profile`, `:732`).
   Filter `TranscodingProfiles` to `Type==Video && Context==request
   context` (optionally only `ts` for most-compatible). Rank each:
   video `1` (copy-compatible codec with no profile failures) / `2`
   (copyable but profile-constrained) / `3` (must re-encode); audio
   same 1/2/3 scale by codec compatibility. Sort by
   `(rank_video, rank_audio, order)`.
6. **Apply the winner.** Set container/sub-protocol, seek info, audio-channel
   cap, segment length, VBR flag. Run `build_stream_video_item`, force
   `PlayMethod::Transcode`, re-resolve subtitles for transcode, and — if the
   failure reason intersects `VIDEO_REASONS | ContainerBitrateExceedsLimit` —
   apply the profile's `conditions` as encoder constraints (max width/
   height/fps, require-AVC, require-non-anamorphic, per-codec
   profile/level/tag/rotation — `apply_transcoding_conditions`, `:2105`).

`build_stream_video_item` (`:826`) derives the actual targets: video codecs
(HLS keeps only `h264,hevc,vp9,av1`), audio codecs (HLS `ts` keeps
`aac,ac3,eac3,mp3`; `mp4` is wider), seed `stream_options` (`max_framerate`,
`{codec}-level/profile/videobitdepth`), downmix if channels exceed the cap
(sets the reason **and mutates** the negotiated source copy's channel count
so the encoder downmixes to it), compute `audio_bitrate =
min(existing, total→audio ladder)` and `video_bitrate = max(cap −
audio_bitrate, 64k)`.

### 2.5 Audio algorithm (`build_audio_item`, `:168`)

Simpler: container+codec match ⇒ DirectPlay candidate (plus a compatibility
re-check); container mismatch but codec match ⇒ DirectStream candidate
(`{ContainerNotSupported}` only), remux to `ts`/`mp4` with an HLS codec
whitelist check; else first transcode profile whose
`can_encode_to_audio_codec` holds. Audio bitrate =
`min(transcoding_bitrate ?? music_streaming_bitrate ?? cap ?? 128k, cap,
source.bitrate)`. The transcoder-support probe is consulted here — with no
ffmpeg (`NoEncoders`), **no audio transcode is ever negotiated**.

### 2.6 Bitrate caps

| Cap | Where | Effect |
|---|---|---|
| `MaxStreamingBitrate` (profile) | streaming context | `ContainerBitrateExceedsLimit` ⇒ kills direct paths; budget for `video_bitrate` |
| `MaxStaticBitrate` / `MaxStaticMusicBitrate` | static context | same check for static transcodes |
| `MusicStreamingTranscodingBitrate` (default 128k) + override + cap | audio | audio transcode target, min'd with source |
| Total→audio ladder | video with audio | ≤640k→128k, ≤2M→384k, ≤10M→1536k, ≤20M→3584k, else 7168k |

`ResolutionNormalizer` (`dlna/resolution.rs:84`) turns a bitrate cap into a
resolution cap (bitrate→width ladder scaled by `sqrt(fps/30)`, HDR ×0.8).

### 2.7 Subtitles: Embed / External / Encode

`get_subtitle_profile` (`stream_builder.rs:1506`):

1. **Embed** first, only for progressive (`sub_protocol != hls`) and only
   into `mkv`/`matroska` (`is_subtitle_embed_supported`, `:2561`). Exact
   format+text-class match preferred, then convertible text.
2. **External/HLS** (`get_external_subtitle_profile`, `:2588`): filters by
   method (HLS only when transcoding), language, and extraction gate —
   embedded subs are skipped during transcode unless the server can extract
   **or** the stream is PGS/VobSub (graphical, client-rendered — bypasses
   the gate). Conversion requires external support and a supported target
   (never to/from `ass`/`ssa`).
3. **Fallback: Encode** (burn-in, keeps source format).

`always_burn_in_subtitle_when_transcoding` flows from `MediaOptions` to a
`…=true` flag on the transcode URL. Per-stream delivery URLs are built by
`StreamInfo::get_subtitle_profiles` (`stream_info.rs:674`).

### 2.8 TranscodingUrl

`StreamInfo::to_url` (`stream_info.rs:448`) builds a *relative* URL:
HLS ⇒ `/videos/{id}/master.m3u8?…`, progressive ⇒
`/videos|audio/{id}/stream.{container}?…`, with PascalCase query params
(`VideoBitrate`, `AudioBitrate`, `MaxWidth/Height`, `SegmentContainer/
Length/MinSegments`, `PlaySessionId`, `ApiKey`, `StartTimeTicks`,
`TranscodeReasons`, copy vetoes, …). The server appends
`&AllowVideoStreamCopy=false` etc. when vetoed and stamps
`TranscodingContainer`/`TranscodingSubProtocol`. **The stream handlers
bind these case-insensitively** (`hls.rs:150`) — dropping one silently
loses the negotiated cap.

---

## 3. Planning: request → `EncodingJobInfo` → ffmpeg `argv`

The planner (`FerrofinStreamStatePlanner::plan`, `apps/ferrofin-server/src/
planner.rs:735`) turns an `HlsStreamRequest` + `StreamInfo` into a
`TranscodePlan { state: EncodingJobInfo, arguments: Vec<String>, env }`.
`build_arguments` (`planner.rs:1618`) is the linear orchestrator — upstream
split this across `EncodingHelper.*`; Ferrofin keeps that shape (small pure
fragments concatenated, never one mega-function).

Steps:

1. **Resolve source** — media streams (+ live-stream override), pick
   video/audio/subtitle (`media_stream()`, `planner.rs:483`).
2. **Copy decision** — `can_stream_copy_video` (`helper.rs:582`) /
   `can_stream_copy_audio` (`helper.rs:708`): compatible codec + container
   + flags. Two vetoes: user permission `video/audio_transcoding=false`
   forces copy; `prevent_hls_audio_copy` (re-encode-video HLS with
   `HlsAudioSeekStrategy::TranscodeAudio`) forces audio re-encode. Output
   codec becomes `"copy"` or the requested codec. Bitrates/channels are
   computed *before* this (a remux still needs them for playlist
   `BANDWIDTH`).
3. **Populate state** — segment length (3 s re-encode / 6 s copy / 3 s live),
   `ResolutionNormalizer::normalize` when bitrate < source (h264-equivalent
   scaling, else pin max dims to source), job type HLS vs Progressive.
4. **Side effects before argv is final** — attachment extraction (burn-in
   fonts), subtitle file resolution + charset, VAAPI driver probe per
   render node. `EncodingOptions` is read **per job** so dashboard edits
   apply to the next job; unreadable ⇒ defaults with warn-once.
5. **`build_arguments`** → `(argv, env)`. Builders return fragments with
   leading spaces; the planner `push_split`s them into argv. Paths go
   through `quote()` (`encoding_utils.rs:105`) so `split_args` round-trips
   them as one token (adversarial quoting tests at `:167` — a filename like
   `a"b.mkv` or `evil" -f lavfi -i …` must stay one argv element). A filter
   graph stays a **single** argv token.

**Pure vs side-effecting boundary (critical for testability):**
everything in §4 is pure (unit-testable, no spawn). Only three things spawn:
`Transcoder::get_process_output` (probes, images, trickplay),
`TranscodeManager::start_ffmpeg` (the live job), and planner I/O
(media-source manager, config reader, subtitle/attachment IO, render-node
fs read).

### 3.1 Input arguments

Order: `-ss`, device/init, `-analyzeduration`/`-probesize`, headers, sync/
pacing/source modifiers, `-i`, external inputs.

- **Seek (before `-i`, fast seek):** `start_time_ticks + remux_offset`
  (`remux_offset = 0.5 s` for HLS copy-video, to land on the keyframe),
  clamped to `[0, runtime−5 s]`, formatted `hh:mm:ss.mmm` (`format_time_parameter`,
  `media_encoder.rs:374`). Copy-video + HLS also gets
  `-noaccurate_seek` so copied audio starts at the video keyframe.
  `-output_ts_offset <sec>` on HLS seek restarts re-aligns PTS to playlist
  time (never `-copyts`, which preserves the source's arbitrary offset).
- **Analyze:** `-analyzeduration <ms×1000 | "200M"> -probesize 1G`
  (`DEFAULT_ANALYZE_DURATION`, `DEFAULT_PROBE_SIZE`, `media_encoder.rs:71`).
- **Headers:** `-user_agent/-referer` from `MediaSource.required_http_headers`.
  RTSP sources (probe path): `-rtsp_transport tcp+udp -rtsp_flags prefer_tcp`.
- **Sync/pacing/source:** audio/video sync → `-async 1` (pre-5.1) /
  `-isync 0` (5.1+); live-TV pacing → `-readrate 10` (≥5.0) /
  `-readrate_catchup` (≥8.0) else `-re`; `IgnoreInputDts → -fflags
  +igndts`; `RequiresLooping → -stream_loop`.
- **Input form** (`encoding_utils.rs:26`): local file `-i file:"/path"`,
  Blu-ray ISO `bluray:"…"`, multi-file `concat:"a|b"`, `://` URL or
  non-file protocol `"<url>"`.
- **External inputs** (`helper.rs:1861`): text sidecar seeks like the media
  (`-ss` + `-isync 0`); **bitmap sidecar (`.sup`/VobSub) loads from start
  on every ffmpeg** (its start time is its first cue — seeking loses the
  display set) and syncs via `-isync`. External audio follows the same
  pattern; VobSub `.sub` → adjacent `.idx`.

### 3.2 Output arguments

**HLS** (`planner.rs:2496`):

```text
-avoid_negative_ts disabled -max_muxing_queue_size 2048 -f hls -max_delay 5000000
-hls_time <segment_length> -hls_playlist_type vod|event -hls_flags temp_file
-hls_segment_type mpegts|fmp4 [-hls_fmp4_init_filename stem-1.mp4
  -hls_segment_options movflags=+frag_discont+skip_sidx]
[-start_number N] [-hls_base_url hls/<stem>/] [-output_ts_offset <sec>]
-hls_segment_filename dir/stem%d.ts -hls_list_size 0 dir/stem.m3u8
```

`segment_container` `ts` (default) or `mp4` (fMP4). `temp_file` gives atomic
segments (ready the instant the file exists — halves time-to-first-segment).

**Progressive** (`planner.rs:2426`): one growing file, `-y <path>`. MP4
streaming: `-f mp4 -movflags frag_keyframe+empty_moov+delay_moov`.
Audio-only: `-vn -id3v2_version 3 -write_id3v1 1`. HLS + progressive-video
strip `-map_metadata -1 -map_chapters -1`.

**Video encoder** (`hw/encoder.rs:137`) — software / NVENC / VAAPI / QSV:

| Output | Software | NVENC | VAAPI | QSV |
|---|---|---|---|---|
| `h264` | `libx264` | `h264_nvenc` | `h264_vaapi` | `h264_qsv` |
| `hevc`/`h265` | `libx265` | `hevc_nvenc` | `hevc_vaapi` | `hevc_qsv` |
| `av1` | `libsvtav1` | `av1_nvenc` | `av1_vaapi` | `av1_qsv` |

Gated on `VideoFile` type (folder rips → software), `enable_hardware_encoding`,
encoder present in the probed list. Other codecs pass through validated
(vp9, prores) else `"copy"`. Audio: `aac_at` (Apple) → `libfdk_aac` →
`aac`; `mp3→libmp3lame`, `opus→libopus`, `vorbis→libvorbis`, …

**Bitrate ladder** (`video_bitrate_param_value`, `helper.rs:351`):

```text
requested (client VideoBitRate, capped to source unless upscaling)
  → bit-depth compensation (10→8: ×1.5/×1.2/×1.1/×1.05 by magnitude)
  → codec efficiency (HEVC/VP9 ×0.6, AV1 ×0.5)
  → low-bitrate floor boost (×4 ≤500k, ×2.5 ≤2M, …)
  → min(scaled, requested), clamp 400M
```

Shape per encoder is **not uniform**: `libx264/x265` get `-maxrate` (cap),
`libsvtav1`/NVENC/QSV get `-b:v -maxrate -bufsize` (target), QSV adds
`-rc_init_occupancy` and a 1 Mbps floor (below 1 M `h264_qsv` refuses).

**Preset/CRF/per-codec params** (`encoder_param`, `helper.rs:982`): x264
`veryfast` + `-crf 23` + `subme/me/lookahead` opts; x265 `-crf 28` + params;
SVT-AV1 preset 5–13 + `-svtav1-params` (≥5.1); VAAPI `-compression_level`
+ `-sei -a53_cc` (≥6.0); QSV preset + `-mbbrc 1` (h264/hevc only); NVENC
`p7–p1` + `-spatial-aq`. HLS defaults: VOD `veryfast`, live `superfast`.

**Profile/level:** `-profile:v:0` normalized (`constrainedbaseline→
baseline`, …), omitted for `av1_nvenc` / `h264_v4l2m2m` (option doesn't
exist — ffmpeg hard-errors). `-level` capped (h264 → 51, hevc → 150,
av1 → 15), **omitted on NVENC** (unreachable level ⇒ "Invalid Level"
failure), x265 via `-x265-params` only, AV1 remapped.

**Scale / fps / rotation:** software scale_filter keeps aspect, even dims
(`helper.rs:1734`); unknown dims use expression forms
(`scale=trunc(min(max(iw\,ih*a),MW)/2)*2:…`). HW chains use vendor
scalers (`scale_vaapi` / `vpp_qsv` / `scale_cuda`) only when size/format
changes. fps cap is `fps=` in the chain (head, or tail when deinterlacing —
needs neighbour frames) plus encoder `-r`. Rotation via `transpose_*`
inside the chains (scalers swap dims when ±90°).

**Keyframes (HLS):** `hls_video_key_frame_arguments` (`helper.rs:1531`):
forced-IDR encoders (NVENC/QSV) → `-forced-idr 1 -force_key_frames
expr:gte(t,n_forced*SEG)`; GOP-only → `-g/-keyint_min SEG·rate`; x264/x265/
VAAPI → force-key-frames (+ `-sc_threshold 0` for x264). Progressive pins
5 s. `libx265` + HLS + ffmpeg <7.0.1 → `-bf 0` (B-frames break fMP4 on iOS).

**Stream mapping:** `map_args` (`helper.rs:213`) maps `0:<ffmpeg-index>`
per stream (ffmpeg index ≠ stream `Index`; `find_index` counts same-path
priors). External audio → `1:<i>`; subtitles by delivery (`Hls|Drop` →
`-map -0:s`; `Embed` → embedded or external; graphical external →
`1:<i> -sn`). With `-filter_complex`, a negative map (`-map -0:<video>`)
cancels the raw video.

**Bitstream filters (copy only, `bitstream.rs:316`):** `h264→
h264_mp4toannexb`, `aac→aac_adtstoasc`, `hevc→hevc_mp4toannexb` +
`hevc_metadata=remove_dovi=1|remove_hdr10plus=1` / `dovi_rpu=strip=1`,
`av1→av1_metadata=…`. HLS copied-audio trim:
`noise=drop='lt(pts*tb\,<seek>)'` (≥5.0).

**Audio output:** `-c:a <enc> [-bsf:a …] [-b:a | VBR] [-ac …] [-ar …]
[-af …]`. `-ac` suppressed when a `pan=` downmix filter is active. `-ar`:
client rate, else 48 kHz for ac-4; Opus snapped to `{8,12,16,24,48}k`.
Channels: requested → min(source) → encoder ceiling (mp3 2, dca/truehd 6,
else 8) → `TranscodingMaxAudioChannels` → HLS layout fix
(`3/4→stereo`, `5→6`, `7→8`).

**Audio filter chain:** for >stereo→stereo only,
`<downmix::algorithm_filter_string> , volume=<down_mix_audio_boost (2.0)>`.
The downmix table (`downmix.rs:16`) is **data** (Dave750, NightmodeDialogue,
Rfc7845, Ac4; 7.1 chains two `pan=`). A port copy-pastes these tables, not
a library call.

**Subtitle burn-in graphs:** text (software) =
`subtitles=filename='<escaped>':charenc=…:fontsdir=…` appended after
downscale/tonemap; text (HW) = `alphasrc` source → `subtitles=` →
`hwupload` → `overlay_*`; graphical (software) = second input preprocessed
to match DAR (`scale`/`pad`/`crop`) then `overlay=eof_action=pass`.

### 3.3 Hardware acceleration (NVENC / VAAPI / QSV only)

Only three vendors have chains. AMF/VideoToolbox/RKMPP/V4L2M2M selecting one
runs **software with a one-time warning** — by owner decision, an unverified
GPU pipeline that emits silent green frames is worse than a slow encode.
Supporting one means porting its chain **and** its encoder/bitrate arms,
verifying on real hardware, adding it to `hardware_path_is_ported`.

- **Device init** (`hw/device_init.rs`): aliases `qs/va/dx11/vt/rk/ocl/cu/
  dr/vk`. QSV is **never direct** — always derived (Linux VAAPI pinned
  `driver=iHD,kernel_driver=i915,vendor_id=0x8086`; Windows D3D11 by
  adapter or Intel vendor id). Priority: render-node path > vendor_id >
  kernel_driver. VAAPI driver env (`LIBVA_DRIVER_NAME`, `AMD_DEBUG`) rides
  in the **per-child env**, not the server process (C# mutated the server).
- **Decoders** (`hw/decoder.rs:428`): `-hwaccel <type>
  [-hwaccel_output_format …]` + named codec decoder where applicable
  (`h264_qsv`, `hevc_cuvid`, …). Gates: `HardwareDecodingCodecs` allowlist,
  10-bit HEVC/VP9 switches, HEVC RExt (10/12-bit), Hi10P (ARM64 VT only).
  `-noautoscale` after a named HW decoder (else ffmpeg inserts a software
  scaler behind it). Rotation stripped via `-display_rotation 0` (≥6.0) +
  `transpose_*` in the chain.
- **Filter chains** (`hw/sw_chain.rs:126`): every vendor produces the same
  `FilterChain { main, sub, overlay }` triple, each with a **software
  fallback**. Software shape: `setparams(HDR) → [deinterlace] → scaled →
  format=yuv420p → [tonemap] → [subtitles]`. NVENC stays in VRAM
  (`scale_cuda/tonemap_cuda/overlay_cuda`), downloads/uploads only at the
  boundary. VAAPI: iHD full VPP chain (scale/tonemap/transpose/`overlay_vaapi`);
  AMD+Vulkan via libplacebo; i965/legacy limited (no tonemap/transpose/
  overlay ⇒ subtitles force `hwdownload`). QSV: Linux on VAAPI, Windows on
  D3D11; Windows two-pass when procamp/RExt.
- **Tonemap — five paths**: software `tonemapx` (jellyfin-ffmpeg) or vanilla
  `zscale`; OpenCL/CUDA `tonemap_{opencl,cuda}`; Vulkan `libplacebo`;
  Intel VPP `tonemap_vaapi` / `vpp_qsv:tonemap=1`; VideoToolbox
  `tonemap_videotoolbox`. Every chain starts with `setparams` (HDR input
  tagged `bt2020/smpte2084|arib-std-b67`, SDR output `bt709`). No explicit
  `-pix_fmt`: 8-bit is enforced *in the graph* (`format=yuv420p`) because
  `libx264` would otherwise emit High10 (browser MSE refuses). HEVC output
  forced 8-bit Main for the same reason.
- **Capability gating per chain**, with per-node fallback but no silent
  half-pipelines. Quality preamble: `-low_power 1` (Intel VDEnc) +
  `-async_depth 1` (i915 hang kernels).

---

## 4. Runtime: jobs, throttling, cleanup

### 4.1 Registry and `start_ffmpeg`

`TranscodeManagerImpl` (`crates/ferrofin-mediaencoding/src/transcoding/
manager.rs:595`) holds `jobs: Mutex<Vec<RegisteredJob>>`. A `RegisteredJob`
(`:485`) names `play_session_id / path / job_type (Progressive|Hls|Dash) /
device_id / live_stream_id`, carries `active_request_count` (consumers;
`0` starts the idle countdown), `last_activity`, a `RunningJob` (child,
throttle progress, cleaner task, dashboard `TranscodingInfo`,
`segment_endings`, `served_segments`, `start_number`), and `user_id +
encodes` for caps.

`start_ffmpeg` (`:1048`):

1. **Admit + register first** (`register_job_for_user`, `:961`): limits check
   + insert under one lock. Job starts with `active_request_count=1` (the
   startup wait is a consumer — the idle reaper can't kill a cold start).
   Full ⇒ evict the longest-idle same-user/same-device job, else refuse
   with `ServiceError::Busy` + `Retry-After: 10`s. **No queue** — past the
   cap is an immediate retry.
2. **Prepare output**: create dirs, ffmpeg log
   (`FFmpeg.{Transcode|Remux|DirectStream}-<time>_<source>_<8hex>.log` in
   the server log dir so `GET /System/Logs` serves it), trace span.
3. **Spawn** via `SegmentTranscoder::start_transcode(SpawnRequest)` —
   `TokioSegmentTranscoder` (`tokio_segment_transcoder.rs:39`):
   `Command`, stdin piped, stdout null, stderr piped, **`kill_on_drop(true)`**,
   per-job `env`. Detached stderr pump (8 KiB reads) feeds the log and
   stores the latest parsed progress. Kill = write `q`, wait 5 s, SIGKILL.
4. **Wait for first output**: inotify (`FsWaiter`, `fs_wait.rs:44`) with
   100 ms fallback tick, bounded by `WAIT_FOR_FILE_TIMEOUT_MS = 180 s`
   (sized for 4K software HEVC→H.264; the old 30 s bound caused
   retry-from-zero loops). Progressive jobs sleep 1 s (+1.5 s if
   `read_at_native_framerate`) so bytes exist before the HTTP response.
5. **Arm throttle** (if eligible), **start segment cleaner** (if eligible),
   return the handle. Fail-if-exited-nonzero appends the stderr tail.

### 4.2 Progress

`progress.rs:56` parses ffmpeg's stderr stats (`fps=`, `time=`,
`size=`×1024, `bitrate=`×1024). `for_job` maps to `position = elapsed +
start_ticks`, `percent = 100·position/runtime`. The controls loop
(`run_transcode_controls`, 500 ms tick) reports to the session layer and
updates the dashboard `TranscodingInfo`. `DownloadObserver` (implements
`TranscodeStreamProgress`) accumulates `bytes_downloaded` / max-updates
`download_position_ticks` / records `served_segments` on response drop;
its `Drop` decrements `active_request_count` — cancellation-safe consumer
accounting.

### 4.3 Throttling (don't outrun the client)

Eligible: local `File`, video, runtime ≥ 300 s, `enable_throttling`
(`throttler.rs:135`). Gap = `max(throttle_delay_seconds, 60)` seconds (5 s
tick). HLS: pause iff `encoded_ticks − downloaded_ticks ≥ gap`.
Progressive: pause iff `encoded_bytes − bytes_downloaded ≥ target_gap`
(unknown size ⇒ **fail open**, never pause). Pause = write a key to ffmpeg
stdin: `p`/`u` (jelfin-ffmpeg *patched*) or `c`/newline (ffmpeg ≤6.1 exact —
`6.1.1` does *not* qualify). Complements: `-re` (native-rate sources) and
`-readrate 10` (+ `-readrate_catchup` ≥8) for copied-video HLS with segment
deletion, so ffmpeg can't sprint. Failing to resume before kill hangs a
paused ffmpeg (it ignores `q` in its command loop) — teardown resumes
*before* killing (`manager.rs:1454`).

### 4.4 Seek restart

**One long-lived ffmpeg per job for all segments** — not one per segment.
A "per-segment" cost only occurs on seek: kill the old job, spawn a new one
with `-ss`/`-start_number`/`-output_ts_offset` targeting the requested
segment (only those args differ). The old segments are **kept on disk** (so
a backward seek serves from cache); the tuner stays open if the restart
re-opens the same live stream.

### 4.5 Idle reaper, pings, stop

- Ping timeouts: **HLS/DASH 60 s, Progressive 10 s**
  (`RegisteredJob::ping_timeout_for`, `manager.rs:511`). The reaper sweeps
  periodically and kills jobs with zero consumers past timeout (browser tab
  kill, cast receiver drop) — and closes the tuner on reap.
- Keep-alive has **no dedicated route**: `POST /Sessions/Playing/Ping?play
  SessionId=` and `POST /Sessions/Playing/Progress` (with `is_paused`) both
  call `ping_transcoding_job`; a ping with no live job is a 204 no-op.
- `POST /Sessions/Playing/Stopped` (or legacy `DELETE /PlayingItems/{id}`)
  kills the session's jobs (`delete_files=true`) then records the stop.
- `DELETE /Videos/ActiveEncodings?deviceId&playSessionId` kills by session
  id if given, else by device id (case-insensitive).
- Every segment/playlist serve takes a `begin_request_guard` (RAII) pairing
  `OnTranscodeBeginRequest`/`OnTranscodeEndRequest`; drop decrements and
  restarts the countdown even on client disconnect.
- **Restart circuit breaker**: after 3 consecutive dead starts for a
  playlist, segment requests fail fast (5xx) for 10 s — a 404 would make
  hls.js skip-walk the playlist spawning doomed ffmpegs.
- **Cleanup**: on kill, delete every file in `output_dir` whose name
  contains the playlist stem. Rolling retention (segment cleaner, 20 s
  interval, 1.5 s delay): if `enable_segment_deletion`, delete segments
  ≤ highest *served* segment whose end ≤ `download_position − max(keep,
  20)s` — using actually-served ends, not arithmetic, so variable-length
  remux segments are exact. Boot wipes the whole transcode dir;
  `DeleteTranscodeFileTask` deletes files >1 day old but **skips while
  any playback is active**.

---

## 5. HLS delivery

### 5.1 Variant playlist (`main.m3u8`)

`DynamicHlsPlaylistGenerator::create_main_playlist`
(`crates/ferrofin-hls/src/dynamic_hls_playlist_generator.rs:96`) — always
**VOD + ENDLIST** (the client knows the full timeline up front):

```text
#EXTM3U
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-VERSION:7 (fMP4) | 3 (mpegts)
#EXT-X-TARGETDURATION:<ceil(max segment)>
#EXT-X-MEDIA-SEQUENCE:0
[#EXT-X-MAP:URI="<prefix>-1.mp4<query>&runtimeTicks=0&actualSegmentLengthTicks=0"]   # fMP4 only
#EXTINF:<6 decimals>, nodesc
<prefix><index><ext><query>&runtimeTicks=<start>&actualSegmentLengthTicks=<len>
…
#EXT-X-ENDLIST
```

Each segment URL carries `runtimeTicks` (its start) + `actualSegmentLength
Ticks` back into the segment request — these drive throttle position and
cleaner accounting.

**Segment lengths — two regimes** (`segment_lengths`, `:181`):
- **Re-encode (or no keyframes): equal-length.** Whole segments of exactly
  `desired/1000` s + a trailing remainder. ffmpeg *will* create keyframes
  (enforced above).
- **Stream-copy (remux) with keyframes: keyframe-cut.** Walk keyframe ticks,
  cut whenever a keyframe ≥ running desired cut. Variable lengths around
  desired. Keyframes come from **metadata-based** extractors only during a
  playlist wait (Matroska cue index; the ffprobe extractor runs in the
  background task) — `ferrofin-keyframes` crate. Only consulted for remuxed
  video with a media-source id.

**fMP4 init (`#EXT-X-MAP`)** is gated on the *first segment existing and
init size > 0* (never a size-stability poll — ffmpeg creates it empty at
header time, fills it at first flush). **Resume correctness:** the transcode
starts at the resume segment, not 0, so the cached init's moov edit list
matches the seek-offset segments. Spawn is never wrapped in `select!`/
cancellation — a cancelled spawn could orphan an unregistered ffmpeg writing
the same files.

### 5.2 Master playlist (`master.m3u8`)

`build_master_playlist` (`crates/ferrofin-hls/src/master_playlist.rs:273`) —
pure builder over the transcode plan, byte-testable against the Jellyfin
oracle. Header only (no `#EXT-X-VERSION` — upstream never emits one).
Total bitrate = audio + video output bitrate, **no floor, no source
fallback** (faithful quirk). Main variant URL `live.m3u8` (live) else
`main.m3u8`; one **shared mutable query dictionary** — later variants
inherit keys earlier ones set. Rotation forces `AllowVideoStreamCopy=false`.

- **Subtitles:** `#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs"` per text
  subtitle (HLS delivery or `enable_subtitles_in_manifest`; suppressed when
  burning in). `DEFAULT=YES` only for the HLS-delivered selected stream.
  URI `{msid}/Subtitles/{idx}/subtitles.m3u8?SegmentLength=30&ApiKey=…`.
  **No separate audio media groups** — one audio track is chosen by the
  planner; the master advertises it inside `CODECS`. Multiple audio ⇒
  re-request with a different `AudioStreamIndex`.
- **Dolby Vision base-layer variant** prepended (`VIDEO-RANGE=PQ,
  CODECS="dvh1.PP.LL[,audio]"`) for copied pure-DoVi when DoVi in range.
- **SDR compatibility entrances** for HDR copies: AV1/HEVC (if encodable)
  + H.264, same bitrate, `AllowVideoStreamCopy=false`, client picks by range.
  **HEVC L5.0 duplicate** for SDR HEVC level > 150 (Apple A10 workaround,
  via CODECS string-replace).
- **Adaptive variants:** two lower-bitrate entries when
  `enable_adaptive_bitrate_streaming` (off on local network, on live, on
  stream-copy, or without a requested videoBitrate). Variation table
  2M/1.5M/1M/500k/300k/200k/100k/50k by source bitrate.
- **Trickplay:** `#EXT-X-IMAGE-STREAM-INF` per width, skipped for
  live/audio.

**CODECS strings** (`hls_codec_strings.rs`): RFC 6381 — `avc1.…` (profile +
level hex), `hvc1.2.4/1.4.L<level>.B0`, `av01.<profile>.<level>.<bitdepth>`,
`vp09.…`, `mp4a.40.2/5` (LC/HE-AAC), `dtsc/dtsh/dtse`, `dvh1.PP.LL`/
`dav1.PP.LL`, plus constants (`ac-3`, `ec-3`, `Opus`, …). Level/profile from
source for copies, requested/defaults (41 / 120 / 19) for re-encodes; a
copied fractional level like `4.1` fails int-parse ⇒ video CODECS dropped
(faithful quirk). `SUPPLEMENTAL-CODECS` for DoVi/HDR10+, `VIDEO-RANGE`
SDR/HLG/PQ (re-encodes always SDR).

---

## 6. HTTP surface & wiring

### 6.1 Routes (Jellyfin contract)

| Purpose | Route |
|---|---|
| Negotiate | `POST /Items/{id}/PlaybackInfo` (query + body, query wins) |
| Master / variant / live | `GET /Videos/{id}/master.m3u8`, `…/main.m3u8`, `…/live.m3u8` (+ `/Audio/…` master/main) |
| Segments | `GET /Videos/{id}/hls1/{playlistId}/{segmentId}.{container}` (authenticated, `ApiKey` query works) |
| Progressive | `GET\|HEAD /Videos/{id}/stream`, `…/{container}`, `/Audio/{id}/stream`, `/Audio/{id}/universal` |
| Static / download | `GET /Items/{id}/Download`, attachments `…/Attachments/{index}` |
| Keep-alive | `POST /Sessions/Playing/Ping`, `POST /Sessions/Playing/Progress` |
| Stop | `POST /Sessions/Playing/Stopped`, `DELETE /Videos/ActiveEncodings` |

`PlaybackInfo` (`crates/ferrofin-api/src/handlers/media_info.rs:365`):
resolve user + visibility → `resolve_playback_media_sources` (live-stream id
wins, else static versions pinned by `media_source_id`; empty ⇒
`NoCompatibleStream`) → mint `play_session_id` (32 hex, before negotiation
so metrics key on it) → sort versions (preferred, direct-playable, file,
bitrate-fit) → `set_device_specific_data` (policy: user transcoding
permissions gate `SupportsTranscoding`/`SupportsDirectStream`, bitrate cap
from user/remote/profile) → per source, `apply_stream_decision` →
`StreamBuilder` → stamp `Supports*` + `TranscodingUrl` + subtitle
`DeliveryMethod/DeliveryUrl`.

Stream routes parse one shared query into `HlsStreamRequest`
(`crates/ferrofin-traits/src/media_encoding.rs:612`): `MediaSourceId`,
`PlaySessionId`, `DeviceId`, `SegmentContainer`, `SegmentLength`,
`RuntimeTicks`/`ActualSegmentLengthTicks`, `StartTimeTicks`, `Audio/Video
Codec`, `Video/AudioBitrate`, `MaxWidth/Height/Framerate`, `Profile/Level`,
`AllowVideo/AudioStreamCopy`, `SubtitleStreamIndex/Method/Codec`,
`TranscodeReasons`, `MinSegments`, raw `query_string` preserved (segment
URLs carry the client's params forward). `ServeFile` handles Range; the
handler only supplies `path + content_type + progress`.

`/Audio/{id}/universal` (`audio.rs:262`) is negotiation-driven: builds a
`DeviceProfile` from the query, runs the full PlaybackInfo pipeline, then
Redirect (302, remote `EnableRemoteMedia`) / HLS master / static file /
progressive transcode.

### 6.2 Composition root (`apps/ferrofin-server`)

Boot order: config/paths → ffmpeg discovery → `MediaEncoderImpl` →
media-source/session managers → `build_media_encoding` → inject into
`AppState`.

- **Discovery** (`bootstrap.rs:649`): `config.ffmpeg_path` →
  persisted `EncoderAppPath` (`encoding.json`) → `$PATH`; ffprobe derived
  by replacing the final component. Both must pass `-version`. A concurrent
  probe round collects `-filters/-encoders/-decoders/-hwaccels`, filter/BSF
  option probes, versions, `chromaprint` muxer into `FfmpegCapabilities`
  (fixed for process lifetime; failed probe ⇒ "absent" ⇒ software).
- **No-ffmpeg boot:** the server still boots, logs `ffmpeg unavailable —
  transcoding/playback will be disabled`, falls back to bare names with
  empty capabilities. The *real* HLS manager is still wired (jobs fail at
  spawn) while negotiation sees `NoEncoders` (no audio transcode offered,
  video degrades). Static/direct-play need no ffmpeg and keep working.
- **`AppState` seams** (`state.rs`): `hls: Arc<dyn HlsStreamManager>`,
  `subtitle_encoder`, `attachments`, `encoder_support:
  Arc<dyn EncoderSupport>` (`supports_encoder(name)`, used by negotiation).
  Pre-injection defaults (`DisabledHlsStreamManager`, `NoEncoders`) keep
  test constructors compiling.
- **`encoding.json` `EncodingOptions`** is read per job: thread count
  (`-1` = ffmpeg decides; per-request `CpuCoreLimit` overrides),
  `HardwareAccelerationType` (`none` default; `vaapi_device`
  `/dev/dri/renderD128`, `qsv_device`), tonemap toggles/params (algorithm
  `bt2390`, peak 100, desat 0), CRF h264 23 / h265 28, encoder preset,
  throttling, segment deletion/keep, `EnableSubtitleExtraction`
  (read by negotiation), downmix boost, deinterlace. Server caps:
  `max_transcodes` / `max_transcodes_per_user` (`TranscodeLimits`,
  `0 = off`).

---

## 7. Porting checklist — implementing this on another media server

### Minimum viable (play every file on every client)

1. **Probe shape + normalization** (§1). One struct for streams; ffmpeg
   strings mapped to enums; unknown ⇒ null, not fabricated defaults
   (fabricating `levels`/`widths` poisons the decision).
2. **Decision as a pure function** (§2). `StreamBuilder` over
   `DeviceProfile`. Start with container+codec+bitrate+profile/level/
   resolution/channel checks; emit the reason **union**. Get DirectPlay vs
   Transcode right; remux can be phase 2.
3. **Arg planner as pure fragments** (§3). One `build_arguments` that
   concatenates small builders; **argv array, never a shell string**;
   paths quoted as single tokens.
4. **One ffmpeg per job, HLS muxer writes the segments** (§§4–5). Reuse +
   restart-on-seek instead of per-segment spawns.
5. **Playlists**: variant (VOD + `ENDLIST`, per-segment `runtimeTicks`
   round-trip), master (BANDWIDTH, CODECS, subtitle group). Codec strings
   per RFC 6381.
6. **Job registry + idle reaper + pings** (§4.5). Without the reaper,
   abandoned clients hold tuners forever; without pings, legit pauses get
   killed.
7. **Throttling** (§4.3). Without it, a fast encoder burns disk/CPU and the
   client starves; with a naive one, a paused ffmpeg wedges until SIGKILL.

### Add next

- **Remux (DirectStream)** — copy with bitstream filters (§3.2).
- **Subtitles** — External/HLS delivery first, then Embed (mkv), then
  burn-in (text → `subtitles=`, graphical → `overlay`).
- **Downmix** — the `pan=` algorithm table is data; copy it.
- **Keyframes** — metadata-based (Matroska cues) for remux segment cuts;
  ffprobe in the background.
- **Concurrency caps + eviction** (no queue; refuse past cap with
  `Retry-After`, evict idle same-user/same-device first).
- **No-ffmpeg graceful degradation** — boot, direct-play works,
  transcode negotiated off. Never a crash at startup.

### Hardware (only if you have the hardware)

- NVENC / VAAPI / QSV chains + their encoder/bitrate arms, each with a
  software fallback. Ship them one vendor at a time, verified on real
  hardware. **AMF/VideoToolbox/RKMPP without hardware to verify** ⇒ fall
  back to software with a logged warning (Ferrofin's stance; an unverified
  pipeline shipping green frames is worse than a slow encode).

### Pitfalls the fixtures pin (don't relearn these)

- **Unknown bitrate ⇒ treat as 40 Mbps.** Fibbing a smaller default makes a
  25 Mbps file direct-play onto a throttled link.
- **Always forced keyframes for re-encodes** (`-force_key_frames` at segment
  boundaries, `-g`/`-keyint_min` for GOP-only encoders) — otherwise the HLS
  muxer can't cut on `-hls_time`.
- **Bitmap subtitles load from start** (`-itsoffset`/`-isync`, never `-ss`)
  — seeking a `.sup` loses the display set.
- **`-noautoscale` after a named hardware decoder**, `-noaccurate_seek` for
  copy-video HLS seeks, `-avoid_negative_ts disabled` *without* `-copyts`
  for HLS (B-frame/AAC-priming shift disagrees with seek jobs).
- **`Picking a transcode URL` and parsing it are one contract**: emit
  PascalCase and bind case-insensitively, or the negotiated bitrate cap
  vanishes.
- **A paused ffmpeg ignores `q`** — resume before kill.
- **404 vs 5xx on dead transcoding**: 404 makes hls.js skip-walk doomed
  ffmpegs; 5xx triggers backoff.
- **fMP4 init must match the resume segment** (moov edit list) — start the
  transcode at the resume segment, not 0.
- **ffmpeg `6.1.1` ≠ paused-key capable** (`≤6.1` exact) — version gates
  (`ffmpeg_at_least`) fail closed on unprobed.
- **NVENC gets no `-level`** (unreachable level = hard failure); **NVENC
  MJPEG doesn't exist** (software); **HTTP helpers must not be copied
  verbatim for RTSP** (`-rtsp_transport tcp+udp`).

### Test data

Ferrofin's oracles for the decision are Jellyfin's `StreamBuilderTests` —
JSON fixtures (`crates/ferrofin-model/tests/data/` +
`PlaybackInfo` response bodies), consumed as `#[rstest] #[case]` tables
(`stream_builder.rs`, `stream_builder_audio.rs`). Key edge corpora:
hi10p, broken-fps, Dolby Vision (`dvh1.05`/`dvhe.08`) vs Safari codec-tag,
secondary-audio (`aacDef`/`aacExt`), 32/33-stream, 180° rotation,
`ContainerBitrateExceedsLimit` (LowBandwidth), `DirectPlayError`
(TranscodeMedia), subtitle extraction matrix (internal/external ×
extract on/off), VobSub `.mks` pathing, embed-into-MKV. **Transliterate
these fixtures** for any port — they are the cheapest specification of
"which reason fires".

For the runtime, ffmpeg-backed integration tests are gated behind
`FERROFIN_FFMPEG_TESTS=1` (real spawn, real segments) and skip when ffmpeg
is absent: `segment_transcode_ffmpeg` (per-segment transcode),
`hls_stream_manager_ffmpeg` (playlist → segments end to end),
`no_repeat_extraction_ffmpeg` (scan/task/restart never re-extract trickplay),
`subtitle_streams_ffmpeg` (PGS/ASS burn-in at the right time). Unit tests
use a `Transcoder` fake — the arg builders are pure enough to assert the
whole `argv` vector without a binary.

---

## Appendix: crate map (Ferrofin)

| Crate | Transcoding role |
|---|---|
| `ferrofin-model` | DLNA decision: `DeviceProfile`, `StreamBuilder`, `StreamInfo`, `MediaOptions`, `TranscodeReason` |
| `ferrofin-traits` | seams: `HlsStreamRequest`, `HlsStreamManager`, `TranscodeManager`, `MediaEncoder`, `EncoderSupport` |
| `ferrofin-mediaencoding` | probe/normalize, ffmpeg arg building (`EncodingHelper`, `hw/`), job runtime, subtitles/attachments |
| `ferrofin-hls` | variant/master playlist generation, segment orchestration |
| `ferrofin-keyframes` | Matroska-cue + ffprobe keyframe extraction (remux segment cuts) |
| `ferrofin-core` | media-source manager (static `MediaSourceInfo` assembly from stored streams) |
| `ferrofin-api` | handlers: PlaybackInfo negotiation, stream/HLS/pause/stop routes |
| `apps/ferrofin-server` | composition root: ffmpeg discovery + capability probe, planner, wiring, idle reaper spawn |
