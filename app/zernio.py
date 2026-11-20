"""Zernio inbox adapter (same contract as the Rentas bot: https://docs.zernio.com/webhooks/inbox)."""
import re
from datetime import datetime
from sqlalchemy import select
from app.db import Outbox


def ingest_zernio(bot, payload):
    if payload.get('test') or payload.get('event') == 'webhook.test':
        return
    account = payload.get('account') or {}
    if (account.get('accountId') or account.get('id')) != bot.settings.zernio_account_id:
        return
    if account.get('platform') != 'whatsapp':
        return
    event = payload.get('event')
    if event not in {'message.received', 'message.sent', 'message.failed', 'conversation.control_changed'}:
        return
    message = payload.get('message') or {}
    if event == 'message.failed':
        with bot.db.session() as s:
            row = s.scalar(select(Outbox).where(Outbox.provider_id == (message.get('platformMessageId') or '')))
            if row:
                row.status, row.error = 'delivery_failed', 'provider_delivery_failed'
                s.commit()
        return
    conversation = payload.get('conversation') or {}
    cid = conversation.get('id')
    if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', cid):
        raise ValueError('Invalid Zernio conversation')
    participant = conversation.get('participantId') or (message.get('sender') or {}).get('id', '')
    if not (isinstance(participant, str) and participant.isdigit() and len(participant) <= 32):
        return
    name = str(conversation.get('participantName') or (message.get('sender') or {}).get('name') or '')[:120]
    echo = False
    if event == 'conversation.control_changed':
        if (payload.get('control') or {}).get('owner') == 'app':
            return
        echo = True
    if event == 'message.sent':
        own_id = message.get('platformMessageId') or message.get('id')
        if isinstance(own_id, str) and own_id:
            with bot.db.session() as s:
                if s.scalar(select(Outbox).where(Outbox.provider_id == own_id)):
                    return
        if message.get('source') == 'cloud_api' and message.get('sentVia') == 'api':
            return
        echo = True
    if event == 'message.received' and message.get('direction') != 'incoming':
        return
    mid = message.get('platformMessageId') or payload.get('id')
    if not isinstance(mid, str) or not mid or len(mid) > 150:
        raise ValueError('Invalid Zernio message')
    stamp = message.get('sentAt') or payload.get('timestamp')
    when = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    if when.tzinfo is None:
        raise ValueError('Zernio timestamp must include timezone')
    metadata = payload.get('metadata') or {}
    if metadata.get('standby'):
        echo = True
    reply_id = (metadata.get('interactiveId') or metadata.get('buttonPayload')
                or metadata.get('quickReplyPayload') or metadata.get('postbackPayload'))
    text = reply_id if isinstance(reply_id, str) and reply_id else (message.get('text') or '')
    kind = 'text'
    attachments = message.get('attachments') or []
    if attachments and not text.strip():
        kind = attachments[0].get('type') if attachments[0].get('type') in {'image', 'audio'} else 'document'
    elif attachments:
        kind = 'image' if attachments[0].get('type') == 'image' else kind
    bot.ingest_message(sender=participant, name=name, zernio_id=cid, message_id=mid,
                       text=str(text)[:2000], kind=kind, timestamp=when.timestamp(), echo=echo)
