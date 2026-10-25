import os
import sys
from pathlib import Path
os.environ.update(AWS_DEFAULT_REGION='eu-central-1', AWS_ACCESS_KEY_ID='testing', AWS_SECRET_ACCESS_KEY='testing', TABLE_NAME='bookingtool', FROM_EMAIL='cafe@example.com', CALENDAR_SECRET_ARN='calendar')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import boto3
import pytest
from moto import mock_aws

@pytest.fixture
def db():
    with mock_aws():
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.create_table(TableName='bookingtool', BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[{'AttributeName':k,'AttributeType':'S'} for k in ('pk','sk')],
            KeySchema=[{'AttributeName':'pk','KeyType':'HASH'},{'AttributeName':'sk','KeyType':'RANGE'}])
        table.put_item(Item=dict(pk='SLOTS',sk='slot',id='slot',title='Malen',kind='painting',capacity=20,remaining=20,closed=False,start='2099-05-20T12:00:00+00:00',end='2099-05-20T15:00:00+00:00'))
        yield table
