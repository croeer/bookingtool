import uuid
from unittest.mock import patch
import app
import worker


def booking():
    return dict(id='abc',name='Änne, Test',people=3,title='Malen; Test',email='a@example.com',phone='–',status='confirmed',createdAt='2099-01-01T00:00:00+00:00',start='2099-01-02T12:00:00+00:00',end='2099-01-02T15:00:00+00:00')


def test_calendar_contains_party_and_safe_escaping():
    content=worker.calendar_file(booking()).decode()
    assert '3 Personen' in content
    assert 'Malen\\; Test' in content and 'Änne\\, Test' in content
    assert 'DTSTART:20990102T120000Z' in content
    assert all(len(line.encode())<=75 for line in content.split('\r\n'))


def test_folding_preserves_multibyte_text():
    value='SUMMARY:'+'ä'*100
    folded=worker.fold(value)
    assert folded.replace('\r\n ','')==value
    assert all(len(x.encode())<=75 for x in folded.split('\r\n'))


def test_worker_skips_obsolete_confirmation_and_reconciles_cancel(db):
    result=app.book(dict(slotId='slot',name='Test',email='a@example.com',people=3),str(uuid.uuid4()))
    app.cancel(result['id'],'admin')
    with patch.object(worker,'send_email') as send, patch.object(worker,'sync_calendar') as sync, patch.object(worker.boto3,'client') as client:
        client.return_value.get_secret_value.return_value={'SecretString':'{}'}
        worker.handler({},None)
    assert send.call_count==1 and send.call_args.args[1]=='cancelled'
    assert all(call.args[0]['status']=='cancelled' for call in sync.call_args_list)
    assert app.query('OUTBOX')==[]


def test_email_success_is_not_repeated_after_calendar_failure(db):
    app.book(dict(slotId='slot',name='Test',email='a@example.com',people=3),str(uuid.uuid4()))
    import pytest
    with patch.object(worker,'send_email') as send, patch.object(worker,'sync_calendar',side_effect=RuntimeError()), patch.object(worker.boto3,'client') as client:
        client.return_value.get_secret_value.return_value={'SecretString':'{}'}
        with pytest.raises(RuntimeError): worker.handler({},None)
        assert send.call_count==1
    assert app.query('OUTBOX')[0]['emailDone'] is True
    with patch.object(worker,'send_email') as send, patch.object(worker,'sync_calendar'), patch.object(worker.boto3,'client') as client:
        client.return_value.get_secret_value.return_value={'SecretString':'{}'}
        worker.handler({},None)
        send.assert_not_called()
    assert app.query('OUTBOX')==[]
