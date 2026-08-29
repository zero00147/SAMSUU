# OpenBazaar — Product Requirement Document (Implementation-Ready)

**Version:** 2.0 (revision of Draft 1.0)
**Status:** Ready for implementation
**Scope change from v1.0:** None. No features added, removed, or altered.
This revision only **resolves contradictions, fills unspecified gaps, and replaces vague
prose with testable values.**

---

## 0. How to Read This Document

Every requirement has a stable ID (`FR-LST-004`, `DB-ITM-011`, …). Each is written to be
implementable in isolation, with explicit values and acceptance criteria — no requirement
depends on inferring intent from prose.

Three markers appear throughout:

| Marker | Meaning |
|---|---|
| **[FIXED]** | v1.0 contained a contradiction. The corrected behaviour is stated. |
| **[FILLED]** | v1.0 specified a feature but omitted necessary detail. A default is supplied. |
| **[SIGN-OFF]** | A business decision v1.0 never made. A default is applied so work is unblocked — **the owner should confirm or override before build starts.** |

Everything marked **[SIGN-OFF]** is collected in §2 so none of it is buried.

---

## 1. Critical Contradictions in v1.0

These are hard conflicts. Each would have caused a build failure or data corruption.

| # | Location | Conflict | Resolution |
|---|---|---|---|
| C1 | §3.2 vs §5.2 | §3.2 creates orders with status `PENDING_COD_VERIFICATION`; the `orders` CHECK constraint permits only `PENDING_OTP`, `CONFIRMED`, `DISPATCHED`, `DELIVERED_PAID`, `REFUSED_CANCELLED`. **Insert would fail.** | Canonical value is **`PENDING_OTP`**. `PENDING_COD_VERIFICATION` is withdrawn. |
| C2 | §3.3 diagram vs §5.2 | Diagram uses `DELIVERED_AND_PAID` / `REFUSED_BY_BUYER`; schema uses `DELIVERED_PAID` / `REFUSED_CANCELLED`. | Schema names are canonical: **`DELIVERED_PAID`**, **`REFUSED_CANCELLED`**. |
| C3 | §3.1 vs §5.2 | Title is "15–100 characters"; column is `VARCHAR(150)`. | Column becomes `VARCHAR(100)`; **application enforces 15–100**. |
| C4 | §3.2 vs §5.2 | "Minimum Next Valid Bid = Current Highest Bid + Bid Increment Step", but `current_highest_bid DEFAULT 0.00`. The **first** bid on a $500 item would compute as `0.00 + 1.00 = $1.00`, ignoring `starting_bid` entirely. | First bid rule separated from subsequent bid rule — see **FR-AUC-002**. |
| C5 | §1.1 | "Sub-second page load times (<1.2s LCP)". 1.2s is not sub-second. | Numeric budget governs: **LCP < 1.2s**. The "sub-second" phrasing is dropped. |
| C6 | §4 vs §3.1 | Quick-bid buttons are hardcoded `+$5 / +$10 / +$25`, but `bid_increment_step` is seller-configurable. With a $50 increment, all three buttons produce **invalid bids**. | Buttons become multiples of the item's increment step — see **FR-UI-006**. |
| C7 | §3.3 vs §2 | Reliability score < 75% "suspends CoD privileges". Since the platform is 100% CoD, this is a total transaction ban — **and the only way to raise the score is to complete a CoD order**, which is now impossible. Permanent dead-end state. | Recovery path defined via the existing Administrator role — see **FR-COD-009**. |
| C8 | §5.1 vs §3.2 | SSE connections are stateful per app node, but the topology load-balances across Node 1 / Node 2 with no fan-out layer. A bid on Node 1 would **never reach clients connected to Node 2**. | Redis Pub/Sub fan-out mandated — see **ARC-004**. |

---

## 2. Decisions Requiring Owner Sign-Off

v1.0 never made these calls. Defaults are applied so implementation is not blocked.
**Review these first.**

| ID | Question | Default Applied |
|---|---|---|
| SO-1 | **Currency?** v1.0 uses `$` but targets "cash-dominant markets". | Single currency per deployment, ISO-4217 code in config. Default `USD`. Stored on every monetary row. No multi-currency. |
| SO-2 | **Can a seller bid on their own listing?** (shill bidding) | **No.** Blocked at API level. |
| SO-3 | **How does a Buyer become a Seller?** | Self-service upgrade; requires `is_verified = TRUE`. No admin approval. |
| SO-4 | **Hybrid listings: what happens to Buy-Now once bidding starts?** | Buy-Now is disabled the moment the first bid ≥ reserve price is placed. Below reserve, Buy-Now stays live. |
| SO-5 | **Anti-snipe extension cap?** | Extensions repeat indefinitely while bids arrive, with a hard stop **60 minutes** past the original `auction_end_time`. |
| SO-6 | **Auction ends below reserve — what then?** | Item → `EXPIRED`. No order. No second-chance offer. |
| SO-7 | **Order auto-cancelled on OTP failure — "listing restored to grid" for an ended auction?** | Fixed-price items → `ACTIVE`. Auction items → `EXPIRED` (not relisted, not offered to runner-up). Relisting is a manual seller action. |
| SO-8 | **Platform commission on remittance?** | **None.** v1.0 defines no fee model; 100% of `final_amount` remits to seller. Flagged because "Funds Remitted to Seller via Platform" implies a ledger. |
| SO-9 | **Can a seller edit or cancel a listing after bids exist?** | Edits: only `description` and adding images. Price, reserve, increment, duration, condition are **locked** after the first bid. Cancellation requires Administrator action. |
| SO-10 | **Bid retraction?** | Not permitted. Bids are final. |
| SO-11 | **Item quantity?** | Always exactly **1**. Every listing is a single unique item. |

---

## 3. Global Conventions

| ID | Requirement |
|---|---|
| GC-001 | All monetary values: `DECIMAL(12,2)`, non-negative, currency per **SO-1**. |
| GC-002 | All timestamps stored `TIMESTAMP WITH TIME ZONE` in **UTC**. Rendering localises client-side. |
| GC-003 | All server↔client time comparisons use **server time**. Countdown timers sync to a server timestamp on load and on every SSE event — client clocks are never trusted. **[FILLED]** |
| GC-004 | Primary keys: `UUID v4`, except `categories` (`SERIAL`). |
| GC-005 | Money is never floating point in application code. Use decimal types end-to-end. |
| GC-006 | All list endpoints are paginated. Default page size **24**, max **96**. **[FILLED]** |

---

## 4. User Roles & Permissions

**[FILLED]** v1.0's matrix omitted the Courier actor entirely, despite §3.3 requiring
couriers to set `DELIVERED_PAID` / `REFUSED_CANCELLED`. Courier is specified here as an
actor because v1.0's fulfilment flow **already depends on it** — this is not a new feature.

| Role | Browse | Bid | Buy Now | List | Moderate | Set Delivery Outcome |
|---|---|---|---|---|---|---|
| Guest | Yes | No | No | No | No | No |
| Registered Buyer | Yes | Yes | Yes | No | No | No |
| Registered Seller | Yes | Yes¹ | Yes¹ | Yes | No | No |
| Courier | No² | No | No | No | No | Yes |
| Administrator | Yes | No | No | No | Yes | No |

¹ Except on their own listings (**SO-2**).
² Courier accesses only the fulfilment API, not the storefront.

| ID | Requirement |
|---|---|
| FR-ROL-001 | A Guest attempting to bid or buy is redirected to login, and the intended action resumes after authentication. **[FILLED]** |
| FR-ROL-002 | Seller role is additive — a Seller retains all Buyer permissions. |
| FR-ROL-003 | Administrator capabilities are exactly: unpublish a listing, cancel a listing (incl. with active bids), suspend/reinstate a user account, reset a `cod_reliability_score`, and read the audit log. **[FILLED]** — v1.0 said only "Moderate / Audit". |
| FR-ROL-004 | Every Administrator action writes a `moderation_actions` row. Admin actions are never silent. **[FILLED]** |
| FR-ROL-005 | Courier accounts are created by Administrators only. No self-registration. **[FILLED]** |

---

## 5. Authentication & Accounts

**[FILLED]** v1.0 defined `password_hash` and `is_verified` but specified **no
authentication mechanism whatsoever** — no session model, no token lifetime, no
verification flow. This section supplies the minimum to make login implementable.

| ID | Requirement |
|---|---|
| FR-AUT-001 | Registration requires: `full_name`, `email`, `phone_number`, `password`. All mandatory. |
| FR-AUT-002 | Password policy: min 10 chars, must contain ≥1 letter and ≥1 digit. Hashed with **argon2id**: memory 64 MiB, iterations 3, parallelism 4, salt 16 bytes. **[FILLED]** — v1.0 named argon2id but gave no parameters. |
| FR-AUT-003 | `is_verified` refers to **phone** verification via the same OTP mechanism as §7. Email is stored but not verified. **[FILLED]** — v1.0 never said which. |
| FR-AUT-004 | Sessions use a signed **HttpOnly, Secure, SameSite=Lax** cookie. Access token TTL **30 min**; refresh token TTL **30 days**, rotated on use. |
| FR-AUT-005 | Logout revokes the refresh token server-side. |
| FR-AUT-006 | `phone_number` stored in **E.164** format. Validated on write. **[FILLED]** |
| FR-AUT-007 | An unverified account (`is_verified = FALSE`) may browse and bid but **cannot place an order** — order creation requires a verified phone. |
| FR-AUT-008 | Login rate limit: 5 failed attempts per account per 15 min, then 15-min lockout. |

---

## 6. Item Listing Engine

### 6.1 Media

| ID | Requirement |
|---|---|
| FR-LST-001 | Minimum **3**, maximum **10** images per listing. Publish is blocked below 3. |
| FR-LST-002 | Accepted uploads: JPEG, PNG, WebP, HEIC. All converted to **WebP** on ingest, quality 82. **[FILLED]** — v1.0 named no input formats. |
| FR-LST-003 | Per-image limit **10 MB**; min dimensions **800×800 px**; max **6000×6000 px**. **[FILLED]** — v1.0 gave no image size limits at all. |
| FR-LST-004 | Exactly one image is `is_primary`. It is auto-cropped to **1:1 centre crop** for grid cards. Default primary = first uploaded; seller may reassign. |
| FR-LST-005 | Optional: **1** video, max **30 s**, max **50 MB**, MP4/H.264 or WebM. |
| FR-LST-006 | Three derivatives generated per image: `thumb` 300×300, `card` 600×600, `full` max-edge 1600. **[FILLED]** — required by §4's grid + carousel but never specified. |
| FR-LST-007 | Media stored in S3-compatible object storage; DB holds keys, never binaries. **[FILLED]** — v1.0 specified no storage layer for media. |
| FR-LST-008 | EXIF stripped on ingest (privacy — location data). **[FILLED]** |

### 6.2 Title, Description, Category

| ID | Requirement |
|---|---|
| FR-LST-009 | Title: **15–100 characters** inclusive, after trimming whitespace. **[FIXED — C3]** |
| FR-LST-010 | Description: **50–5000 characters**, mandatory. **[FILLED]** — schema had `description TEXT NOT NULL` but §3.1 never mentioned it or its bounds. |
| FR-LST-011 | Category taxonomy is exactly **3 levels**. Enforced by a `depth` column constrained to 1–3. **[FILLED]** — v1.0's `parent_id` permitted unlimited nesting. |
| FR-LST-012 | A listing must attach to a **depth-3 (leaf)** category. Attaching to depth 1 or 2 is rejected. **[FILLED]** |

### 6.3 Condition

| ID | Requirement |
|---|---|
| FR-LST-013 | Condition is one of five values. **[FILLED]** — v1.0's display labels and DB values differed with no stated mapping: |

| Stored value | Display label |
|---|---|
| `NEW` | New / Sealed |
| `LIKE_NEW` | Like New |
| `GOOD` | Good |
| `FAIR` | Fair |
| `FOR_PARTS` | For Parts / Repair |

### 6.4 Pricing

| ID | Requirement |
|---|---|
| FR-LST-014 | `sale_type = FIXED` → `fixed_price` required (> 0); auction fields must be NULL. |
| FR-LST-015 | `sale_type = AUCTION` → `starting_bid`, `bid_increment_step`, `auction_start_time`, `auction_end_time` required; `fixed_price` must be NULL. `reserve_price` optional. |
| FR-LST-016 | `sale_type = HYBRID` → both fixed and auction field sets required. |
| FR-LST-017 | These combinations are enforced by **DB CHECK constraints**, not application logic alone. **[FILLED]** — v1.0's schema permitted a `FIXED` listing with no price. |
| FR-LST-018 | If set, `reserve_price` ≥ `starting_bid`. |
| FR-LST-019 | Reserve price is **never disclosed**. The PDP shows only a boolean "Reserve not yet met" indicator. **[FILLED]** — v1.0 never said whether reserve was public. |
| FR-LST-020 | Auction duration: min **1 hour**, max **14 days**. **[FILLED]** — v1.0 listed "Duration" with no bounds. |
| FR-LST-021 | `bid_increment_step` > 0. Min **0.01**, max **10%** of `starting_bid`. **[FILLED]** |
| FR-LST-022 | Hybrid Buy-Now disabling follows **SO-4**. |

### 6.5 Specifications, Defects, Logistics

**[FILLED]** — v1.0 §3.1 required all of the following, yet the schema in §5.2 contained
**no table or column for any of it.** This was the single largest gap in the document.

| ID | Requirement |
|---|---|
| FR-LST-023 | Specifications are key–value pairs in a dedicated `item_specifications` table. Max **30** per item; key ≤ 40 chars, value ≤ 200 chars. |
| FR-LST-024 | Recommended keys surfaced in the UI: Brand, Model, Year, Color, Warranty. Sellers may add free-form keys. Zero specifications is permitted. |
| FR-LST-025 | **Known Defects & Wear Disclaimer** is a mandatory column on `items`, **10–2000 characters**. For `condition_rating = 'NEW'` the seller may submit the literal value `None — sealed`, but the field can never be empty. |
| FR-LST-026 | Logistics: `location_city`, `location_area`, `location_postal_code` all mandatory on `items`. |
| FR-LST-027 | `weight_class` is an enum: `LIGHT` (≤1 kg), `MEDIUM` (1–5 kg), `HEAVY` (5–20 kg), `BULKY` (>20 kg). **[FILLED]** — v1.0 said "shipping weight class" with no values. |

### 6.6 Listing Lifecycle

| ID | Requirement |
|---|---|
| FR-LST-028 | Statuses: `DRAFT` → `ACTIVE` → (`PENDING_ORDER`) → `SOLD` \| `EXPIRED` \| `CANCELLED`. |
| FR-LST-029 | **`PENDING_ORDER` is a new status added to close a race condition.** Without it, two buyers can click Buy-Now simultaneously and both orders succeed against one physical item. On order creation the item moves to `PENDING_ORDER` and is unpurchasable. **[FIXED]** |
| FR-LST-030 | If OTP verification fails, item returns per **SO-7**. |
| FR-LST-031 | `DRAFT` listings are private to the seller, are not indexed, and expire after **30 days** of inactivity. **[FILLED]** — v1.0 had a `DRAFT` value with no described workflow. |
| FR-LST-032 | Edit and cancellation rules after first bid follow **SO-9**. |

---

## 7. Auction Engine

| ID | Requirement |
|---|---|
| FR-AUC-001 | Bids accepted only while `status = ACTIVE` and `auction_start_time ≤ now < auction_end_time`. |
| FR-AUC-002 | **Bid validity — [FIXED — C4]:**<br>• **First bid:** `amount ≥ starting_bid`<br>• **Subsequent bids:** `amount ≥ current_highest_bid + bid_increment_step`<br>The v1.0 single formula produced a $1.00 minimum on a $500 item. |
| FR-AUC-003 | Sellers cannot bid on their own items (**SO-2**). |
| FR-AUC-004 | Users with `cod_reliability_score < 75` cannot bid — winning would create an order they are barred from completing. **[FILLED]** — v1.0 suspended CoD but left bidding undefined, allowing an auction to be won by someone who cannot transact. |
| FR-AUC-005 | Bid writes are atomic. A **Redis Lua script** holds a per-item lock keyed `lock:item:{item_id}`, TTL **3000 ms**, released on commit. Concurrent bids serialise; losers receive a retry response. **[FILLED]** — v1.0 said "Redis Lua scripts for atomic locks" with no key scheme, TTL, or contention behaviour. |
| FR-AUC-006 | Every bid attempt is persisted, including rejected ones, for the §8 fraud-detection requirement. |

### 7.1 Proxy Bidding

**[FILLED]** — v1.0 described proxy bidding in one sentence and stored `max_proxy_amount`
on the `bids` row, which duplicates the ceiling across every bid and exposes it. Proxy
maxima now live in a separate `auto_bids` table.

| ID | Requirement |
|---|---|
| FR-AUC-007 | A user has at most **one** active auto-bid per item. Re-submitting replaces it, and the new max must exceed the old. |
| FR-AUC-008 | `max_proxy_amount` must be ≥ the current minimum valid bid (FR-AUC-002). |
| FR-AUC-009 | On any new bid, the engine raises the leading auto-bid to `min(competitor_bid + increment, own_max)`. |
| FR-AUC-010 | **Tie-breaking:** if two auto-bids share the same max, the **earlier-submitted** one wins and is placed at exactly that max; the later cannot outbid it. **[FILLED]** — v1.0 defined no tie rule. |
| FR-AUC-011 | Proxy maxima are never exposed via any API response, SSE event, or admin-facing view. |
| FR-AUC-012 | Auto-bid cascades resolve fully within one transaction before any SSE event is emitted, so clients never observe an intermediate bid state. **[FILLED]** |

### 7.2 Anti-Sniping

| ID | Requirement |
|---|---|
| FR-AUC-013 | A bid inside the final **3 minutes** extends `auction_end_time` by **+3 minutes** from the moment of the bid. |
| FR-AUC-014 | Extensions repeat, subject to the **SO-5** hard stop at +60 min past original end. |
| FR-AUC-015 | Each extension emits an SSE `auction_extended` event carrying the new end time. **[FILLED]** |

### 7.3 Conclusion

| ID | Requirement |
|---|---|
| FR-AUC-016 | Auction closure is driven by a **scheduled sweeper** running every **10 seconds**, selecting `status = ACTIVE AND auction_end_time <= now()`. **[FILLED]** — v1.0 never said what triggers closure; the entire auction lifecycle had no execution owner. |
| FR-AUC-017 | Closure is idempotent — a double-run must never create two orders. Enforced by the unique constraint in **DB-ORD-012**. |
| FR-AUC-018 | If `current_highest_bid ≥ reserve_price` (or no reserve set) and ≥1 bid exists → item `SOLD`, order created with status **`PENDING_OTP`**. **[FIXED — C1]** |
| FR-AUC-019 | If reserve not met, or zero bids → item `EXPIRED`, no order (**SO-6**). |
| FR-AUC-020 | On closure, the winner and all losing bidders are notified. **[FILLED]** — v1.0 specified no loser notification. |

### 7.4 Real-Time Transport

| ID | Requirement |
|---|---|
| FR-AUC-021 | SSE endpoint: `GET /api/items/{item_id}/stream`, `Content-Type: text/event-stream`. |
| FR-AUC-022 | Event types: `bid_placed`, `auction_extended`, `auction_closed`, `heartbeat`. **[FILLED]** — v1.0 specified no event schema. |
| FR-AUC-023 | `bid_placed` payload: `{item_id, current_highest_bid, bid_count, min_next_bid, auction_end_time, server_time, leading_bidder_masked}`. Bidder identity is masked (e.g. `r***t`). |
| FR-AUC-024 | Every event carries a monotonic `id:`. Clients reconnect with `Last-Event-ID`, and the server replays missed events. **Without this, a client that briefly loses connectivity silently displays a stale price.** **[FILLED]** |
| FR-AUC-025 | `heartbeat` every **20 s** to defeat proxy idle timeouts. |
| FR-AUC-026 | Server closes idle streams after **30 min**; client reconnects with backoff (1s, 2s, 4s, capped 30s). |
| FR-AUC-027 | Fan-out across app nodes per **ARC-004**. |

---

## 8. Cash on Delivery & Fulfilment

### 8.1 Order Creation

| ID | Requirement |
|---|---|
| FR-COD-001 | Orders originate from Buy-Now or auction win. Both enter at **`PENDING_OTP`**. **[FIXED — C1]** |
| FR-COD-002 | **Buy-Now: the buyer supplies the shipping address before the order is created.** Auction wins: the address is collected at the OTP step. **[FILLED]** — v1.0's flow jumped straight from "Places Order" to OTP with no point at which an address is ever captured, despite `shipping_address` being `NOT NULL`. |
| FR-COD-003 | Order status set: `PENDING_OTP`, `CONFIRMED`, `DISPATCHED`, `DELIVERED_PAID`, `REFUSED_CANCELLED`. **[FIXED — C2]** |

### 8.2 OTP Verification

**[FILLED]** — v1.0 required OTP but specified no length rules, no expiry, no retry
budget, and stored the code in plaintext.

| ID | Requirement |
|---|---|
| FR-COD-004 | OTP is **6 numeric digits**, cryptographically random. |
| FR-COD-005 | Validity **10 minutes** (`otp_expires_at`). Expiry is the "Timeout" branch of the §3.3 diagram, which v1.0 left undefined. |
| FR-COD-006 | Maximum **5** verification attempts (`otp_attempts`). Exhaustion = failure. |
| FR-COD-007 | Maximum **3** resends per order, each ≥60 s apart. Prevents SMS-bombing a third party. |
| FR-COD-008 | **The OTP is stored as a SHA-256 hash, never plaintext.** v1.0's `otp_code VARCHAR(6)` stored live codes in the clear — anyone with read access to a replica could confirm any pending order. |
| FR-COD-009 | Delivery channel: SMS primary, WhatsApp fallback after 60 s of no delivery receipt. Dispatched asynchronously via Kafka topic `otp.send`. |
| FR-COD-010 | On success → `CONFIRMED`. On failure/expiry → `REFUSED_CANCELLED`, item restored per **SO-7**. |
| FR-COD-011 | OTP failure does **not** affect reliability score — it is often a network fault, not buyer misconduct. **[FILLED]** |

### 8.3 Dispatch & Delivery

| ID | Requirement |
|---|---|
| FR-COD-012 | The **seller** marks `CONFIRMED` → `DISPATCHED`, supplying `courier_tracking_id`. **[FILLED]** — v1.0 showed the transition but named no actor. |
| FR-COD-013 | Only an assigned Courier may set the terminal delivery outcome. |
| FR-COD-014 | Courier outcomes: `DELIVERED_PAID` or `REFUSED_CANCELLED`. |
| FR-COD-015 | Courier webhook `POST /api/courier/webhook` authenticates by **HMAC-SHA256** signature over the raw body, with a shared secret per courier account and a ±5-minute timestamp window for replay defence. **[FILLED]** — v1.0's diagram depended on courier callbacks but specified no authentication at all, leaving order status publicly writable. |
| FR-COD-016 | Webhook processing is idempotent on `(order_id, status)`. |
| FR-COD-017 | **Failed delivery attempts** (buyer absent / unreachable — distinct from refusal) increment an `delivery_attempts` counter. Three attempts auto-transition to `REFUSED_CANCELLED`. **[FILLED]** — v1.0's diagram had only two outcomes and no way to represent an unsuccessful attempt. |

### 8.4 Reliability Score

| ID | Requirement |
|---|---|
| FR-COD-018 | New users start at **100.00**. |
| FR-COD-019 | `DELIVERED_PAID` → **+2.00**. `REFUSED_CANCELLED` at doorstep → **−25.00**. |
| FR-COD-020 | Score is clamped to **[0.00, 100.00]**. **[FILLED]** — v1.0 allowed unbounded growth and negative values (`DECIMAL(5,2)` permits 999.99). |
| FR-COD-021 | Score < **75.00** → CoD suspended: the user cannot place orders **or bids** (FR-AUC-004). Browsing continues. |
| FR-COD-022 | **Recovery — [FIXED — C7]:** a suspended user cannot earn +2% because earning it requires completing an order they are barred from placing. The only exit is **Administrator reinstatement** (FR-ROL-003), which resets the score to **80.00** and writes an audit row. This uses the existing Administrator role and adds no new capability. |
| FR-COD-023 | Every score change writes an immutable audit row (old value, new value, cause, order ref). |

### 8.5 Remittance

| ID | Requirement |
|---|---|
| FR-COD-024 | On `DELIVERED_PAID`, a remittance record is created for the seller for **100%** of `final_amount` (**SO-8**). |
| FR-COD-025 | Remittance record carries status `PENDING` \| `SETTLED`. Settlement itself is **out of scope** — v1.0 defines no payout mechanism, and inventing one would exceed this revision's mandate. Flagged as an open dependency. |

---

## 9. UI/UX Requirements

| ID | Requirement |
|---|---|
| FR-UI-001 | Performance budget: **LCP < 1.2 s** on 4G, **CLS < 0.05**. **[FIXED — C5]** |
| FR-UI-002 | v1.0 specified **FID < 50 ms**. FID was retired from Core Web Vitals in March 2024 and is no longer reported by field tooling. Replaced with the successor metric: **INP < 200 ms**. The original intent — input responsiveness — is preserved. **[FIXED]** |
| FR-UI-003 | Breakpoints: mobile < 768 px (2 cols), tablet 768–1199 px (3 cols), desktop ≥ 1200 px (4 cols). **[FILLED]** — v1.0 gave column counts but no pixel boundaries. |
| FR-UI-004 | Search covers `title`, `description`, and specification values. Filters: category, condition, price range, sale type, location city. Sort: Ending Soonest (default for auctions), Newest, Price ↑, Price ↓. **[FILLED]** — v1.0 said "dynamic search, autocomplete" with no fields, filters, or sort orders. |
| FR-UI-005 | Autocomplete triggers at ≥2 characters, debounced **250 ms**, max 8 suggestions. |
| FR-UI-006 | **Quick-bid buttons are `+1×`, `+2×`, `+5×` the item's `bid_increment_step`**, labelled with computed amounts. **[FIXED — C6]** |
| FR-UI-007 | PDP conversion box is sticky and shows: current bid, bid count, min next bid, countdown (per **GC-003**), reserve-met indicator, and the CoD CTA. |
| FR-UI-008 | PDP for a `SOLD`/`EXPIRED` item renders read-only with a clear terminal-state banner and no actionable controls. **[FILLED]** — v1.0 never described the post-auction PDP. |
| FR-UI-009 | Every list and detail view defines **loading**, **empty**, and **error** states. **[FILLED]** — entirely absent from v1.0. |
| FR-UI-010 | Search results paginate per **GC-006**. |
| FR-UI-011 | Countdown under 60 s switches to per-second updates with a visual urgency treatment. |
| FR-UI-012 | Accessibility: WCAG 2.1 AA — keyboard-operable bidding, visible focus rings, ≥4.5:1 contrast, `aria-live` announcements for SSE bid updates. **[FILLED]** — v1.0 had no accessibility requirements. |

---

## 10. Technical Architecture

| ID | Requirement |
|---|---|
| ARC-001 | Edge: Cloudflare CDN + WAF. Origin: NGINX reverse proxy / load balancer. |
| ARC-002 | **Runtime is Node.js (TypeScript).** v1.0 said "Node.js / Go microservices" — an unresolved either/or that cannot be built against. A single runtime is chosen. **[FIXED]** |
| ARC-003 | **Deployment is a modular monolith**, not microservices. v1.0's topology showed two identical application nodes with no service decomposition, no inter-service contracts, and no service boundaries — it depicted a horizontally scaled monolith while calling it microservices. Modules: `auth`, `catalog`, `auction`, `orders`, `media`, `admin`. **[FIXED]** |
| ARC-004 | **SSE fan-out — [FIXED — C8]:** app nodes publish bid events to Redis Pub/Sub channel `auction:{item_id}`; every node subscribes and pushes to its own connected SSE clients. Without this, horizontal scaling silently breaks real-time bidding for a fraction of users. |
| ARC-005 | NGINX must set `proxy_buffering off` and `X-Accel-Buffering: no` on SSE routes, or events are buffered and never delivered. **[FILLED]** |
| ARC-006 | SSE requires **sticky sessions** at the load balancer (`ip_hash` or cookie-based). |
| ARC-007 | PostgreSQL primary takes all writes. Reads route to replicas via **PgBouncer** (transaction pooling). **[FILLED]** — PgBouncer appeared in §6 prose but was missing from the §5.1 topology. |
| ARC-008 | **Read-after-write consistency:** a user's own bid must never appear absent due to replica lag. Reads within **5 s** of a user's own write route to the primary. **[FILLED]** — v1.0's replica design would otherwise show a bidder their bid vanishing. |
| ARC-009 | Kafka topics: `otp.send`, `courier.webhook`, `notification.dispatch`, `audit.log`. Consumer groups per module; at-least-once delivery; consumers idempotent. **[FILLED]** — v1.0 named Kafka's purpose but defined no topics. |
| ARC-010 | Redis holds: bid locks (FR-AUC-005), sessions, live auction cache, SSE pub/sub. |
| ARC-011 | Object storage (S3-compatible) for media per FR-LST-007. |

---

## 11. Database Schema (Corrected)

Changes from v1.0 are annotated inline. All additions exist solely to store data that
v1.0 already required.

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";        -- ADDED: required by FR-UI-004 search

-- 1. USERS
CREATE TABLE users (
    user_id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name             VARCHAR(100) NOT NULL,
    email                 VARCHAR(255) UNIQUE NOT NULL,
    phone_number          VARCHAR(20)  UNIQUE NOT NULL,      -- E.164 (FR-AUT-006)
    password_hash         VARCHAR(255) NOT NULL,
    role                  VARCHAR(20)  NOT NULL DEFAULT 'BUYER'
        CHECK (role IN ('BUYER','SELLER','COURIER','ADMIN')), -- ADDED: v1.0 had no role column
    cod_reliability_score DECIMAL(5,2) NOT NULL DEFAULT 100.00
        CHECK (cod_reliability_score BETWEEN 0 AND 100),      -- ADDED: clamp (FR-COD-020)
    is_verified           BOOLEAN NOT NULL DEFAULT FALSE,     -- phone verified (FR-AUT-003)
    is_suspended          BOOLEAN NOT NULL DEFAULT FALSE,     -- ADDED: FR-ROL-003
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. CATEGORIES
CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    parent_id   INT REFERENCES categories(category_id) ON DELETE SET NULL,
    name        VARCHAR(100) NOT NULL,
    slug        VARCHAR(120) UNIQUE NOT NULL,
    depth       SMALLINT NOT NULL CHECK (depth BETWEEN 1 AND 3)  -- ADDED: FR-LST-011
);

-- 3. ITEMS
CREATE TABLE items (
    item_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    seller_id            UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    title                VARCHAR(100) NOT NULL,               -- FIXED C3: was 150
    description          TEXT NOT NULL,
    known_defects        TEXT NOT NULL,                       -- ADDED: FR-LST-025
    category_id          INT NOT NULL REFERENCES categories(category_id),
    condition_rating     VARCHAR(30) NOT NULL
        CHECK (condition_rating IN ('NEW','LIKE_NEW','GOOD','FAIR','FOR_PARTS')),
    sale_type            VARCHAR(20) NOT NULL
        CHECK (sale_type IN ('FIXED','AUCTION','HYBRID')),
    currency             CHAR(3) NOT NULL DEFAULT 'USD',      -- ADDED: SO-1
    fixed_price          DECIMAL(12,2) CHECK (fixed_price > 0),
    starting_bid         DECIMAL(12,2) CHECK (starting_bid > 0),
    reserve_price        DECIMAL(12,2),
    current_highest_bid  DECIMAL(12,2),                       -- FIXED C4: NULL until first bid
    bid_count            INT NOT NULL DEFAULT 0,              -- ADDED: FR-AUC-023
    bid_increment_step   DECIMAL(10,2) CHECK (bid_increment_step > 0),
    auction_start_time   TIMESTAMPTZ,
    auction_end_time     TIMESTAMPTZ,
    original_end_time    TIMESTAMPTZ,                         -- ADDED: SO-5 extension cap
    -- Logistics (ADDED: FR-LST-026/027 — entirely absent from v1.0)
    location_city        VARCHAR(100) NOT NULL,
    location_area        VARCHAR(100) NOT NULL,
    location_postal_code VARCHAR(20)  NOT NULL,
    weight_class         VARCHAR(10)  NOT NULL
        CHECK (weight_class IN ('LIGHT','MEDIUM','HEAVY','BULKY')),
    status               VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT','ACTIVE','PENDING_ORDER','SOLD','EXPIRED','CANCELLED')),
                                                              -- ADDED PENDING_ORDER: FR-LST-029
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- ADDED: FR-LST-017. v1.0 allowed a FIXED listing with no price at all.
    CONSTRAINT chk_pricing_shape CHECK (
        (sale_type = 'FIXED'
            AND fixed_price IS NOT NULL
            AND starting_bid IS NULL AND auction_end_time IS NULL)
     OR (sale_type = 'AUCTION'
            AND fixed_price IS NULL
            AND starting_bid IS NOT NULL AND bid_increment_step IS NOT NULL
            AND auction_start_time IS NOT NULL AND auction_end_time IS NOT NULL)
     OR (sale_type = 'HYBRID'
            AND fixed_price IS NOT NULL
            AND starting_bid IS NOT NULL AND bid_increment_step IS NOT NULL
            AND auction_start_time IS NOT NULL AND auction_end_time IS NOT NULL)
    ),
    CONSTRAINT chk_reserve   CHECK (reserve_price IS NULL OR reserve_price >= starting_bid),
    CONSTRAINT chk_auction_window CHECK (auction_end_time IS NULL
                                      OR auction_end_time > auction_start_time)
);

-- 4. ITEM MEDIA  (ADDED — v1.0 required 3-10 images + video but had NO media table)
CREATE TABLE item_media (
    media_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id      UUID NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    media_type   VARCHAR(10) NOT NULL CHECK (media_type IN ('IMAGE','VIDEO')),
    storage_key  VARCHAR(500) NOT NULL,
    is_primary   BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order   SMALLINT NOT NULL DEFAULT 0,
    width_px     INT,
    height_px    INT,
    duration_s   SMALLINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_media_one_primary
    ON item_media(item_id) WHERE is_primary;                  -- exactly one primary

-- 5. ITEM SPECIFICATIONS  (ADDED — v1.0 §3.1 required this "structured key-value grid"
--    but the schema had nowhere to put it)
CREATE TABLE item_specifications (
    spec_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id  UUID NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    spec_key   VARCHAR(40)  NOT NULL,
    spec_value VARCHAR(200) NOT NULL,
    sort_order SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (item_id, spec_key)
);

-- 6. BIDS
CREATE TABLE bids (
    bid_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id     UUID NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    bidder_id   UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    bid_amount  DECIMAL(12,2) NOT NULL CHECK (bid_amount > 0),
    is_auto_bid BOOLEAN NOT NULL DEFAULT FALSE,
    -- max_proxy_amount REMOVED -> auto_bids table (FR-AUC-007/011)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. AUTO BIDS  (ADDED — proxy ceilings must not live on bid rows)
CREATE TABLE auto_bids (
    auto_bid_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id          UUID NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    bidder_id        UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    max_proxy_amount DECIMAL(12,2) NOT NULL CHECK (max_proxy_amount > 0),
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (item_id, bidder_id)                               -- FR-AUC-007
);

-- 8. ORDERS
CREATE TABLE orders (
    order_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    item_id            UUID NOT NULL REFERENCES items(item_id) ON DELETE RESTRICT,
    buyer_id           UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    seller_id          UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    winning_bid_id     UUID REFERENCES bids(bid_id),          -- ADDED: link to winning bid
    final_amount       DECIMAL(12,2) NOT NULL CHECK (final_amount > 0),
    currency           CHAR(3) NOT NULL DEFAULT 'USD',        -- ADDED: SO-1
    payment_method     VARCHAR(20) NOT NULL DEFAULT 'COD',
    order_status       VARCHAR(30) NOT NULL DEFAULT 'PENDING_OTP'
        CHECK (order_status IN ('PENDING_OTP','CONFIRMED','DISPATCHED',
                                'DELIVERED_PAID','REFUSED_CANCELLED')),
                       -- FIXED C1: PENDING_COD_VERIFICATION withdrawn
                       -- FIXED C2: schema names are canonical
    shipping_address   TEXT NOT NULL,
    shipping_phone     VARCHAR(20) NOT NULL,
    courier_id         UUID REFERENCES users(user_id),        -- ADDED: FR-COD-013
    courier_tracking_id VARCHAR(100),
    delivery_attempts  SMALLINT NOT NULL DEFAULT 0,           -- ADDED: FR-COD-017
    -- OTP (ADDED expiry + attempts; hash replaces plaintext — FR-COD-004..008)
    otp_hash           CHAR(64),                              -- SHA-256, never plaintext
    otp_expires_at     TIMESTAMPTZ,
    otp_attempts       SMALLINT NOT NULL DEFAULT 0,
    otp_resend_count   SMALLINT NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ADDED: FR-AUC-017. Without this, a double-run of the closure sweeper creates
-- two orders for one physical item.
CREATE UNIQUE INDEX idx_orders_one_active_per_item
    ON orders(item_id) WHERE order_status <> 'REFUSED_CANCELLED';

-- 9. RELIABILITY AUDIT  (ADDED: FR-COD-023)
CREATE TABLE reliability_events (
    event_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id    UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    order_id   UUID REFERENCES orders(order_id),
    old_score  DECIMAL(5,2) NOT NULL,
    new_score  DECIMAL(5,2) NOT NULL,
    reason     VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 10. MODERATION AUDIT  (ADDED: FR-ROL-004 — v1.0 granted admins "Moderation / Audit"
--     but provided no audit table)
CREATE TABLE moderation_actions (
    action_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id    UUID NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    target_type VARCHAR(20) NOT NULL CHECK (target_type IN ('ITEM','USER','ORDER')),
    target_id   UUID NOT NULL,
    action      VARCHAR(40) NOT NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 11. REMITTANCES  (ADDED: FR-COD-024 — "Funds Remitted to Seller" appeared in v1.0's
--     flow diagram with no supporting table)
CREATE TABLE remittances (
    remittance_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id      UUID NOT NULL UNIQUE REFERENCES orders(order_id),
    seller_id     UUID NOT NULL REFERENCES users(user_id),
    amount        DECIMAL(12,2) NOT NULL,
    currency      CHAR(3) NOT NULL DEFAULT 'USD',
    status        VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','SETTLED')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- INDICES
CREATE INDEX idx_bids_item_id_amount   ON bids(item_id, bid_amount DESC);
CREATE INDEX idx_bids_bidder           ON bids(bidder_id);            -- ADDED
CREATE INDEX idx_items_status_end_time ON items(status, auction_end_time);
CREATE INDEX idx_items_category        ON items(category_id);
CREATE INDEX idx_items_seller          ON items(seller_id);           -- ADDED
CREATE INDEX idx_items_title_trgm      ON items USING GIN (title gin_trgm_ops); -- FR-UI-004
CREATE INDEX idx_orders_buyer          ON orders(buyer_id);           -- ADDED
CREATE INDEX idx_orders_status         ON orders(order_status);       -- ADDED
CREATE INDEX idx_media_item            ON item_media(item_id, sort_order);

-- updated_at maintenance (ADDED — v1.0 declared updated_at columns with a DEFAULT
-- but nothing ever updated them, so they would have been permanently wrong)
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_touch  BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_items_touch  BEFORE UPDATE ON items
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER trg_orders_touch BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
```

---

## 12. Non-Functional & Security

| ID | Requirement |
|---|---|
| NFR-001 | All writes → PostgreSQL primary. Reads → replicas via PgBouncer, subject to **ARC-008**. |
| NFR-002 | TLS 1.3 only. HSTS `max-age=31536000; includeSubDomains`. |
| NFR-003 | Passwords: argon2id with the parameters in **FR-AUT-002**. |
| NFR-004 | **Rate limiting — [FILLED]:** v1.0 specified only "5 bid attempts/sec/IP". IP-only limiting is unsound where mobile users share carrier NAT — one abuser throttles a whole region, and one abuser with many IPs is unaffected. Limits are therefore layered:<br>• Bids: 5/s per IP **and** 10/min per user per item<br>• OTP send: 3 per order, 10/hour per phone<br>• Login: FR-AUT-008<br>• Listing creation: 20/day per seller<br>• Search: 30/min per IP |
| NFR-005 | Fraud signals flagged for admin review: >20 bids/min on one item; two accounts alternating bids on the same item repeatedly; a new account (<24 h) bidding above a configurable high-value threshold. **[FILLED]** — v1.0 said "automated anti-fraud flags for unusual bidding patterns" without defining a single pattern. |
| NFR-006 | Input validation on every endpoint (schema-validated). Parameterised queries only. Output encoding on all user-supplied strings. |
| NFR-007 | Security headers: CSP, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`. **[FILLED]** |
| NFR-008 | PII (`phone_number`, `shipping_address`) encrypted at rest. Never written to application logs. **[FILLED]** |
| NFR-009 | Availability target **99.5%** monthly. Nightly full backup + PITR via WAL archiving, retention 30 days. **[FILLED]** — v1.0 had no availability or backup requirement. |
| NFR-010 | Structured JSON logging with correlation IDs. Metrics: bid latency p50/p95/p99, SSE connection count, OTP success rate, auction closure lag. |

---

## 13. Open Dependencies

Items that cannot be resolved without information outside v1.0. **None block the build**;
each has a stated interim position.

| # | Item | Interim position |
|---|---|---|
| O-1 | SMS/WhatsApp provider not named. | Abstract behind a `NotificationProvider` interface; supply a logging stub for development. |
| O-2 | Courier company integration contract unknown. | Implement the generic HMAC webhook (FR-COD-015); map per-carrier payloads in an adapter layer. |
| O-3 | Seller payout/settlement mechanism undefined (**FR-COD-025**). | Record remittances as `PENDING`. Settlement is out of scope. |
| O-4 | Target market / currency (**SO-1**). | Config-driven, default `USD`. |
| O-5 | Category seed data not supplied. | Ship a minimal 3-level starter taxonomy; content is an owner deliverable. |
| O-6 | Legal — CoD consumer-protection and data-retention obligations vary by market. | Requires owner/legal input before launch. |

---

## Appendix A — Build Sequence

Ordered so each stage is independently testable and nothing depends on later work.

| Stage | Deliverable | Gate |
|---|---|---|
| 1 | Schema (§11) + migrations | All constraints provable by test inserts |
| 2 | Auth & accounts (§5) | Register → verify → login → refresh → logout |
| 3 | Categories + taxonomy | 3-level tree enforced; leaf-only attachment |
| 4 | Listing CRUD + media pipeline (§6) | Publish blocked below 3 images; all CHECKs hold |
| 5 | Search, filters, grid (FR-UI-003/004) | Pagination and all sort orders |
| 6 | Buy-Now + order creation (§8.1) | `PENDING_ORDER` prevents double purchase |
| 7 | OTP lifecycle (§8.2) | Expiry, attempt cap, resend cap, hashed storage |
| 8 | Bidding engine (§7, no proxy) | FR-AUC-002 first-vs-subsequent bid rule |
| 9 | Redis locking (FR-AUC-005) | Concurrent bids serialise correctly under load |
| 10 | Proxy bidding (§7.1) | Tie-break and cascade behaviour |
| 11 | SSE + Redis fan-out (§7.4, ARC-004) | Event replay via `Last-Event-ID` |
| 12 | Anti-snipe + closure sweeper (§7.2/7.3) | Idempotent closure; extension cap |
| 13 | Dispatch, courier webhook, reliability (§8.3/8.4) | HMAC auth; score clamping |
| 14 | Admin moderation + audit (§4) | Every action audited |
| 15 | Performance hardening (§9) | LCP/INP/CLS budgets met |

**Stages 8–12 are the highest-risk work.** Concurrency, proxy cascades, and SSE fan-out
are where correctness bugs hide, and they are the least forgiving of a vague spec — which
is why they carry the most detail above.

---

## Appendix B — Requirements Traceability

| v1.0 § | Status in v2.0 |
|---|---|
| §1 Vision | Retained. C5 corrected. |
| §2 Roles | Retained + Courier documented (implied by §3.3), admin powers enumerated. |
| §3.1 Listings | Retained. Media, specifications, defects, logistics given schema. |
| §3.2 Auctions | Retained. C1 and C4 corrected; proxy, closure, SSE specified. |
| §3.3 CoD | Retained. C2 and C7 corrected; OTP and courier auth specified. |
| §4 UI/UX | Retained. C6 corrected; FID→INP; breakpoints and states added. |
| §5.1 Infrastructure | Retained. C8 corrected; runtime and topology disambiguated. |
| §5.2 Schema | Retained and extended. No table removed; `bids.max_proxy_amount` relocated. |
| §6 NFR | Retained. Rate limiting and fraud rules made concrete. |

**No feature present in v1.0 was removed. No feature absent from v1.0 was added.**
