"""Generate public frontend configuration from CloudFormation outputs on stdin."""
import json
import sys
from pathlib import Path
outputs = json.load(sys.stdin)
if isinstance(outputs, dict):
    outputs = outputs['Stacks'][0]['Outputs']
values = {entry['OutputKey']: entry['OutputValue'] for entry in outputs}
config = dict(apiUrl=values['ApiUrl'], clientId=values['AdminClientId'], cognitoDomain=values['LoginUrl'])
Path('web/config.js').write_text('window.BOOKING_CONFIG = ' + json.dumps(config) + ';\n')
