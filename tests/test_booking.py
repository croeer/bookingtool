import json
import uuid
import pytest
import app
from domain import slots


def data(people=4):
    return dict(slotId='slot',name='Sandra',email='sandra@example.com',people=people)


def test_booking_consumes_people_and_creates_outbox(db):
    result=app.book(data(),str(uuid.uuid4()))
    assert app.get('SLOTS','slot')['remaining']==16
    assert app.get('BOOKING',result['id'])['people']==4
    assert len(app.query('OUTBOX'))==1


def test_capacity_is_atomic_and_admin_has_no_override(db):
    app.book(data(18),str(uuid.uuid4()),'admin')
    with pytest.raises(ValueError): app.book(data(3),str(uuid.uuid4()))
    assert app.get('SLOTS','slot')['remaining']==2
    assert len(app.query('BOOKING'))==1
    assert len(app.query('OUTBOX'))==1


def test_retry_does_not_double_book(db):
    key=str(uuid.uuid4()); first=app.book(data(),key)
    assert app.book(data(),key)==first
    assert app.get('SLOTS','slot')['remaining']==16
    with pytest.raises(ValueError): app.book(data(5),key)


def test_cancel_once_releases_capacity_once(db):
    result=app.book(data(),str(uuid.uuid4()))
    app.cancel(result['id'],'admin');app.cancel(result['id'],'admin')
    assert app.get('SLOTS','slot')['remaining']==20
    assert len(app.query('OUTBOX'))==2
    assert app.get('BOOKING',result['id'])['status']=='cancelled'


@pytest.mark.parametrize('people',[0,-1,21,1.2,True,'2'])
def test_invalid_party_size(db,people):
    with pytest.raises(ValueError):app.book(data(people),str(uuid.uuid4()))
    assert app.get('SLOTS','slot')['remaining']==20


def test_closed_slot_rejected(db):
    db.update_item(Key=dict(pk='SLOTS',sk='slot'),UpdateExpression='SET closed = :c',ExpressionAttributeValues={':c':True})
    with pytest.raises(ValueError): app.book(data(),str(uuid.uuid4()))


def test_public_api_hides_personal_data_and_free_counts(db):
    app.book(data(),str(uuid.uuid4()))
    result=app.handler(dict(rawPath='/slots',requestContext={'http':{'method':'GET'}}),None)
    item=json.loads(result['body'])[0]
    assert item['available'] is True
    assert 'remaining' not in item and 'email' not in item


@pytest.mark.parametrize('groups',['','notadmins','superadmins','["users"]'])
def test_admin_requires_exact_group(db,groups):
    event=dict(rawPath='/admin/slots',requestContext={'http':{'method':'GET'},'authorizer':{'jwt':{'claims':{'cognito:groups':groups}}}})
    assert app.handler(event,None)['statusCode']==403


def test_admin_access(db):
    event=dict(rawPath='/admin/slots',requestContext={'http':{'method':'GET'},'authorizer':{'jwt':{'claims':{'cognito:groups':'[admins]'}}}})
    assert app.handler(event,None)['statusCode']==200


def test_berlin_recurrence_keeps_local_hour_across_dst():
    entries=slots(dict(title='Malen',kind='painting',**{'from':'2099-03-27','to':'2099-03-30','time':'14:00'}))
    assert len(entries)==4
    assert entries[0]['start'][11:16]=='13:00'
    assert entries[-1]['start'][11:16]=='12:00'
    from datetime import datetime
    assert all((datetime.fromisoformat(e['end'])-datetime.fromisoformat(e['start'])).total_seconds()==10800 for e in entries)


def test_exceptions_and_weekdays():
    entries=slots(dict(title='Malen',**{'from':'2099-05-01','to':'2099-05-10','time':'14:00','weekdays':[0,1,2,3,4,5,6],'excludedDates':['2099-05-02']}))
    assert len(entries)==9
    assert not any(e['start'].startswith('2099-05-02') for e in entries)


def test_series_retry_preserves_booked_capacity(db):
    payload=dict(title='Malen',**{'from':'2099-06-01','time':'14:00'})
    key=str(uuid.uuid4());first=app.create_slots(payload,key)
    booking=data();booking['slotId']=first['ids'][0]
    app.book(booking,str(uuid.uuid4()))
    assert app.create_slots(payload,key)==first
    assert app.get('SLOTS',first['ids'][0])['remaining']==16
