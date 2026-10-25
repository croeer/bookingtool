"""Durable outbox delivery. One scheduled invocation at a time."""
import base64
import json
import logging
import os
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo

import boto3
from app import get, query, table, now

log = logging.getLogger(__name__)


def escape(value):
    return str(value).replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold(line):
    parts, current = [], ''
    for char in line:
        if len((current + char).encode('utf-8')) > 75:
            parts.append(current)
            current = ' '
        current += char
    return '\r\n'.join(parts + [current])


def calendar_file(booking):
    stamp = lambda value: datetime.fromisoformat(value).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Kleine Auszeit//Booking//DE',
             'BEGIN:VEVENT', f"UID:{booking['id']}@bookingtool", 'DTSTAMP:' + stamp(booking['createdAt']),
             'DTSTART:' + stamp(booking['start']), 'DTEND:' + stamp(booking['end']),
             'SUMMARY:' + escape(f"{booking['people']} Personen · {booking['title']} · {booking['name']}"),
             'DESCRIPTION:' + escape(f"Buchung: {booking['id']}\nE-Mail: {booking['email']}\nTelefon: {booking['phone']}"),
             'STATUS:CONFIRMED', 'END:VEVENT', 'END:VCALENDAR']
    return ('\r\n'.join(fold(line) for line in lines) + '\r\n').encode()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward iCloud credentials to a redirected host.


def sync_calendar(booking, config):
    url = config['calendar_url']
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.icloud.com') or parsed.username or parsed.query or parsed.fragment or parsed.port not in (None, 443):
        raise ValueError('Expected HTTPS iCloud CalDAV calendar collection URL.')
    endpoint = url.rstrip('/') + '/' + booking['id'] + '.ics'
    auth = base64.b64encode(f"{config['username']}:{config['password']}".encode()).decode()
    cancelled = booking['status'] == 'cancelled'
    req = Request(endpoint, method='DELETE' if cancelled else 'PUT',
                  data=None if cancelled else calendar_file(booking),
                  headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'text/calendar; charset=utf-8'})
    try:
        with build_opener(NoRedirect).open(req, timeout=12) as result:
            if result.status not in (200, 201, 204):
                raise RuntimeError('Unexpected CalDAV status')
    except HTTPError as exc:
        if not (cancelled and exc.code == 404):
            raise


def send_email(booking, action):
    when = datetime.fromisoformat(booking['start']).astimezone(ZoneInfo('Europe/Berlin')).strftime('%d.%m.%Y um %H:%M Uhr')
    cancelled = action == 'cancelled'
    subject = 'Deine Buchung wurde storniert' if cancelled else 'Deine kreative Auszeit ist gebucht'
    body = (f"Hallo {booking['name']},\n\n" +
            ('deine Buchung wurde storniert.\n' if cancelled else 'wir freuen uns auf euch! Deine Buchung ist bestätigt.\n') +
            f"\n{booking['title']}\n{when}\nPersonen: {booking['people']}\nBuchungsnummer: {booking['id']}\n" +
            ('\nDie Bezahlung erfolgt vor Ort. Bei Fragen antworte einfach auf diese E-Mail.\n' if not cancelled else '') +
            '\nLiebe Grüße\nDein Kreativcafé Kleine Auszeit')
    boto3.client('sesv2').send_email(FromEmailAddress=os.environ['FROM_EMAIL'],
        ReplyToAddresses=[os.environ['FROM_EMAIL']], Destination={'ToAddresses': [booking['email']]},
        Content={'Simple': {'Subject': {'Data': subject, 'Charset': 'UTF-8'},
                            'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}}}})


def mark(job, field):
    table().update_item(Key={'pk': 'OUTBOX', 'sk': job['sk']}, UpdateExpression=f'SET {field} = :done',
                        ExpressionAttributeValues={':done': True})


def handler(event, context):
    config = None
    failures = 0
    for job in query('OUTBOX'):
        if context and context.get_remaining_time_in_millis() < 20000:
            break
        booking = get('BOOKING', job['bookingId'])
        if not booking:
            failures += 1
            continue
        for channel in ('email', 'calendar'):
            field = channel + 'Done'
            if job[field]:
                continue
            try:
                if channel == 'email':
                    # A cancellation before delivery suppresses the obsolete confirmation.
                    if job['action'] == booking['status']:
                        send_email(booking, job['action'])
                else:
                    if config is None:
                        secret = boto3.client('secretsmanager').get_secret_value(SecretId=os.environ['CALENDAR_SECRET_ARN'])
                        config = json.loads(secret['SecretString'])
                    # Always reconcile the current state, including when replaying old work.
                    sync_calendar(booking, config)
                mark(job, field)
                job[field] = True
            except Exception:
                failures += 1
                log.error('Delivery failed: channel=%s booking=%s', channel, booking['id'])
                table().update_item(Key={'pk': 'OUTBOX', 'sk': job['sk']},
                    UpdateExpression='SET lastFailureAt = :now ADD attempts :one',
                    ExpressionAttributeValues={':now': now(), ':one': 1})
        if job['emailDone'] and job['calendarDone']:
            table().delete_item(Key={'pk': 'OUTBOX', 'sk': job['sk']})
    if failures:
        raise RuntimeError(f'{failures} deliveries pending; scheduled retry will follow.')
    return {'ok': True}
