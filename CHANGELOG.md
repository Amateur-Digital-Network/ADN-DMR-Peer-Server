# Changelog

All notable changes to **adn-server** are documented here.

<!-- version list -->

## v2.6.1 (2026-10-16)

### Bug Fixes

- **hbp**: A 4000 on a MASTER many hotspots log in to directly clears only the sender's dynamics
  ([`6df35ff`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/6df35ff5c7fe0e6ebb8139a33850a710e64ed47f))

- **routing**: Keep unit data off hotspots it is not for and off slots playing voice
  ([`1ad6432`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/1ad6432755db56955df5db931d4db74a365669e4))

- **routing**: Treat rate 1 data blocks as unit data
  ([`ca67fe1`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ca67fe16cc6efc30bd4c378ca40571f060a38c7f))


## v2.6.0 (2026-10-14)

### Bug Fixes

- **acl**: OpenBridge systems apply their TGID_ACL
  ([`ad46496`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ad46496edd835dfeff1cc00c8096d2ea19116dac))

- **announcements**: Keep mesh voice off the MASTER slot a plugin announcement holds
  ([`127ef24`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/127ef24ad9f9c710daeef01a1b986a5cd2bfc707))

- **announcements**: Plugin announcements behave like the core ones under routed traffic
  ([`5c6348a`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/5c6348a96a9b111f52125f55a03ca1905d952da2))

- **config**: Reject PROXY and OBP_PROXY sharing the same LISTEN_PORT
  ([`35765a5`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/35765a5d824199d1ec6bd4025fec60331bc88964))

- **config-reload**: SIGHUP applies OBP_PROXY
  ([`3645bd1`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/3645bd103452eb074f9d7509bd27aa6b71d0ad86))

- **hbp**: A 4000 never releases a leg the sender has as a static
  ([`d8cfcf2`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/d8cfcf239f29a96783c2b6d71dbec14b455464c5))

- **hbp**: Every hotspot stream on a shared MASTER slot gets its END
  ([`a6059b3`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/a6059b3d27a4408827b164d6ccce7c338ac9d09b))

- **hbp**: Reset a MASTER's bridges only when its last peer is gone
  ([`fc127e8`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/fc127e8dc20c5353dadfdc1a7d4e992a9dfea9df))

- **hbp**: TG 4000 on a shared MASTER clears the sender's dynamics only
  ([`9f9dc1f`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/9f9dc1fd9f8c639781006ff5257043101f979ba7))

- **loop-guard**: Hold 1.0 s by default and always under the parrot's 2.0 s
  ([`ef31359`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ef31359878765d8871990d7d72b0dd221f93a1db))

- **obp**: Anchor a DNS-named peer by host, not by host and port
  ([`17a8a8d`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/17a8a8d815436326f635f8f851852fe2acadc753))

- **obp**: Count frames refused for coming from the wrong address
  ([`55faf79`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/55faf797dae1102bb2e92fbca1b11cc6b7743fe3))

- **obp**: Keep keepalives from relocating a bridge's peer
  ([`35f5c01`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/35f5c01b3f9f516c4bbb4b2db2fa2e2cf41fee37))

- **obp**: Let a reused stream_id start a new call instead of dropping it
  ([`c2fd826`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/c2fd82698dd8065987a5ad5723421178ed63247f))

- **obp**: Release per-stream talker-alias state when OBP streams are trimmed
  ([`59ae137`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/59ae137b60484f0db184eda5ffe56bbe9bea96f9))

- **obp**: Trim idle forward legs so STATUS does not grow per call
  ([`c3db5b4`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/c3db5b4c421154f7f2490d37d47bd851c1f63843))

- **obp-proxy**: Drop per-packet RX debug for voice, not once-per-stream
  ([`e8da064`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/e8da064d63cc9ced9e27e9356d249e162fc27210))

- **obp-proxy**: Keep OBP_PROXY config, demux control frames by peer, pin egress
  ([`38e8c2d`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/38e8c2d60bd28446186037a447c82d44f99c125f))

- **obp-proxy**: Log OBP_PROXY RX once per stream instead of per packet
  ([`fd82c57`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/fd82c573c8f816fa65dcd2dfe634c8252760b948))

- **obp-proxy**: Re-pin egress after a reload and match peers by configured target
  ([`7ab5d77`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/7ab5d774a1033d81e054a98bebc9f19155d9dabe))

- **obp-replay**: Judge only what arrived, and do not blame a table we do not have
  ([`d5755cc`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/d5755cc3f21403a9cfac042cf0cf914e5cdf4520))

- **plugins**: An announcement waits for a QSO on its talkgroup, mesh included
  ([`117afaf`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/117afafb07adb4c58f90681f1321e2f12263e7de))

- **plugins**: Plugin voice records RX state like a hotspot frame
  ([`0e61d5d`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/0e61d5d473d028fffba6b3971f816c5a5aee7f58))

- **plugins**: PLUGINS is read from adn-server.yaml and follows SIGHUP
  ([`c418b1a`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/c418b1a893304fb054617dc8f983b4c129d7bd28))

- **plugins**: Wait on a per-TG last-heard record, with a plugin tg_quiet_s
  ([`6bf4394`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/6bf4394f38d229de60016a3bc11642f400019623))

- **reload**: A system added by SIGHUP gets its own config, and answers
  ([`808e61c`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/808e61cab1a4327764503dda8bbf14113bd37839))

- **report**: The monitor shows a MASTER's yaml statics as each hotspot's own
  ([`eeee4dd`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/eeee4ddc9a55ed8584c473c57346ce04505dd95e))

- **routing**: A stream resuming after a pause resyncs its sequence
  ([`ddda5ae`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ddda5aeca9934634e68a0b3a94291c6fa9df812f))

- **routing**: A terminator behind the last sequence still ends the call
  ([`8e55cbd`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/8e55cbd764e7a712165e9836f65349b979c30c7e))

- **routing**: Another server's announcement over OpenBridge is mesh voice
  ([`3d9202d`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/3d9202d3c9e90d6f5f9a2e0d767b0223191b92cc))

- **routing**: Compare voice sequence numbers modulo 256 on OBP and HBP ingress
  ([`0caece5`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/0caece53d51fa46b3d4538b9d24f4fb75297174a))

- **routing**: Don't fan out unit data to OBPs for a fresh local subscriber
  ([`fa0ceae`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/fa0ceae8a106fd75e21e8e8d1eaee4b97ee382a4))

- **routing**: Tg_quiet_for sees per-stream live voice and overs without terminator
  ([`b0f6dd2`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/b0f6dd2afd6647ebe8876c22572767935dfb8c72))

- **schema**: Declare connected_at and options on topology peers
  ([`e7b8701`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/e7b87017a96d4a3fcf13e4465350436e4c73f04d))

- **sub_map**: Save routes within minutes of a change, and atomically
  ([`915aacf`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/915aacfa82dde1953687ce477ef3b75a9f42a9e9))

- **sub_map**: Unique temp name per save and remove it if the dump fails
  ([`a74e391`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/a74e391010ab2411bf30ae10dae5309c42cae9e6))

- **subscription**: Unindex a leg by where it was indexed, not by its state now
  ([`e5bc60f`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/e5bc60fd17089ddbd9ffb2ae78555d35a0cd38b1))

- **tools**: Hotspot_probe rows carry the UTC date
  ([`0e3324f`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/0e3324f2ab432d27aa52d8866ecbc40a395e4043))

- **unit-data**: Do not send unit data back to the hotspot it came from
  ([`4b6e5cd`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/4b6e5cd602753f82f6ce489755b018afc2198e66))

- **voice**: A private call to 9991-9999 plays its on-demand file again
  ([`02ce1b4`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/02ce1b4e952c2858e99f5b3b3b9c456e723d6a65))

- **voice**: A server prompt holds its slot after mesh voice used it
  ([`75067b7`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/75067b761d9e6be8c16804af0ecc3a36682ce5e0))

- **voice**: Server prompts hold TS2 while they play
  ([`9283bd7`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/9283bd73bdff5b0578a65c869c2fe572befa11d6))

### Documentation

- Sync the guides with what changed since 2.5.6
  ([`ee482c2`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ee482c210618a962901a8768a60a2da198461a90))

- TG 4000 in-band deactivation only on a MASTER of its own
  ([`9cc02c6`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/9cc02c6b1f156a3631f3113e03a4d1cc6f3b542d))

- **hbp**: _apply_tg4000_reset says what a shared MASTER skips
  ([`306650d`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/306650d30b559c409f55e2a08f21907a21ed9689))

- **obp-replay**: What a live master taught about reading the report
  ([`091d260`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/091d2609a79fab02ab6f4b4e35ee5805c56c8523))

- **readme**: Sync version parity note to 2.6, translate README_es.md
  ([`5a4741f`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/5a4741fac481019df6cbddb88b0ed3e4b9d847cb))

- **routing**: The seq-0 terminator comes from client apps in general
  ([`b445863`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/b4458630ef24a2e23d9ea4453295cdde6c4a5c4b))

### Features

- **hbp**: TS1_STATIC / TS2_STATIC reach every hotspot of their MASTER
  ([`b910155`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/b9101552c83594db78f350e29166178e63aeca10))

- **loop-guard**: Catch voice coming back through transcoding bridges
  ([`a83cfd5`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/a83cfd5b1bcffe2c8f0642147483735460962b32))

- **loop-guard**: Count all OpenBridge links as one ingress, the mesh
  ([`269fb8e`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/269fb8e17910d5c91613bbd2d7fe6761967aa380))

- **loop-guard**: Drop bridge loop echoes by default
  ([`ab54a0c`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ab54a0cc0ac07b2026dbdf66e6c972bfb92efb2e))

- **obp**: Anchor a bridge's peer to DNS when TARGET_IP is a hostname
  ([`d5d44b1`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/d5d44b1fde7cd19ad3651ad3983782fba6c9bc6b))

- **obp**: CALL END counts every missed packet and shows the longest gap
  ([`0e1eeac`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/0e1eeaca1542c2974d9eacffa79b0229f7134e66))

- **obp**: Replay a capture through the ingress to see why frames were refused
  ([`5c24ecb`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/5c24ecb48bb7bd52dc962b0e48a64b61467f3c40))

- **obp**: Warn when two bridges share a passphrase
  ([`2ed5d74`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/2ed5d74bc61e362a2f62908a6fa6c2a32f7fd997))

- **plugins**: Group voice from plugins, routed like announcements
  ([`ecddd11`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ecddd1191d17432cf4175b3e1141851501950fb9))

- **plugins**: Let a plugin send unit data, opt-in per plugin
  ([#103](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/pull/103),
  [`0c3432e`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/0c3432ebfd31b2ae8e702f3cc1e0ce868f837ad0))

- **plugins**: Voice-announcements plugin (scheduled, TTS, beacons)
  ([`620a2b7`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/620a2b7b43d0927667d121a5a388c9ff4073c7c5))

- **plugins**: Voice_slot_for_tg and monitor TX for plugin voice
  ([`d76de25`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/d76de25cbc8a26be1c77a62c7f8f282d13a454ab))

- **report**: Send peer latitude, longitude and height
  ([`805aa92`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/805aa92a58abe9cdaeed27d7a36878226ac322b1))

- **sub-map**: Per-system SUB_MAP_LEARN for service systems
  ([`0b8e45d`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/0b8e45d6639ce4d474fedc80a10293e6fb4737b5))

- **tools**: Hotspot_probe, a listen-only hotspot that records what reaches it
  ([`31c4aa2`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/31c4aa22e7ec4c349a6aeeb5a7e93df0aebd46d6))

- **voice**: TG 4000 follow-up: private reply, statics named, reactor pacing, sender-only legs
  ([`2d5efaa`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/2d5efaa72d992ca43fa0e4a6e2857d0d6b24b684))

- **voice**: TG4000_VOICE answers a hotspot's TG / ID 4000 with the link status
  ([`868c507`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/868c5079ea5fb485333114693427210264515603))

- **voice**: TG4000_VOICE on by default
  ([`b4a41bc`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/b4a41bcf5938af7a3934bdf37a31ea4ad453c7ab))

### Performance Improvements

- **alias**: Reload alias files only when they change
  ([`3bbc174`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/3bbc17497362b9806e4ce473c27f0c863171d8b3))

- **aliases**: Download the lists when their published checksum changes
  ([`1f06564`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/1f06564237109eb2c6f63056e2d2aed1595201aa))

- **aliases**: Stop each alias reload from raising the server's memory
  ([`1aa602a`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/1aa602a934d0379125109d7aefb068a7acc19764))

- **auth**: Load user passwords when they change, not every 10s
  ([`5ee724d`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/5ee724dc92f035b23977448134a958430c80f8d6))

- **hbp**: Parse a peer's OPTIONS and RF mode when they change, not per frame
  ([`cb70a1c`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/cb70a1cae6547769418b4a505fbcbfbdf63c8b58))

- **obp**: Derive a bridge's dns_host on reload, not on every lookup
  ([`fea9507`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/fea9507fe85f2b6ca6754fcc559a65fb1007190f))

- **obp**: Stop repeating per-frame work in OBP ingress
  ([`d0fc702`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/d0fc7020f70403122e748495938f221a11644957))

- **plugins**: Build only the events some plugin wants; batch deferred ones
  ([`d74ef04`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/d74ef04bfc6975d4caf99d194184dda7528683dc))

- **report**: Stop deep-copying every hotspot for each virtual SYSTEM-N
  ([`579e844`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/579e844c18a63f7b9db0e8be6376d6964d9dcd2f))

- **routing**: Encode a call's LC once for all its legs; index legs by system
  ([`3396030`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/3396030ac3f317596e79467dcc9fa68328b50c5d))

- **routing**: Stop rescanning the subscription store per voice frame
  ([`1896a24`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/1896a2484205f5bcc18deeac14014fd303d6562d))

- **sub-map**: Cache the SUB_MAP_LEARN decision on the HBP protocol
  ([`ec69f90`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/ec69f90776b9fb29474d6575db10666f4a1b9d0b))

- **subscription**: Answer store_has_table from the index, not a scan
  ([`91ff612`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/91ff6128d9c23951e25a8d68e4cf10b1a99cb7ca))

- **talker-alias**: Stop re-decoding a stream's alias once it is complete
  ([`703b013`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/703b013a62f5acd03d897150f33cd267193bf9df))

### Refactoring

- **alias**: One loader with .bak fallback, so the TSV caches too
  ([`77cc451`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/77cc451c04a21c2cc1a1c4a894f3f50fad034dbc))

- **hbp**: One ACL gate for the MASTER and PEER ingress paths
  ([`82e8450`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/82e845088e8e205f1375b588d727db32e240ac1c))

- **loop-guard**: Cap the hold at the parrot's own PLAYBACK_DELAY_S
  ([`3e74b10`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/3e74b100ada6effcca416c548a124aa11dd3e37c))

- **obp**: Drive OpenBridge ingress from an engine that answers with effects
  ([`e105d49`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/e105d493f05c170a42e2a8c03ed5f4897d783449))

- **obp**: Give each bridge a session instead of writing state into its config
  ([`05915a8`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/05915a8207428bd03c66d64036de42152716e7e0))

- **obp**: Lift ingress admission out of the hblink port into the domain
  ([`616d939`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/616d939f5b358e6544682371456729b6fb5ce937))

- **routing**: Address review on the forward plan cache
  ([`7c8db55`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/7c8db554a3e21393c8e5cfb84f1c2e7e7d483d68))

- **voice**: Address review on the prompt slot hold
  ([`01abfe3`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/01abfe3bdd0bd4fc372b3e686c754b1dd768ce81))

- **voice**: Scheduled announcements and TTS leave the core
  ([`1ab9036`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/1ab9036345a8f9ad1bcb8348d40d7e55d1a49f1d))

- **voice-announcements**: Use server_id_bytes()
  ([`0dd50cd`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/0dd50cd752975f805c8c13edd031ef2cbbb87d6b))

### Testing

- **hbp**: TG 4000 tests on the shared repeat stack with real routing
  ([`2c9f668`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/2c9f668131fb971ab5dad76d17367e6d7a8feedd))

- **report**: Cover peer latitude/longitude/height in topology payload
  ([`d0da3aa`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/d0da3aa80e56d5e84ad3a8a91cbe490123835f2c))

- **voice**: A refused TG 4000 reply frame is the last one tried
  ([`e85c056`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/e85c0560f873b53bc4c2d84d8acae56609b0776e))


## v2.5.6 (2026-09-19)

### Bug Fixes

- Write alias downloads atomically and validate checksum before persisting
  ([`aad30ba`](https://github.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/commit/aad30baebab0bf5195b9a5313fa5c7a6e82f23e6))


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
