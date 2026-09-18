# Changelog

All notable changes to **adn-server** are documented here.

<!-- version list -->

## v2.5.5 (2026-09-18)

### Bug Fixes

- Retry alias JSON downloads on a short poll instead of waiting a full STALE_DAYS cycle
  ([`f6cc0fe`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/f6cc0fe2818ab52c91316d623d432451ec1051a4))


## v2.5.4 (2026-08-19)

### Bug Fixes

- Keep hotspots connected when the security server is unreachable
  ([`49114fe`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/49114feee954e864d99d85d95fbbb3e8d89a6938))

- Stop unit calls to 4000 from being broadcast to every peer
  ([`62225c9`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/62225c9f07650078f1338461f1bb9a2d063e3691))


## v2.5.3 (2026-07-31)

### Bug Fixes

- Deliver a hotspot's own group call back to its other slot when subscribed there too
  ([#67](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/67),
  [`c788382`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c7883822c61b56317e57cc18b68eb724f1d85c25))

- Deliver TG on both slots when static on one, dynamic on the other
  ([#66](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/66),
  [`6c28754`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/6c28754ce4fc470d05e98b45fe9e893b10a2a20a))

- Dual-slot delivery for mixed static+dynamic TG subscriptions
  ([#66](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/66),
  [`6c28754`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/6c28754ce4fc470d05e98b45fe9e893b10a2a20a))

- Strip NUL-padded CALLSIGN instead of showing raw bytes
  ([#66](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/66),
  [`6c28754`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/6c28754ce4fc470d05e98b45fe9e893b10a2a20a))


## v2.5.2 (2026-07-28)

### Bug Fixes

- Deliver a group call on both slots when a peer has the TG on TS1+TS2
  ([`4360efa`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/4360efa3d984faf773fa3087d56592f10146571d))


## v2.5.1 (2026-07-25)

### Bug Fixes

- Target private calls to the known hotspot instead of broadcasting
  ([`11970e0`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/11970e0552203fa1e013121c8997f805504898b4))


## v2.5.0 (2026-07-23)

### Bug Fixes

- Don't attribute synthetic announcement PTT to a real connected peer
  ([`99b6f8d`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/99b6f8da705ac199206237cd024b9b85bc4a8b60))

- Log silent dynamic TG activation once per peer and TG
  ([`278b10e`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/278b10eb585a07f7bf7e6cc9ff361c972db5b2e5))

### Features

- Add HP3ICC acknowledgments to README
  ([`4bf378f`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/4bf378fe9523be2777d8bd711c70308a0391f87b))


## v2.4.3 (2026-07-22)

### Bug Fixes

- Forward OBP group voice to an HBP target only once per TG
  ([`6a66010`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/6a6601055e405ac9718807b31383fb66187077ab))


## v2.4.2 (2026-07-22)

### Bug Fixes

- Default TALKER_ALIAS_SEND_DMRA off for legacy wire parity
  ([`169467e`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/169467ecbb3114f1dd0605cafa77ad83821e347b))


## v2.4.1 (2026-07-21)

### Bug Fixes

- Accept NUL-padded RPTC callsigns in login check
  ([#52](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/52),
  [`5f2f69d`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5f2f69dd1979f2b27a86d413b4bccefb6609f711))

- Accept NUL-padded RPTC/RPTO from ipsc2hbp
  ([#52](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/52),
  [`5f2f69d`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5f2f69dd1979f2b27a86d413b4bccefb6609f711))

- Deliver one DMRD when TG is on both OPTIONS slots
  ([#53](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/53),
  [`7ff3010`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/7ff3010c23cd84400144842749539dc39c93b0c0))

- Strip NUL padding from monitor export and shared HBP fields
  ([#52](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/52),
  [`5f2f69d`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5f2f69dd1979f2b27a86d413b4bccefb6609f711))

- Strip NUL padding from RPTO OPTIONS in logs and storage
  ([#52](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/52),
  [`5f2f69d`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5f2f69dd1979f2b27a86d413b4bccefb6609f711))


## v2.4.0 (2026-07-15)

### Bug Fixes

- OBP_PROXY fan-in for centralized OpenBridge ingress
  ([`770523c`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/770523cc3e22a2815009b10077b540f66fac1d73))

### Features

- Publish OBP keepalive connected status in dashboard_state
  ([`b27c166`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/b27c1663c62cc25b3d4671b7114a1cded43da5c5))


## v2.3.3 (2026-07-13)

### Bug Fixes

- Gate OBP source ensure when bridge source is already active
  ([`e140822`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/e140822e68fc65331123f12ee1ece8b7122a2690))


## v2.3.2 (2026-07-13)

### Bug Fixes

- Inject scheduled voice via routing and restore monitor TX events
  ([`62f429a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/62f429aad90272e1684f554ac8a1999cf15eed43))


## v2.3.1 (2026-07-13)

### Bug Fixes

- Stop premature OBP monitor voice END events
  ([`cc4031a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/cc4031ae4d887cd2dbcfdc29b0ccaa8fd57dd72c))


## v2.3.0 (2026-07-10)

### Chores

- Fix import order in voice subscription plan test
  ([#40](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/40),
  [`c3b1c1c`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c3b1c1c797ed68f20a81e43c62cbdd6964e45e16))

### Features

- Poll peer_dynamic_tgs.need_reload for dynamic TG purge
  ([#40](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/40),
  [`c3b1c1c`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c3b1c1c797ed68f20a81e43c62cbdd6964e45e16))


## v2.2.6 (2026-07-10)

### Bug Fixes

- Block OBP during server announcements on inject-only masters
  ([`cae5d57`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/cae5d57d8ec0ebcc4deaf5662d35d3f5d59571b2))


## v2.2.5 (2026-07-10)

### Bug Fixes

- Add ReportWire contract tests against report-v2 schema
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Add tests/fakes shim for application test decoupling
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Align OPTIONS static validity checks across routing and report
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Audit items 11-13 coverage, infra tests, and warning logs
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Audit refactors, report contract, and concurrent OBP downlink
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Audit wave 1 server hygiene refactors
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Document dashboard_state in report-v2 schema
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- OBP DMRE source-server validation without ALLOW_UNREG_ID bypass
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Per-stream OBP bridge TX legs for concurrent MASTER downlink
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))

- Ruff lint in OBP concurrent streams downlink test
  ([#36](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/36),
  [`c15430a`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c15430a3e0905f9b87ca2f32c98ee7cf26a0cd48))


## v2.2.4 (2026-07-09)

### Bug Fixes

- Duplicate-safe TA embed phase and REPEAT VHEAD DMRA (B)
  ([#34](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/34),
  [`1567314`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/156731447c62cd9fe5f31562900cfba2d6c5d7ca))

- Exclude byte-identical duplicates from HBP rate counter (A.2)
  ([#34](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/34),
  [`1567314`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/156731447c62cd9fe5f31562900cfba2d6c5d7ca))

- Production ingress mitigations (UDP rcvbuf, rate limiter, TA embed)
  ([#34](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/34),
  [`1567314`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/156731447c62cd9fe5f31562900cfba2d6c5d7ca))

- Raise UDP SO_RCVBUF on voice listeners (C-LOCAL)
  ([#34](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/34),
  [`1567314`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/156731447c62cd9fe5f31562900cfba2d6c5d7ca))

### Chores

- Document echo point-to-point and logged_in reconciliation (EN/ES)
  ([#34](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/34),
  [`1567314`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/156731447c62cd9fe5f31562900cfba2d6c5d7ca))


## v2.2.3 (2026-07-04)

### Bug Fixes

- Echo TG 9990 isolation for multi-peer inject proxy
  ([#32](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/32),
  [`8698359`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/86983597b2f23f4dbcc3b0bc7dc1b9b11cd7dc28))


## v2.2.2 (2026-07-02)

### Bug Fixes

- Correct broken hotspot-proxy anchor link in special-numbers (EN)
  ([`2ec900b`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/2ec900b2608f40d434ebcd776324027df5fdf670))

- Reconcile logged_in against connected peers every 120s
  ([`ab63a8c`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/ab63a8ceb1e9632cbc8b41a7369a60e61d7c6015))

### Chores

- Document OPTIONS line behaviour and self-service dashboard login (EN/ES)
  ([`81cf74d`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/81cf74d4d50378a3074a33bcaabea84983a4949b))


## v2.2.1 (2026-07-01)

### Bug Fixes

- Add shared PASS= redaction for OPTIONS log lines
  ([#28](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/28),
  [`9de26ea`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/9de26eab991fbdeee8db4542a16d005538d1d2e0))

- Elevate proxy attach rejection to WARNING for RPTC/RPTO
  ([#28](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/28),
  [`9de26ea`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/9de26eab991fbdeee8db4542a16d005538d1d2e0))

- Fetch DB options when hotspot sends no RPTO after RPTC
  ([#28](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/28),
  [`9de26ea`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/9de26eab991fbdeee8db4542a16d005538d1d2e0))

- Remove noisy CALL RX log on OBP DMRE voice header
  ([#28](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/28),
  [`9de26ea`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/9de26eab991fbdeee8db4542a16d005538d1d2e0))

- Rework self-service RPTO to distinguish PASS, empty, and OPTIONS
  ([#28](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/28),
  [`9de26ea`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/9de26eab991fbdeee8db4542a16d005538d1d2e0))

- Self-service RPTO handling, PASS redaction, and proxy log cleanup
  ([#28](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/28),
  [`9de26ea`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/9de26eab991fbdeee8db4542a16d005538d1d2e0))


## v2.1.1 (2026-06-22)

### Bug Fixes

- Block downlink DMRD to busy hotspot RF slot per peer
  ([`ecca56f`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/ecca56febdb6698aee939099d0c37f1205ee941c))


## v2.1.0 (2026-06-22)

### Bug Fixes

- Allow same static TG on TS1 and TS2 for duplex routing
  ([`e7d3dab`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/e7d3dab4c07000fec92a384c87f892d2d22affa3))

- Inject-only contention, dynamic TG restore, and UA timer parity
  ([#24](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/24),
  [`c5cbdb2`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c5cbdb233112841c547bbc2ac3b4dfe5ed26d51f))

- Inject-only slot contention, monitor events, and UA timer display
  ([#24](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/24),
  [`c5cbdb2`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c5cbdb233112841c547bbc2ac3b4dfe5ed26d51f))

- Reject malformed peer OPTIONS for monitor and downlink
  ([`08c648f`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/08c648fdc208822274ce50d0f8d3a64447e2972a))

- Slot contention, simplex RF mode, and static TG dedup
  ([`1931682`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/1931682337da141bd342cf9a65bd79ae3e78fcd6))

### Documentation

- Document simplex downlink TS2 choice (MMDVMHost DMO parity)
  ([`49a9605`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/49a96055e93579870c38f7c7f152e17293f39406))

### Features

- Dynamic TG restore, OBP cross-slot downlink, and DB migration 005
  ([#24](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/24),
  [`c5cbdb2`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/c5cbdb233112841c547bbc2ac3b4dfe5ed26d51f))


## v2.0.6 (2026-06-22)

### Bug Fixes

- Tear down REPEAT and bridge TX per stream on concurrent duplex slots
  ([#19](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/19),
  [`de05504`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/de05504a700193706dbae2acc0892b26daff3482))

### Chores

- **ci**: Require merge-commit release PRs for develop ff sync
  ([#21](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/21),
  [`374cc68`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/374cc686efcdbae314e8b67d125032a0b1d22722))


## v2.0.5 (2026-06-21)

### Bug Fixes

- Cross-slot DMRD remap and monitor TE slot
  ([#18](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/18),
  [`5945d66`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5945d6641b4056a77b678093f5fead8491263eb4))

- Remap REPEAT DMRD slot to peer OPTIONS for cross-slot downlink
  ([#18](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/18),
  [`5945d66`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5945d6641b4056a77b678093f5fead8491263eb4))

- Restore cross-slot downlink and REPEAT monitor activity
  ([#18](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/18),
  [`5945d66`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5945d6641b4056a77b678093f5fead8491263eb4))

### Chores

- **ci**: Sync develop via merge instead of force-push
  ([#18](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/18),
  [`5945d66`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5945d6641b4056a77b678093f5fead8491263eb4))


## v2.0.4 (2026-06-21)

### Bug Fixes

- Cross-slot downlink and REPEAT monitor activity
  ([`f561b26`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/f561b2685c53b8625b87ce73d599c0276fba8fa1))

### Chores

- **ci**: Sync develop via merge after release
  ([`e05dc00`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/e05dc003185b49522d0838ed33c8074303f3600c))


## v2.0.3 (2026-06-21)

### Bug Fixes

- **ci**: Force-with-lease when syncing develop after release
  ([#13](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/13),
  [`5fad3b5`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/5fad3b515fe17ca94f33b49f540956ea0e2ae178))


## v2.0.2 (2026-06-21)

### Bug Fixes

- Keep YAML SINGLE_MODE on multi-peer masters for dynamic TGs
  ([#12](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/12),
  [`47c7e33`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/47c7e33c7adc98abc6134547f0539f6cd9f54b59))

- SINGLE=0 multi-dynamic TGs and YAML SINGLE_MODE defaults
  ([#12](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/12),
  [`47c7e33`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/47c7e33c7adc98abc6134547f0539f6cd9f54b59))

### Chores

- Run releases on master only, not develop
  ([#12](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/12),
  [`47c7e33`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/47c7e33c7adc98abc6134547f0539f6cd9f54b59))


## v2.0.1 (2026-06-20)

### Bug Fixes

- Filter standalone DMRA downlink by TG subscription
  ([#10](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/10),
  [`0dcdf54`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/0dcdf54a07328f7203b02809cd7e26255987258e))

### Chores

- Run releases on master only, not develop
  ([#10](https://github.com/ce5rpy/ADN-DMR-Peer-Server/pull/10),
  [`0dcdf54`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/0dcdf54a07328f7203b02809cd7e26255987258e))


## v2.0.0 (2026-06-19)

### Chores

- Add release workflow and semver versioning (semver from 2.0.0; no RC phase)
  ([`013b766`](https://github.com/ce5rpy/ADN-DMR-Peer-Server/commit/013b766a30dca0128daf35c116385cf234410b99))


## [2.0.0-rc.4] - 2026-06-18

### Added

- **Docs (MkDocs EN/ES):** bridges vs subscriptions, performance notes, report-proxy guide for legacy dashboards, Mermaid diagrams in architecture/monitoring pages.

### Fixed

- **Echo TG 9990** — excluded from SINGLE/UA session locks; inject-only monitor remap for echo TX legs; bridge TX report field 5 resolves hotspot radio id for ECHO/hotspot live chips.
- **Dynamic TG restore** — `sync_restored_dynamic_tgs` passes `now=` as keyword (fixes TypeError on echo startup after DB restore).

### Compatibility

- **Monitor:** adn-monitor **2.0.0-rc.5**.

## [2.0.0-rc.3] - 2026-06-17

### Added

- **Dynamic TG persistence** — per-peer user-activated TGs stored in MariaDB (`peer_dynamic_tgs`); restored on hotspot reconnect (RPTC); shared `DATABASE` block with proxy self-service.
- **Server-owned migration** — ensures `peer_dynamic_tgs` table on startup (idempotent, same schema as adn-monitor `004`).

### Fixed

- **TG 4000** — clears all peer dynamic slots (memory + DB); emits `INGRESS` BRDG_EVENT so monitor SINGLE=0 chips clear without stuck TX; TG 4000 is never stored as a UA session.

### Compatibility

- **Monitor:** adn-monitor **2.0.0-rc.4**.

## [2.0.0-rc.2] - 2026-06-16

### Fixed

- **Cross-slot static TG downlink** (legacy REPEAT parity): inject-only MASTER delivers group voice to hotspots when the TG is listed in TS1 or TS2 OPTIONS, regardless of incoming wire timeslot. Wire slot is unchanged; downlink index and `peer_should_receive_group_voice` match both OPTIONS lists.

### Compatibility

- **Monitor:** adn-monitor **2.0.0-rc.2** (TS chip maps OPTIONS static slot on receive).

## [2.0.0-rc.1] - 2026-06-12

First v2 release candidate since **1.0.0** (~70 commits). HBP/OpenBridge on-wire behaviour preserved.

### Added

- **Report v2** — JSON HELLO, slim `dashboard_state` TCP wire (no full topology/routing snapshots to monitor), bounded report queue, optional MQTT.
- **Unified binary** — `adn-server.py` with `--echo`, `--doctor`, `--no-proxy`; wiring in `bootstrap/peer_server.py`.
- **Integrated proxy** — in-process UDP fan-in (`PROXY`), inject-only MASTER, self-service MySQL OPTIONS, peer blacklist/timers parity with legacy proxy.
- **Subscription routing** — `SubscriptionStore` + `SubscriptionRouter` as runtime authority; store-native timers, OPTIONS/static TG, in-band ON/OFF; `MeshCodecRegistry` on OpenBridge.
- **Inject-only production path** — per-peer OPTIONS downlink filter, monitor topology expansion (virtual SYSTEM slots), CONFIG push on peer connect.
- **Performance** — O(1) BRIDGES source index; peer downlink index for inject fan-out; adaptive CONFIG_SND debounce on mass login.

### Changed

- **Architecture** — `bridge_use_cases` split into `routing_use_cases` + mixins; `RuntimeContext` and atomic SIGHUP reload; DMR codecs vendored to `domain/dmr/`; mesh codecs extracted from `udp_hbp`.
- **OPTIONS / static TG** — event-driven refresh (RPTO, startup, reload, dmrd fallback) instead of 26 s periodic loop.
- **Talker Alias** — embed on local REPEAT; passthrough and dedupe fixes on relay path.

### Fixed

- OpenBridge packet control and rate-limit parity with legacy.
- Echo playback when sequence byte wraps; echo TG 9990 bootstrap.
- Inject TG 4000: clear dynamics once per PTT per peer.
- Unit data (ARS/LRRP) downlink for 7-digit private calls; monitor TS chip (no spurious `PRIVATE VOICE` on unit data).
- OBP → HBP voice forwarding and downlink CPU under multi-peer inject load.

### Removed

- Standalone **`adn-parrot.py`** — use `adn-server.py --echo`.
- Periodic **26 s `options_config_loop`**.
- Legacy pickle CONFIG/BRIDGE snapshots on the **server → monitor** wire (monitor uses v2 slim ingest).

### Compatibility

- **Monitor:** adn-monitor **2.0.0-rc.1** (report v2 slim wire, HELLO JSON).
- **Wire:** HBP and OpenBridge on-wire formats unchanged vs legacy ADN DMR Server.
- **Config:** same `adn-server.yaml` shape; add optional `PROXY` / `SELF_SERVICE` / `REPORTS.MQTT` sections.

## [1.0.0] - 2026-06-06

First stable public release. 

### Added

- Clean-architecture rewrite: domain, application, infrastructure layers.
- YAML configuration (`adn-server.yaml`) with startup validation.
- HBP MASTER/PEER and OpenBridge forwarding with legacy parity (loops, BCSQ, packet control).
- Talker Alias: DMRA packets and embedded LC overlay (UTF-8 / ISO-8859-1 / 7-bit formats).
- Voice: announcements, TTS pipeline, on-demand AMBE playback, recording.
- Echo playback entrypoint (`adn-server.py --echo`, `adn-echo.yaml`) for TG 9990.
- Monitor report TCP: HELLO JSON (mode v2), CONFIG_SND / BRIDGE_SND (pickle), BRDG_EVENT.
- Hot reload: `adn-server.yaml` (SIGHUP), `adn-voice.yaml` (15 s loop), log reopen (SIGUSR2).
- MkDocs user guide (EN/ES).

### Fixed (highlights since parity baseline)

- OpenBridge packet control and ingress timing.
- Echo playback sequence preservation on long QSOs.
- Config validator accepts numeric MMDVM option fields in YAML.
- OBP END/TX reporting and STATUS lifecycle alignment with legacy.

### Compatibility

- **Monitor:** adn-monitor **1.0.0** (HELLO v2 + legacy pickle report).
- **Config:** use `adn-server.example.yaml` as template; production YAML is not committed.
- **Wire:** HBP and OpenBridge on-wire formats unchanged vs legacy ADN DMR Server.

[1.0.0]: https://github.com/ce5rpy/ADN-DMR-Peer-Server/releases/tag/v1.0.0
