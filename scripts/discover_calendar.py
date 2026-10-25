"""Find an iCloud CalDAV collection without putting credentials in shell history."""
import base64
from getpass import getpass
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from xml.etree import ElementTree as ET

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def discover(username, password):
    authorization = 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()
    def propfind(url, properties, depth='0'):
        parsed=urlparse(url)
        if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.icloud.com') or parsed.username or parsed.port not in (None,443):
            raise ValueError('Unexpected calendar host')
        body=f'<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>{properties}</d:prop></d:propfind>'.encode()
        request=Request(url,data=body,method='PROPFIND',headers={'Authorization':authorization,'Depth':depth,'Content-Type':'application/xml; charset=utf-8'})
        with build_opener(NoRedirect).open(request,timeout=20) as result:
            return ET.fromstring(result.read())
    base='https://caldav.icloud.com/'
    principal=propfind(base,'<d:current-user-principal/>').find('.//{DAV:}current-user-principal/{DAV:}href')
    if principal is None: raise RuntimeError('iCloud returned no principal')
    principal_url=urljoin(base,principal.text)
    home=propfind(principal_url,'<c:calendar-home-set/>').find('.//{urn:ietf:params:xml:ns:caldav}calendar-home-set/{DAV:}href')
    if home is None: raise RuntimeError('iCloud returned no calendar home')
    home_url=urljoin(principal_url,home.text)
    root=propfind(home_url,'<d:displayname/><d:resourcetype/>','1')
    for response in root.findall('{DAV:}response'):
        if response.find('.//{urn:ietf:params:xml:ns:caldav}calendar') is not None:
            print(response.findtext('.//{DAV:}displayname','Kalender'))
            print('  '+urljoin(home_url,response.findtext('{DAV:}href')))

if __name__=='__main__':
    discover(input('Apple Account E-Mail: ').strip(),getpass('App-spezifisches Passwort: '))
