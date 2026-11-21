# Kro GeoIP — Technical investigation and implementation report

**Date:** 2026-11-20  
**Evidence standard:** CONFIRMED (documented or directly visible source); LIKELY (inference supported by source); UNVERIFIED (insufficient evidence); DISPROVEN (contradicted by scoped evidence).  
**Scope:** Publicly visible documentation and repository files, plus the user-supplied KroGeoIP_Railway.zip. No access to the private Telegram bot, game accounts, Midasbuy credentials, merchant accounts or live API tests.

## 1. Executive summary

**CONFIRMED:** Official Midasbuy terms distinguish **user_region** (game-side maintained player region), **shop_region** (Midasbuy site region, associated with local currency/language), and **payment_region** (transaction location, defined using user-selected shop_region). The fact a third-party bot displayed Turkey in the user's existing browser does not establish a permanent PUBG account region change. [S1]

**CONFIRMED:** One inspected open-source implementation named `setCountry` changes the **tool's** active country and writes selected-country cookies into its own Playwright context; its `/switch` function concerns **switching the linked PUBG player** in its redeem workflow and requires login, not changing account nationality. This distinction is backed by the source commentary and code. [S4]

**UNVERIFIED:** Whether the private bot made any persistent server-side change; what its `Stock: 67` tracks; whether a new browser would see Turkey for the same player. The observed UI alone is not enough to classify the bot as fake or genuine.

**CONFIRMED, scoped:** No authorized player-region mutation operation appears in the Midasbuy OpenAPI sections reviewed, which document merchant onboarding, user validation, payment webhooks, product validation, orders and testing. This is not a proof that no internal endpoint exists. [S1–S3]

## 2. Separation of region concepts

| State | Meaning | Changed solely through knowing another player's ID? | Evidence |
|---|---|---|---|
| PUBG account/player region | Game-side attribute | **Not demonstrated; no authorized mutation documented** | [S1] |
| `user_region` | Player region maintained by game side | **Not demonstrated** | [S1] |
| `shop_region` | Midasbuy site selection, currency and language | A user may select their *own* shop region; not evidence of cross-user persistent mutation | [S1], [S4] |
| `payment_region` | Transaction location derived from selected shop region in official glossary | Not independently mutable by third-party Player ID lookup | [S1] |
| IP geolocation | Approximate country inferred from network egress | Depends on network, not ID | Network behavior, general explanation |
| Browser country preference | Site-selected country saved in browser session/cookies | Session-level change is plausible; player ID is not needed for this | [S4] |
| Catalog/products | Regional offerings can vary by shop locale | Selection may change display, not player ownership region | [S1] |
| Server-side eligibility/restrictions | Enforcement of recharge, region, account and payment rules | No authorized Player-ID-only mutation found | [S2], [S3] |

**Other fields:** `user_id` identifies a game player; `user_name` is returned for ID confirmation; `server_id` is the game server; `app_id` identifies the Midasbuy application. [S1] `zone_id` in an example third-party lookup response is a field, **not** substantiated as a two-letter ISO country or evidence of player nationality. [S5] `country` can be a site locale, a provider parameter or a browser preference depending on context; its name does not tell us which. `region` is similarly overloaded. `xMidas` appears as a page-side JavaScript function in the inspected hybrid implementation. Its exact cryptographic scheme is **not independently established here**; calling it an HMAC-SHA256 signature without evidence would be unjustified. [S4]

## 3. Repository-by-repository investigation

### A. exfador/midasbuy-api

- **CONFIRMED:** The displayed repository has `readme.md` only, not the complete server implementation. Its description advertises region switching, but its endpoint table enumerates `POST /redeem`, `/info`, `/stats`, `/history` only. Each request needs a separately issued `x-key`; README documents limits and an `/info` example with nickname, `zone_id`, and `is_ban`. [S5]
- **Authentication:** API key for its own hosted service; README mentions encrypted cookies stored server-side, not furnished by the bot user. **Unverified** internal credentials, proxies or implementation.
- **Region mutation:** **NOT DEMONSTRATED.** No region-mutation API implementation is published there.
- **Railway:** A separately written HTTP client could run on Railway if provider access and terms permit; this repository itself does not provide a self-hostable full server.
- **License:** No explicit LICENSE file visible in repository listing. Public GitHub visibility does **not** grant reuse rights. Confirm owner terms before copying anything.
- **Review depth:** README and repository listing; no server source available; 5 commits listed, individual diffs not audited.

### B. medcharaf111/midasbuy-hybrid

- **CONFIRMED:** Publishes `midasbuy-hybrid.js`, `midasbuy-automation.js`, `find-player.js`, `package.json`, `Dockerfile`, shell scripts, and a HAR file. The README describes anonymous `GET /lookup/<player_id>` and logged-in `GET /switch/<player_id>` / coupon operations. [S6]
- **CONFIRMED source:** In `midasbuy-hybrid.js`, `_maybeAutoSwitchCountry(validationData)` reads `playerCountryCode` and invokes `setCountry`. `setCountry` assigns `this.country`, resets region-dependent templates, and adds selection cookies to **its Playwright browser context**. It does not show a call to mutate `user_region` on a game account. The same source documents `/switch` as a linked-player selection in the redeem flow, not a country-change operation. [S4]
- **Dependencies:** `playwright` 1.59.1 and `undici` ^7; Node >=18 in `package.json`. A container image installs a browser and offers a persistent browser profile. [S7]
- **Credentials/cookies:** It reuses a browser login and cookies according to README; `AUTH_TOKEN` optionally protects its own daemon API. It is not a credential-free account region changer. [S6]
- **Network location:** README claims edge-side geographical behavior. This was **not independently reproduced**. No instructions for geographic-rule circumvention are included here.
- **Completeness and Railway:** A substantial source implementation is visible, with a Dockerfile; may be technically deployable but requires browser infrastructure, an authorized session and compliance with service terms. Neither reliability nor authorized access from Railway was tested.
- **License:** No LICENSE file visible at repository root; reuse permission is not established. 38 commits listed but diffs not individually reviewed.

### C. NightsSoftware/MidasbuyAPI

- **CONFIRMED:** Repository has examples, LICENSE and README, and points to a separately hosted service. README shows a Python example posting to the developer's activation API using `X-Api-Key` and a `player_id` plus redemption code. This is not full server source. [S8]
- **Login:** Client requires provider key; the demo does not ask PUBG credentials. No proof of an authorized region-change function, nor of internal cookies, browser automation, proxy configuration.
- **Railway:** Python client examples are portable, given documented access; provider service itself is not deployable from the example repo.
- **License:** GPL-3.0 for repository code; check implications before redistributing modified examples. It does not license the external API/service.
- **Review depth:** README and repository tree; example directory not fully audited; 7 commits listed but individual diffs not audited.

### Other projects

- `aliothman123/spark-uc-midasbuy-api`: README advertises redeem / check-code / player lookup, not evidence of account-region mutation. [S9]
- `zelsaddr/pubgm_id2name`: historical PHP code demonstrates player-name resolution and parameters such as `zoneid`; it is not evidence of a current supported or authorized integration. [S10]
- Search terms covered combinations of Midasbuy, region, shop_region, user_region, Player ID and Telegram bot. No independently reproducible Player-ID-only account-region change was established.

## 4. Official API capabilities and limitations

**CONFIRMED:** Midasbuy OpenAPI describes merchant platform registration and contracts, game-side user validation webhook, product validation, payment notifications, order query and sandbox tests. The game side owns user validation and game player information. The merchant's successful payment notification is distinct from the player account's region. [S2–S3]

**CONFIRMED:** Regional eligibility can matter: official extensions describe recharge restrictions and a message directing the user to switch to the appropriate `user_region`. This does **not** document a server-side mutation endpoint. [S11]

**UNVERIFIED:** There might be private or partner-only operations not in the reviewed public documentation. Their existence, permission model and suitability for Kro GeoIP are unknown. No undocumented URL is assumed.

## 5. Hypotheses A–G

| Hypothesis | Status | What would decide it | Legitimate path |
|---|---|---|---|
| A. Shop/browser preference changed | **LIKELY possible** | Compare original vs fresh browser and cookies/selected shop country | User-directed site country selection within rules |
| B. Public preference API | **UNVERIFIED** | Authoritative public API spec documenting the operation and scope | Only documented, authorized endpoint |
| C. Authorized service/merchant | **POSSIBLE but unverified for region mutation** | Partner contract + specific approved operation + independent result | Formal partnership if rights actually granted |
| D. Network geography | **POSSIBLE display influence** | Controlled read-only comparisons from authorized environments | Respect regional/payments restrictions; not a bypass |
| E. External API provider | **POSSIBLE** | Provider contract, documented field semantics, repeatable audit evidence | Authorized, licensed provider |
| F. Simulated success | **POSSIBLE** | Compare independent before/after checks and a new browser | Never simulate successful changes |
| G. Private privileged or exploited route | **UNVERIFIED** | Independently authenticated disclosure/audit; none available | Not suitable absent explicit authorization |

**Important:** A single browser reading Turkey afterwards does **not** select among A/C/E/F. Browser state may change without a player's account being mutated. Tests should be read-only and use accounts the tester controls.

## 6. What could `Stock: 67` mean?

**UNVERIFIED:** No official `stock` field associated with player region was identified in the reviewed Midasbuy glossary or published lookup examples. It could be an upstream provider's quota, inventory, bot quota, or an invented value. A decreasing count across operations still would not prove UC stock. To distinguish, obtain the operator's documentation or repeat observations with independently verified outcomes. No stock meaning can be asserted from the screenshot text alone.

## 7. Kro GeoIP implementation delivered

Existing project **was inspected** from the user-supplied ZIP. Replacement `main.py` preserves one-file Telegram architecture, sqlite history and optional HTTP lookup. Changes:

1. Separate `lookups` and `operations` tables; each observed lookup logs `NOT_PERFORMED` / `UNVERIFIED`.
2. Distinct timeout, invalid player, 429 rate limiting, authorization, redirect and malformed response statuses.
3. Operator-controlled HTTPS URL adapter, optional Bearer token and no redirects.
4. Does **not** interpret a numeric `zone_id` as the player region; only explicitly designated player fields are used.
5. Telegram `/start`, GeoIP button, ID entry, `/history`, `/cancel`; player name, player region, **unverified** shop region, target Turkey, status and verification state.
6. In-memory per-user cooldown and SQLite persistence; no passwords or third-party cookies are stored.

**Tests:** Four local unit tests passed using a lightweight stub for the unavailable `python-telegram-bot` package and `httpx.MockTransport` (no outbound API or Telegram access). Syntax compilation passed. Integration tests with actual Telegram and Railway were **not** run. `requirements.txt` still pins `python-telegram-bot==22.5` and `httpx==0.28.1`.

## 8. Railway deployment

- Railway service root = the `KroGeoIP` folder; single worker process.
- Start command: `python main.py` (Procfile included).
- Mounted persistent volume: `/data`; environment `DB_PATH=/data/kro_geoip.db`.
- Secret `BOT_TOKEN`; optional `PLAYER_LOOKUP_URL`, `PLAYER_LOOKUP_TOKEN`, `REQUEST_TIMEOUT`, `LOOKUP_COOLDOWN_SECONDS`, `LOG_LEVEL`.
- No inbound HTTP port or public dashboard required; Telegram long polling is used.
- Do not scale multiple polling replicas against a single bot token and SQLite volume.

Railway docs on persistent volumes: [S12].

## 9. Practical roadmap

1. Obtain a **documented and authorized** player lookup provider. Verify nickname and exact semantic meaning of `user_region` against controlled accounts.
2. Run read-only cross-browser tests with owned account and consistent browser/network conditions. Record timestamps, actual displayed labels and whether selected shop region persisted.
3. Only if an official partner contract explicitly authorizes a mutation, design a separate permissioned integration with independent verification and immutable audit record. Without that, maintain `NOT_PERFORMED`.
4. Deploy single Railway worker with persistent volume; test restarts, Telegram polling, 429 and provider failures; add database backup policy.

## 10. Evidence catalog — direct source URLs and code locations

- **[S1]** Official terminology: https://developer.midasbuy.com/guide/integration/glossary — `user_region`, `shop_region`, `payment_region`, `user_id`, `user_name`, `server_id`.
- **[S2]** Official API integration: https://developer.midasbuy.com/game_doc/guide/integration/api-integration/ — Webhooks overview, UserValidate, ProductValidate, PaymentNotification, OrderQuery and sandbox keys.
- **[S3]** Official merchant onboarding: https://developer.midasbuy.com/game_doc/guide/integration/register — merchant registration, technical integration, contracts and sandbox testing.
- **[S4]** Source: https://github.com/medcharaf111/midasbuy-hybrid/blob/main/midasbuy-hybrid.js — `_maybeAutoSwitchCountry`, `setCountry`, source comments regarding linked-player `switch`, `lookup(playerId)`.
- **[S5]** https://github.com/exfador/midasbuy-api — README `Endpoints` and `/info`, repository file tree.
- **[S6]** https://github.com/medcharaf111/midasbuy-hybrid — README endpoints, login and file tree.
- **[S7]** https://github.com/medcharaf111/midasbuy-hybrid/blob/main/package.json and https://github.com/medcharaf111/midasbuy-hybrid/blob/main/Dockerfile — dependencies and deployment packaging.
- **[S8]** https://github.com/NightsSoftware/MidasbuyAPI — README `API key`, activation example, tree and GPL-3.0 LICENSE.
- **[S9]** https://github.com/aliothman123/spark-uc-midasbuy-api — README advertised scope.
- **[S10]** https://github.com/zelsaddr/pubgm_id2name/blob/master/index.php — historical player name lookup example.
- **[S11]** https://developer.midasbuy.com/game_doc/guide/extensions — regional recharge restrictions.
- **[S12]** https://docs.railway.com/volumes — persistence and mount behavior.

### Caveats

Several Midasbuy official URLs returned a 403 during direct open; their search-indexed official page text was used where available, and no live API operation was exercised. GitHub source snippets were read through the public web interface; a full repo clone and comprehensive commit-by-commit diff review were not possible in the execution container (external DNS unavailable). Accordingly, this report does not claim to have exhausted all versions or performed a 25–40 minute lab investigation. No access to private bot code, API keys or authenticated sessions was available.
