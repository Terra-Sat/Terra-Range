"""The SMS location conversation, driven by the Location tree.

After someone confirms consent they are asked where they are, one level at a time
(for example county, then sub-county, then ward), until they reach a node with no children.
The tree can be any depth, so adding a level needs data, not code.

Conversation state lives on the Recipient (awaiting_location, location_cursor, location_page).
Every function here only changes the recipient row and returns the text to send; the caller
saves inside its transaction and sends after the commit, so a duplicate webhook can never
produce a second reply.

Replies the person can send while a question is open:
    a number          choose that option on the current page
    a name            choose by name (exact, unique prefix, or unique fragment of 3+ letters)
    MORE / ZAIDI      next page of options
    BACK / NYUMA      go up one level
    0                 "not listed": keep the current area (only below the top level)
"""
from dataclasses import dataclass
from typing import Optional

from django.conf import settings

from apps.messaging.core.keywords import BACK_WORDS, NEXT_PAGE_WORDS, normalise_text
from apps.messaging.enums import Purpose
from apps.messaging.messages import render_location_prompt, render_location_saved
from apps.messaging.models import Location

_STATE_FIELDS = ["awaiting_location", "location_cursor", "location_page"]


@dataclass(frozen=True)
class FlowReply:
    purpose: str
    body: str


@dataclass(frozen=True)
class FlowResult:
    outcome: str
    reply: Optional[FlowReply] = None


def locations_loaded():
    return Location.objects.filter(is_active=True).exists()


def _children(parent):
    return list(Location.objects.filter(parent=parent, is_active=True).order_by("sort_order", "name"))


def _paginate(children, fits):
    """Greedy pages: as many options per page as still fit the SMS length budget."""
    pages, current = [], []
    for child in children:
        if current and not fits(current + [child]):
            pages.append(current)
            current = [child]
        else:
            current.append(child)
    if current:
        pages.append(current)
    return pages


def _pages_for(recipient, children):
    cursor = recipient.location_cursor
    level = children[0].level
    unlisted = cursor.name if cursor is not None else ""
    max_chars = getattr(settings, "MESSAGING_LOCATION_SMS_MAX_CHARS", 300)
    max_options = getattr(settings, "MESSAGING_LOCATION_MAX_OPTIONS", 9)

    def fits(options):
        if len(options) > max_options:
            return False
        text = render_location_prompt(
            recipient.language, level=level, options=[o.name for o in options],
            more=True, back=cursor is not None, unlisted_name=unlisted,
        )
        return len(text) <= max_chars

    return _paginate(children, fits)


def _prompt(recipient, *, sorry=False):
    """Build the question for the current cursor and page. Assumes the cursor has children."""
    children = _children(recipient.location_cursor)
    pages = _pages_for(recipient, children)
    index = recipient.location_page % len(pages)
    if index != recipient.location_page:
        recipient.location_page = index
        recipient.save(update_fields=["location_page"])
    cursor = recipient.location_cursor
    body = render_location_prompt(
        recipient.language,
        level=children[0].level,
        options=[o.name for o in pages[index]],
        more=len(pages) > 1,
        back=cursor is not None,
        unlisted_name=cursor.name if cursor is not None else "",
        sorry=sorry,
    )
    return FlowReply(Purpose.LOCATION_PROMPT, body)


def start(recipient):
    """Begin, or restart from the top, asking this person where they are.

    Returns the first question, or None when no locations are loaded (nothing to ask).
    """
    if not _children(None):
        return None
    recipient.awaiting_location = True
    recipient.location_cursor = None
    recipient.location_page = 0
    recipient.save(update_fields=_STATE_FIELDS)
    return _prompt(recipient)


def reask(recipient):
    """Repeat the open question (for example when they answer YES again)."""
    if not recipient.awaiting_location or not _children(recipient.location_cursor):
        return None
    return _prompt(recipient)


def cancel(recipient):
    """Stop asking, for example after STOP. Leaves any saved location untouched."""
    if recipient.awaiting_location or recipient.location_cursor_id or recipient.location_page:
        recipient.awaiting_location = False
        recipient.location_cursor = None
        recipient.location_page = 0
        recipient.save(update_fields=_STATE_FIELDS)


def _finish(recipient, node):
    recipient.location = node
    recipient.ward_code = node.code
    recipient.awaiting_location = False
    recipient.location_cursor = None
    recipient.location_page = 0
    recipient.save(update_fields=["location", "ward_code", *_STATE_FIELDS])
    return FlowResult("location_saved", FlowReply(Purpose.LOCATION_CONFIRMATION, render_location_saved(recipient.language, node.label())))


def _choose(recipient, node):
    if _children(node):
        recipient.location_cursor = node
        recipient.location_page = 0
        recipient.save(update_fields=["location_cursor", "location_page"])
        return FlowResult("location_next", _prompt(recipient))
    return _finish(recipient, node)


def _match_by_name(children, text):
    """Exact name, else a unique prefix, else a unique fragment of at least 3 letters."""
    exact = [c for c in children if c.search_name == text]
    if len(exact) == 1:
        return exact[0]
    prefix = [c for c in children if c.search_name.startswith(text)]
    if len(prefix) == 1:
        return prefix[0]
    if len(text) >= 3:
        fragment = [c for c in children if text in c.search_name]
        if len(fragment) == 1:
            return fragment[0]
    return None


def answer(recipient, raw_text):
    """Interpret a reply to the open question. The caller has checked consent is active."""
    cursor = recipient.location_cursor
    children = _children(cursor)
    if not children:
        # The tree changed while this person was mid-conversation.
        if cursor is not None:
            return _finish(recipient, cursor)
        cancel(recipient)
        return FlowResult("location_unavailable")

    text = normalise_text(raw_text)
    if text in NEXT_PAGE_WORDS:
        recipient.location_page += 1
        recipient.save(update_fields=["location_page"])
        return FlowResult("location_page", _prompt(recipient))

    if text in BACK_WORDS and cursor is not None:
        recipient.location_cursor = cursor.parent
        recipient.location_page = 0
        recipient.save(update_fields=["location_cursor", "location_page"])
        return FlowResult("location_back", _prompt(recipient))

    if text == "0" and cursor is not None:
        return _finish(recipient, cursor)

    chosen = None
    if text.isdigit():
        pages = _pages_for(recipient, children)
        page = pages[recipient.location_page % len(pages)]
        number = int(text)
        if 1 <= number <= len(page):
            chosen = page[number - 1]
    elif text:
        chosen = _match_by_name(children, text)

    if chosen is None:
        return FlowResult("location_invalid", _prompt(recipient, sorry=True))
    return _choose(recipient, chosen)
