-- =============================================================================
-- Nistula Unified Messaging Platform -- PostgreSQL Schema
--
-- Hardest design decision (the brief asks reviewers to read this):
-- ----------------------------------------------------------------------------
-- The hardest decision was modeling guest identity. The webhook hands us a
-- guest_name and a channel, but the same human reaches us under a phone
-- number on WhatsApp, a handle on Instagram, a masked email from
-- Booking.com, and their real email on a direct enquiry. Treating
-- guest_name as canonical creates duplicate guest rows on every
-- name-spelling variation and breaks the loyalty engine -- repeat-stay
-- detection becomes structurally impossible.
--
-- We separated `guests` (canonical identity, one row per real human) from
-- `guest_identifiers` (channel-specific handles, many-to-one), with
-- `(channel, identifier_value)` as the unique lookup key. Identity merging
-- is then a deliberate, auditable operation a human triggers, not an
-- accident of how someone typed their name. The trade-off is added join
-- complexity on every guest lookup -- a webhook must resolve a
-- channel-specific handle into a guest_id before doing anything else --
-- but that join is small and indexed, and it's the only design that makes
-- 360-degree guest profiles real rather than theatrical.
-- ----------------------------------------------------------------------------
--
-- File is re-runnable: DROP IF EXISTS preamble lets the reviewer execute
-- against the same database without manual cleanup. PostgreSQL >=13
-- recommended (gen_random_uuid is core in 13+; pgcrypto provides it for
-- older releases).
-- =============================================================================


-- ----- Re-runnable preamble -------------------------------------------------
-- Drop in reverse-dependency order. CASCADE on the parents we own.
DROP TABLE IF EXISTS message_audit_log CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS reservations CASCADE;
DROP TABLE IF EXISTS guest_identifiers CASCADE;
DROP TABLE IF EXISTS guests CASCADE;
DROP TABLE IF EXISTS agents CASCADE;
DROP TABLE IF EXISTS properties CASCADE;

DROP TYPE IF EXISTS send_status;
DROP TYPE IF EXISTS message_direction;
DROP TYPE IF EXISTS action_type;
DROP TYPE IF EXISTS query_type;
DROP TYPE IF EXISTS source_channel;


-- ----- Extensions -----------------------------------------------------------
-- gen_random_uuid() is core in PG 13+, but enabling pgcrypto keeps the
-- script portable to older releases that ship gen_random_uuid only via
-- this extension.
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ----- Enumerated types -----------------------------------------------------
-- Each enum mirrors the matching Literal in src/models.py exactly so a
-- future ORM round-trips without translation. Adding new values means a
-- one-line ALTER TYPE migration plus an update to the Pydantic Literal.

CREATE TYPE source_channel AS ENUM (
    'whatsapp',
    'booking_com',
    'airbnb',
    'instagram',
    'direct'
);

CREATE TYPE query_type AS ENUM (
    'pre_sales_availability',
    'pre_sales_pricing',
    'post_sales_checkin',
    'special_request',
    'complaint',
    'general_enquiry'
);

CREATE TYPE action_type AS ENUM (
    'auto_send',
    'agent_review',
    'escalate'
);

CREATE TYPE message_direction AS ENUM (
    'inbound',     -- from guest to Nistula
    'outbound'     -- from Nistula (AI or agent) to guest
);

CREATE TYPE send_status AS ENUM (
    'pending',     -- drafted, awaiting send (auto or agent approval)
    'sent',        -- delivered to the channel
    'failed'       -- channel rejected the send; see message_audit_log for the reason
);


-- ============================================================================
-- properties
--   Inventory of bookable units. Hybrid layout: queryable columns for
--   anything the messaging platform joins or displays (slug, max_guests,
--   rate card, check-in/out times) and a `details` JSONB for the long
--   tail (wifi_password, chef_on_call flags, per-property availability
--   snapshots, etc.) that doesn't need indexing or type safety.
-- ============================================================================
CREATE TABLE properties (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                        TEXT NOT NULL UNIQUE,         -- stable identifier exposed to channels (e.g. 'villa-b1')
    name                        TEXT NOT NULL,
    location                    TEXT NOT NULL,
    bedrooms                    INTEGER NOT NULL CHECK (bedrooms >= 0),
    max_guests                  INTEGER NOT NULL CHECK (max_guests > 0),
    private_pool                BOOLEAN NOT NULL DEFAULT FALSE,
    check_in_time               TIME NOT NULL,
    check_out_time              TIME NOT NULL,

    -- Rate card. INR (whole rupees) stored as INTEGER -- no fractional rupees in
    -- normal pricing, and INTEGER avoids floating-point drift on aggregations.
    base_rate_inr               INTEGER NOT NULL CHECK (base_rate_inr >= 0),
    base_rate_includes_guests   INTEGER NOT NULL CHECK (base_rate_includes_guests > 0),
    extra_guest_inr_per_night   INTEGER NOT NULL DEFAULT 0 CHECK (extra_guest_inr_per_night >= 0),

    cancellation_policy         TEXT NOT NULL,

    -- Long tail: wifi_password, chef_on_call, chef_requires_prebooking,
    -- availability snapshots (e.g. availability_april_20_24), and any
    -- per-property metadata channels send. Production would extract
    -- secret-y values (wifi_password) into a dedicated secrets store.
    details                     JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- agents
--   Internal staff users. Caretakers, managers, founders. Soft-delete is
--   not modeled here -- agents leaving the company are deactivated
--   (is_active=FALSE), which preserves audit-log foreign keys without
--   pretending the row is gone.
-- ============================================================================
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL,                          -- 'caretaker' | 'manager' | 'founder' | etc. Free-form for org flexibility.
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- guests
--   Canonical guest identity. ONE row per real human, regardless of how
--   many channels they've reached us on. Display_name is the best-known
--   name we have; it gets updated as we learn more (e.g. an Instagram
--   handle resolves to a real name after a booking). Soft-delete via
--   `deleted_at` because legal retention may require keeping the row
--   even after a guest 'deletes' their account, and historical bookings
--   must remain attributable for auditability.
-- ============================================================================
CREATE TABLE guests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name    TEXT,
    notes           TEXT,                                   -- free-form staff notes (allergies, VIP context, etc.)
    deleted_at      TIMESTAMPTZ,                            -- NULL = active; non-null = soft-deleted
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- guest_identifiers
--   Channel-specific handles linked many-to-one to a `guests` row. This
--   is the resolution table that turns 'whatsapp:+919812345678' or
--   'instagram:@sneha_k' into a guest_id. The (channel, identifier_value)
--   composite is UNIQUE so the same handle on the same channel cannot
--   point to two guests; cross-channel collisions are fine (a phone
--   number on WhatsApp and a different phone number on Booking.com both
--   pointing at the same guest is the WHOLE POINT).
-- ============================================================================
CREATE TABLE guest_identifiers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_id            UUID NOT NULL REFERENCES guests(id) ON DELETE CASCADE,  -- handles can't outlive the guest record
    channel             source_channel NOT NULL,
    identifier_value    TEXT NOT NULL,                       -- phone, email (real or masked), IG handle, etc.
    identifier_kind     TEXT NOT NULL,                       -- 'phone' | 'email' | 'masked_email' | 'ig_handle' | etc.
    is_primary          BOOLEAN NOT NULL DEFAULT FALSE,      -- the preferred handle on this channel for this guest
    verified_at         TIMESTAMPTZ,                         -- when we confirmed this identifier really belongs to this guest
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- The central design constraint: a handle on a channel maps to AT MOST
    -- one guest. This is also the lookup index used at every webhook hit.
    UNIQUE (channel, identifier_value)
);


-- ============================================================================
-- reservations
--   Bookings. Linked to a single guest and a single property.
--   booking_ref is the human-visible reference shown to the guest and
--   used by channels (e.g. 'NIS-2026-0145'). Soft-delete because a
--   cancelled reservation is still an artifact a finance/ops audit needs
--   to see; hard-deleting it would orphan all related conversations and
--   messages.
-- ============================================================================
CREATE TABLE reservations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_ref         TEXT NOT NULL UNIQUE,
    guest_id            UUID NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,    -- a guest with bookings cannot be hard-deleted
    property_id         UUID NOT NULL REFERENCES properties(id) ON DELETE RESTRICT,
    check_in_date       DATE NOT NULL,
    check_out_date      DATE NOT NULL CHECK (check_out_date > check_in_date),
    num_guests          INTEGER NOT NULL CHECK (num_guests > 0),
    total_inr           INTEGER NOT NULL CHECK (total_inr >= 0),
    status              TEXT NOT NULL,                       -- 'confirmed' | 'cancelled' | 'completed' | 'no_show' -- free-form for ops flex
    source              source_channel NOT NULL,             -- where the booking came from
    deleted_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- conversations
--   A messaging thread between Nistula and a guest, scoped to a single
--   channel. reservation_id is NULLABLE because pre-sales conversations
--   exist before any booking is created (PLAN S10.2). assigned_agent_id
--   is NULLABLE because most conversations stay AI-handled -- agents are
--   only attached when the confidence score escalates.
-- ============================================================================
CREATE TABLE conversations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guest_id            UUID NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    reservation_id      UUID REFERENCES reservations(id) ON DELETE SET NULL,       -- pre-sales conversations have no booking yet
    channel             source_channel NOT NULL,
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    last_message_at     TIMESTAMPTZ,
    assigned_agent_id   UUID REFERENCES agents(id) ON DELETE SET NULL,             -- agent leaving doesn't lose the thread
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- messages
--   The single source of truth for every message in/out of Nistula,
--   regardless of channel or sender. `direction` distinguishes inbound
--   (guest -> us) from outbound (us -> guest); `sender_kind` distinguishes
--   the three outbound originators (AI, agent, system). One `body` column
--   holds the canonical text -- agent edits are captured as before/after
--   pairs in message_audit_log rather than duplicated as separate columns
--   here.
--
--   raw_payload preserves the original webhook body verbatim. Channels are
--   heterogeneous (WhatsApp adds message IDs, Booking.com adds
--   reservation metadata) and we will need that data for debugging, for
--   re-processing under a future schema, and for compliance traceability.
-- ============================================================================
CREATE TABLE messages (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id         UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction               message_direction NOT NULL,
    source_channel          source_channel NOT NULL,
    sender_kind             TEXT NOT NULL,                       -- 'guest' | 'ai' | 'agent' (TEXT, not enum, to leave room for future kinds like 'system')
    sender_agent_id         UUID REFERENCES agents(id) ON DELETE SET NULL,  -- only set when sender_kind = 'agent'
    body                    TEXT NOT NULL,
    timestamp               TIMESTAMPTZ NOT NULL,                -- channel-reported send/receive time
    raw_payload             JSONB NOT NULL DEFAULT '{}'::jsonb,  -- original webhook payload for inbound; channel send-receipt for outbound

    -- AI-tracking columns. Populated for outbound AI-drafted messages and
    -- for inbound messages whose classification informs the conversation
    -- routing. NULL on guest messages and on pure-agent outbound messages.
    ai_drafted              BOOLEAN NOT NULL DEFAULT FALSE,
    ai_confidence_score     NUMERIC(4, 3) CHECK (ai_confidence_score >= 0 AND ai_confidence_score <= 1),
    query_type              query_type,
    action_taken            action_type,

    -- Outbox state. Pending until the channel confirms send.
    agent_edited            BOOLEAN NOT NULL DEFAULT FALSE,
    send_status             send_status NOT NULL DEFAULT 'pending',
    sent_at                 TIMESTAMPTZ,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================================
-- message_audit_log
--   Append-only trail of every state change on a message: AI drafted,
--   agent edited, agent approved, sent, rejected, escalated. before_text
--   / after_text capture body edits so we can reconstruct the AI's
--   original draft even after a human rewrites it. The audit log is the
--   reason we keep messages.body as a single column rather than carrying
--   separate ai_body / agent_body columns -- we get the same information
--   here without duplicating storage on every message.
-- ============================================================================
CREATE TABLE message_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    actor_agent_id  UUID REFERENCES agents(id) ON DELETE SET NULL,    -- NULL = system / AI action
    action          TEXT NOT NULL,                                    -- 'drafted_by_ai' | 'edited_by_agent' | 'approved' | 'sent' | 'rejected' | 'escalated' | etc.
    before_text     TEXT,                                             -- prior body when action is 'edited_by_agent'
    after_text      TEXT,                                             -- new body when action mutates body
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ----- Indexes --------------------------------------------------------------
-- PLAN S10.3 names the minimum set: messages.conversation_id,
-- messages.timestamp, guest_identifiers (channel, identifier_value),
-- reservations.booking_ref. The (channel, identifier_value) UNIQUE on
-- guest_identifiers and the booking_ref UNIQUE on reservations are
-- already created inline as constraints, which implicitly index. The
-- rest are explicit below.

-- Conversation thread fetches: 'last N messages in this thread' is the
-- dominant read pattern. A composite (conversation_id, timestamp DESC)
-- supports both 'all messages for a conversation' and the time-ordered
-- variant from a single index.
CREATE INDEX idx_messages_conversation_id_timestamp
    ON messages (conversation_id, timestamp DESC);

-- Time-window analytics ('messages received in the last hour'); separate
-- from the composite above because the leading column matters.
CREATE INDEX idx_messages_timestamp
    ON messages (timestamp DESC);

-- Guest -> all conversations.
CREATE INDEX idx_conversations_guest_id
    ON conversations (guest_id);

-- Reservation -> conversations about that booking. Partial index because
-- the column is nullable and we only care about the bound rows.
CREATE INDEX idx_conversations_reservation_id
    ON conversations (reservation_id)
    WHERE reservation_id IS NOT NULL;

-- Outbox: 'find pending messages to send'. Partial index on the hot path.
CREATE INDEX idx_messages_send_status_pending
    ON messages (created_at)
    WHERE send_status = 'pending';

-- Active guests for membership/loyalty queries. Partial index keeps it tight.
CREATE INDEX idx_guests_active
    ON guests (id)
    WHERE deleted_at IS NULL;

-- Audit log lookups by message_id (the most common access pattern -- 'show me
-- the history of this message').
CREATE INDEX idx_message_audit_log_message_id
    ON message_audit_log (message_id);


-- ----- End of schema --------------------------------------------------------
