CREATE TRIGGER IF NOT EXISTS room_events_no_replace
BEFORE INSERT ON room_events
WHEN EXISTS (
    SELECT 1 FROM room_events AS existing
    WHERE existing.event_id = NEW.event_id
       OR (existing.session_id = NEW.session_id AND existing.sequence_no = NEW.sequence_no)
       OR (existing.session_id = NEW.session_id AND existing.idempotency_key = NEW.idempotency_key)
)
BEGIN
    SELECT RAISE(ABORT, 'room_events identity is immutable');
END;
