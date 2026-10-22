# Security and privacy

## Optional video analysis and ASR (1.2.0 / 1.3.0)

Disabled by default; accepts existing chat-scoped video handles only. Decodes
plugin-owned MP4/MOV bytes, not arbitrary paths, URLs, archives or chat files.
Explicit argv, restricted file/pipe protocols/formats, bounded output, deadlines
and cancellation cleanup apply. Linux also limits child address space/CPU/FDs.
These are **not** an OS sandbox or immunity to decoder vulnerabilities. Keep
ffmpeg/ffprobe patched. Non-Linux lacks these Linux rlimits; the real-binary
production smoke gate runs on Linux.

Frames go to the existing vlm supplier. Explicit `include_audio=true` additionally
requires the separate, default-off `asr.enabled` setting. Only the first audio
track is decoded to bounded 16kHz mono PCM WAV in memory and sent to the host's
existing `voice` task via `llm.transcribe_audio`. No new credentials or provider
fallback are added by this plugin; Gemini-configured voice tasks use native
generateContent audio, not the OpenAI transcription endpoint. Host and provider
retry/routing policies still apply. ASR adds at most one logical host call before
the existing visual call, within the unchanged overall deadline. Silence/music
can still produce erroneous model text; transcripts have no verified timestamps,
speaker identity or completion guarantee. Missing finish metadata stays unknown.
Do not analyze content without permission
or assume zero supplier retention. Frame/subtitle text is untrusted data, not
instructions. No QQ sending or media delivery-state reset occurs in analysis.
Persistent unknown admission precedes the logical SDK call; no plugin-level
retry of ambiguous work. Host/provider retries and upstream cancellation cannot
be guaranteed. Private bounded results do not prove full video comprehension;
audio is not analyzed unless explicitly requested and enabled. Audio/transcript
instructions are untrusted data; the transcript is not sent to the visual model.
ASR failures do not silently degrade to visual-only success. Combined unknown
work is not replayed after reload, even if ASR completed before a visual failure.
Since 1.3.1, unknown work is keyed by chat/content independently of configuration
and audio options, and takes precedence over successful cached variants. Old
opaque unknown rows cannot be mapped back to chat/content: they conservatively
block all analysis/cache reads until their original one-hour expiry, without
deleting evidence or extending that expiry. New rows retain chat/content isolation.
The original four-column analysis table remains compatible with baseline SQL;
identity mappings are committed in a separate table in the same transaction.
The transitional five-column table is restored transactionally, preserving all
rows and mappings; failure rolls back the whole migration. SQL compatibility
does not restore security to old code: a safe rollback requires a compatibility
patched build with the new admission and clock protections, not bare old code.
Invalid/null/non-finite/negative or >5-second-future creation timestamps latch
a persistent fault before any expiry deletion. A wall/monotonic clock divergence
over 5 seconds in one process (including cumulative small steps), or a wall
rollback over 5 seconds since the persisted observation, also fails closed.
Faults stop analysis/cache reads until offline operator investigation and a
reviewed repair; restart/clock correction alone must not clear evidence.
Forward jumps across restart cannot reliably be distinguished from real downtime:
the one-hour TTL still relies on trusted wall time at that boundary and is not
an absolute billing-idempotency guarantee. Unobserved clock changes are not
claimed detectable. Keep analysis disabled if this boundary is unacceptable.
Private transcript text uses the same bounded one-hour logical result cache;
physical SQLite expiry cleanup is deferred until a later admission. No audio
sidecar is written by this plugin; the host/provider may retain request data.

Do not put tokens, cookies, QR login URLs, account identities or private share
links into GitHub issues. Report reproducible issues with redacted, synthetic
fixtures. Do not send a live credential to demonstrate a problem.

Official Bilibili/Douyin summaries may use credentials provided by the operator.
Third-party media parsers receive a public share link or video ID, never those
account cookies. The default primary parser uses plain HTTP: observers on that
path may read or alter that request/response. Use only public share links and
disable `media.enabled` if this trust model is unacceptable.

Media downloads validate and pin all resolved public IPs, preserve Host/TLS SNI,
recheck redirects, reject credentials/private addresses and limit size/decoding.
These checks do not authenticate a third party's content or guarantee copyright,
availability, safety of every downstream decoder, or original quality in QQ.

MP4 validation checks a container signature only, not full decoding. Video bytes
are never passed as an arbitrary remote URL to the messaging platform. A failed
or ambiguous send must not be retried automatically; a platform request accepted
is not proof that a recipient displayed it.

Video1.1 uses256KiB chunks to a private local spool; only a generated file URI
crosses RPC. The QQ client must share the file system. No tool accepts a local
path, delegates an external URL to QQ or starts a public file server. The optional
single-file cap defaults256MiB (0=unlimited); a1GiB/32-file cache,256MiB free-disk
reserve,180s total deadline and two slots remain. Complete/uncertain files stay
one hour for asynchronous platform reads; scoped housekeeping then removes them.
Interrupted partials are removed. The spool is0700/files0600 on POSIX; Windows
also needs NTFS ACLs. Stopping the plugin defers cleanup until next startup.
Images retain their8MiB binary-RPC cap. No video is stored in model/chat history.

Credential files are local and mode 0600 where supported. They are not encrypted
at rest; the service account and programs running as it may read them. Invalid
files are preserved, not silently treated as an empty writable credential store.
