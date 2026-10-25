"""Validation and timezone handling, independent of AWS."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import re

BERLIN = ZoneInfo('Europe/Berlin')


def text(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} fehlt oder ist zu lang.')
    if any(ord(c) < 32 for c in value):
        raise ValueError(f'{label} enthält ungültige Zeichen.')
    return value.strip()


def integer(value, label, low=1, high=20):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label}: erlaubt sind {low} bis {high}.')
    return value


def customer(data):
    email = text(data.get('email'), 'E-Mail', 254)
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ValueError('Bitte eine gültige E-Mail-Adresse eingeben.')
    return dict(name=text(data.get('name'), 'Name', 100), email=email,
                people=integer(data.get('people'), 'Personenzahl'),
                phone=text(data.get('phone') or '–', 'Telefon', 50))


def utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds')


def slots(data):
    title = text(data.get('title'), 'Titel', 120)
    kind = data.get('kind', 'painting')
    if kind not in ('painting', 'event'):
        raise ValueError('Unbekannte Terminart.')
    capacity = integer(data.get('capacity', 20), 'Kapazität')
    start_date = date.fromisoformat(data['from'])
    end_date = date.fromisoformat(data.get('to') or data['from'])
    if not 0 <= (end_date - start_date).days <= 366:
        raise ValueError('Eine Serie darf maximal 366 Tage umfassen.')
    weekdays = data.get('weekdays', list(range(7)))
    if not isinstance(weekdays, list) or not weekdays or any(type(x) is not int or x not in range(7) for x in weekdays):
        raise ValueError('Bitte Wochentage auswählen (Montag = 0).')
    excluded = set(data.get('excludedDates', []))
    for day in excluded:
        date.fromisoformat(day)
    start_time = time.fromisoformat(data['time'])
    if start_time.tzinfo or start_time.second or start_time.microsecond:
        raise ValueError('Bitte eine lokale Uhrzeit mit Stunden und Minuten angeben.')
    duration = 180 if kind == 'painting' else integer(data.get('duration', 180), 'Dauer', 30, 720)
    result = []
    for offset in range((end_date - start_date).days + 1):
        day = start_date + timedelta(days=offset)
        if day.weekday() not in weekdays or day.isoformat() in excluded:
            continue
        start = datetime.combine(day, start_time, BERLIN)
        # Reject nonexistent and ambiguous wall times rather than silently moving an event.
        if start.astimezone(timezone.utc).astimezone(BERLIN).replace(tzinfo=None) != start.replace(tzinfo=None) or start.utcoffset() != start.replace(fold=1).utcoffset():
            raise ValueError('Diese Uhrzeit ist wegen der Zeitumstellung nicht eindeutig.')
        end = start.astimezone(timezone.utc) + timedelta(minutes=duration)
        if start <= datetime.now(timezone.utc):
            raise ValueError('Termine müssen in der Zukunft liegen.')
        result.append(dict(title=title, kind=kind, capacity=capacity, start=utc(start), end=utc(end)))
    if not result:
        raise ValueError('Keine passenden Termine im Zeitraum.')
    return result
