# Security and privacy

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

Credential files are local and mode 0600 where supported. They are not encrypted
at rest; the service account and programs running as it may read them. Invalid
files are preserved, not silently treated as an empty writable credential store.
