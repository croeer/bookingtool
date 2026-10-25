"""HTTP API. All booking state changes use DynamoDB transactions."""
import base64
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from domain import customer, slots, text

log = logging.getLogger(__name__)
TABLE = os.environ.get('TABLE_NAME', 'bookingtool')
serializer = TypeSerializer()


def table():
    return boto3.resource('dynamodb').Table(TABLE)


def encode(item):
    return {k: serializer.serialize(v) for k, v in item.items()}


def get(pk, sk):
    return table().get_item(Key={'pk': pk, 'sk': sk}, ConsistentRead=True).get('Item')


def query(pk):
    args = dict(KeyConditionExpression='pk = :pk', ExpressionAttributeValues={':pk': pk}, ConsistentRead=True)
    result = []
    while True:
        page = table().query(**args)
        result.extend(page['Items'])
        if 'LastEvaluatedKey' not in page:
            return result
        args['ExclusiveStartKey'] = page['LastEvaluatedKey']


def put(item):
    return {'Put': {'TableName': TABLE, 'Item': encode(item), 'ConditionExpression': 'attribute_not_exists(pk)'}}


def update(pk, sk, expression, condition, values, names=None):
    args = dict(TableName=TABLE, Key=encode(dict(pk=pk, sk=sk)), UpdateExpression=expression,
                ConditionExpression=condition, ExpressionAttributeValues=encode(values))
    if names:
        args['ExpressionAttributeNames'] = names
    return {'Update': args}


def transact(items):
    boto3.client('dynamodb').transact_write_items(TransactItems=items)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def task(booking, action):
    return dict(pk='OUTBOX', sk=f"{booking['id']}#{action}", bookingId=booking['id'], action=action,
                emailDone=False, calendarDone=False, createdAt=now())


def book(data, request_id, actor='customer'):
    request_id = str(uuid.UUID(request_id))
    details = customer(data)
    slot_id = text(data.get('slotId'), 'Termin', 100)
    fingerprint = hashlib.sha256(json.dumps({'slotId': slot_id, **details}, sort_keys=True).encode()).hexdigest()
    previous = get('REQUEST', request_id)
    if previous:
        if previous['fingerprint'] != fingerprint:
            raise ValueError('Diese Anfrage-ID wurde bereits für andere Daten verwendet.')
        return {'id': previous['bookingId'], 'status': get('BOOKING', previous['bookingId'])['status']}
    slot = get('SLOTS', slot_id)
    if not slot or slot['start'] <= now() or slot['closed']:
        raise ValueError('Dieser Termin ist nicht buchbar.')
    booking_id = str(uuid.uuid4())
    booking = dict(pk='BOOKING', sk=booking_id, id=booking_id, slotId=slot_id, **details,
                   title=slot['title'], start=slot['start'], end=slot['end'], status='confirmed', createdAt=now(), actor=actor)
    try:
        transact([
            update('SLOTS', slot_id, 'SET remaining = remaining - :n',
                   'remaining >= :n AND closed = :false AND #start > :now',
                   {':n': details['people'], ':false': False, ':now': now()}, {'#start': 'start'}),
            put(booking), put(dict(pk=f'SLOT#{slot_id}', sk=booking_id, bookingId=booking_id)),
            put(dict(pk='REQUEST', sk=request_id, fingerprint=fingerprint, bookingId=booking_id)),
            put(task(booking, 'confirmed')),
        ])
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'TransactionCanceledException':
            raise
        previous = get('REQUEST', request_id)
        if previous and previous['fingerprint'] == fingerprint:
            return {'id': previous['bookingId'], 'status': get('BOOKING', previous['bookingId'])['status']}
        raise ValueError('Nicht genügend Plätze oder parallele Buchung. Bitte erneut versuchen.') from exc
    return {'id': booking_id, 'status': 'confirmed'}


def cancel(booking_id, actor):
    booking = get('BOOKING', booking_id)
    if not booking:
        raise ValueError('Buchung nicht gefunden.')
    if booking['status'] == 'cancelled':
        return {'status': 'cancelled'}
    try:
        transact([
            update('BOOKING', booking_id, 'SET #s = :cancelled, cancelledAt = :now, cancelledBy = :actor',
                   '#s = :confirmed', {':cancelled': 'cancelled', ':confirmed': 'confirmed', ':now': now(), ':actor': actor}, {'#s': 'status'}),
            update('SLOTS', booking['slotId'], 'SET remaining = remaining + :n',
                   'attribute_exists(pk)', {':n': booking['people']}),
            put(task(booking, 'cancelled')),
        ])
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'TransactionCanceledException' and get('BOOKING', booking_id)['status'] == 'cancelled':
            return {'status': 'cancelled'}
        raise
    return {'status': 'cancelled'}


def create_slots(data, request_id):
    # Deterministic IDs make retrying a partially completed series safe.
    namespace = uuid.UUID(request_id)
    entries = slots(data)
    ids = []
    for entry in entries:
        slot_id = str(uuid.uuid5(namespace, entry['start']))
        item = dict(pk='SLOTS', sk=slot_id, id=slot_id, remaining=entry['capacity'], closed=False, **entry)
        try:
            table().put_item(Item=item, ConditionExpression='attribute_not_exists(pk)')
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
                raise
            existing = get('SLOTS', slot_id)
            if any(existing[k] != v for k, v in entry.items()):
                raise ValueError('Anfrage-ID bereits für eine andere Serie verwendet.') from exc
        ids.append(slot_id)
    return {'ids': ids}


def response(status, data):
    return {'statusCode': status, 'headers': {'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
            'body': json.dumps(data, default=lambda x: int(x) if isinstance(x, Decimal) else str(x))}


def handler(event, context):
    try:
        method = event['requestContext']['http']['method']
        path = event['rawPath'].rstrip('/')
        admin = path.startswith('/admin')
        claims = event.get('requestContext', {}).get('authorizer', {}).get('jwt', {}).get('claims', {})
        if admin and 'admins' not in str(claims.get('cognito:groups', '')).replace('[', '').replace(']', '').replace('"', '').replace("'", '').replace(' ', ',').split(','):
            return response(403, {'error': 'Admin-Zugriff erforderlich.'})
        raw = event.get('body') or '{}'
        if event.get('isBase64Encoded'):
            raw = base64.b64decode(raw).decode()
        if len(raw) > 16000:
            return response(413, {'error': 'Anfrage zu groß.'})
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('JSON-Objekt erwartet.')
        request_id = event.get('headers', {}).get('idempotency-key', '')
        actor = claims.get('sub', 'customer')
        if method == 'GET' and path in ('/slots', '/admin/slots'):
            result = sorted(query('SLOTS'), key=lambda s: s['start'])
            if not admin:
                result = [{k: s[k] for k in ('id', 'title', 'kind', 'start', 'end', 'capacity')} | {'available': s['remaining'] > 0}
                          for s in result if not s['closed'] and s['start'] > now()]
            return response(200, result)
        if method == 'POST' and path in ('/bookings', '/admin/bookings'):
            return response(201, book(data, request_id, actor))
        if method == 'POST' and path == '/admin/slots':
            return response(201, create_slots(data, request_id))
        if method == 'PATCH' and path.startswith('/admin/slots/'):
            if type(data.get('closed')) is not bool:
                raise ValueError('closed muss true oder false sein.')
            table().update_item(Key=dict(pk='SLOTS', sk=path.split('/')[-1]), UpdateExpression='SET closed = :c',
                                ConditionExpression='attribute_exists(pk)', ExpressionAttributeValues={':c': data['closed']})
            return response(200, {'ok': True})
        if method == 'GET' and path == '/admin/bookings':
            slot_id = (event.get('queryStringParameters') or {}).get('slotId')
            if not slot_id:
                raise ValueError('Bitte einen Termin auswählen.')
            return response(200, [get('BOOKING', b['bookingId']) for b in query(f'SLOT#{slot_id}')])
        if method == 'POST' and path.startswith('/admin/bookings/') and path.endswith('/cancel'):
            return response(200, cancel(path.split('/')[-2], actor))
        return response(404, {'error': 'Nicht gefunden.'})
    except (ValueError, KeyError, TypeError) as exc:
        return response(400, {'error': str(exc) or 'Ungültige Eingabe.'})
    except Exception:
        # Avoid logging customer data or request bodies.
        log.error('API operation failed', extra={'request_id': getattr(context, 'aws_request_id', '')})
        return response(500, {'error': 'Technischer Fehler. Bitte mit derselben Anfrage erneut versuchen.'})
