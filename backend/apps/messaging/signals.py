"""Hooks other apps (such as the delivery engine) can subscribe to."""
from django.dispatch import Signal

# Sent after a delivery report moves a message forward. Keyword arguments:
#   reference (the id the caller passed to send_advisory), provider_message_id,
#   status (queued/submitted/sent/delivered/failed), error_code.
delivery_status_changed = Signal()
