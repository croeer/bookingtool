# Kleine Auszeit · Buchungssystem

Eigenständiges Buchungssystem für das Kreativcafé: öffentliche Reservierung mit Personenzahl, dreistündige Malslots, einmalige Events und Serien, Adminverwaltung, E-Mail-Bestätigungen und einseitige Synchronisierung nach Apple iCloud Kalender.

**Stand:** implementierte erste Version, automatisiert lokal geprüft. Noch nicht in AWS deployed und noch nicht mit einem echten iCloud-/SES-Konto integriert getestet. Vor dem Umschalten von Amelia die Abnahme unten durchführen. Es gibt keine automatische Migration bestehender Buchungen.

## Architektur

| Bestandteil | Umsetzung | Warum |
| --- | --- | --- |
| Kunden- und Adminoberfläche | Statisches HTML/CSS/JavaScript, privates S3 + CloudFront OAC | Kein Frontend-Build oder laufender Webserver nötig; passt zur bestehenden S3/CloudFront-Homepage |
| API | API Gateway HTTP API + Python 3.13 Lambda / ARM64 | Bedarfsgerechte Ausführung, keine EKS-/EC2-/RDS-Grundlast |
| Daten | DynamoDB On-Demand mit PITR | Transaktionen sichern Gruppenbuchung und Platzlimit gemeinsam |
| Adminzugang | Cognito Hosted Login, Authorization Code + PKCE, TOTP-MFA, Gruppe `admins` | Keine öffentlichen Registrierungen oder selbstgebauten Passwörter |
| E-Mail | Amazon SES v2 | Bestätigung und Stornierung aus dem eigenen verifizierten Absender |
| Kalender | CalDAV PUT/DELETE in einem dedizierten iCloud-Kalender | Personenzahl und Kontaktdaten pro Buchung; stabile UID |
| Zustellung | DynamoDB-Outbox + Lambda jede Minute | Buchung bleibt erfolgreich, auch wenn SES oder iCloud ausfallen |
| Infrastruktur | AWS SAM / CloudFormation | Kleiner gemeinsamer Stack; über Terraform/Terragrunt bei Bedarf als CloudFormation-Stack verwaltbar |

Standardregion ist **eu-central-1**. Die CSP in `template.yaml` ist bewusst auf diese Region zugeschnitten. Keine VPC, kein NAT Gateway, kein Kubernetes. Ein bestehender beruflicher Keycloak wird nicht als Abhängigkeit für das Café angenommen.

## Funktionsumfang und Regeln

- Öffentliche Terminliste mit Monats- und Angebotsfilter; genaue Restplätze werden Gästen nicht angezeigt.
- Kunde gibt Name, E-Mail, optionale Telefonnummer und die **gesamte Personenzahl einschließlich sich selbst** an.
- Standardkapazität 20; konfigurierbar von 1 bis 20. Adminbuchungen unterliegen ebenfalls diesem Limit.
- Malslots dauern genau 180 Minuten. Events dürfen 30 bis 720 Minuten dauern.
- Serien: Von/Bis, Wochentage, Uhrzeit und Ausnahmedaten. Je täglicher Uhrzeit eine Serie. Termine werden sofort als konkrete Slots veröffentlicht; keine automatische monatliche Freischaltung.
- Alle Eingabezeiten gelten in Europe/Berlin. UTC-Speicherung, DST-sichere Serien. Mehrdeutige/nicht existierende Uhrzeiten werden abgewiesen.
- Admin kann Termine/Serien anlegen, neue Buchungen schließen/wieder öffnen, Buchungen ansehen, manuell anlegen und stornieren.
- Das Schließen eines Termins storniert bestehende Buchungen **nicht**. Eine Veranstaltung absagen: schließen und die Buchungen einzeln stornieren.
- Zahlung vor Ort, kein Checkout. Der Preis ist hier nicht modelliert; für besondere Events muss die Preisinformation vor dem produktiven Einsatz ergänzt werden.
- Änderungen an Datum, Gruppengröße oder Kontaktdaten: zunächst stornieren, dann neu buchen. Keine Kundenkonten oder öffentliche Self-Service-Stornierung in dieser Version.
- Jeder Slot hat eine eigene Kapazität. Überlappende Slots/Events teilen **keinen** globalen Raum-Pool. Im Adminbereich daher keine konkurrierenden Angebote für denselben Raum veröffentlichen.
- Feiertage werden über Ausnahmedaten gepflegt, nicht automatisch aus einem Landesfeiertagskalender importiert.

## Lokal prüfen

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
node --check web/app.js
sam validate --lint --region eu-central-1
sam build
python -m http.server 8080 --directory web
```

Ohne `web/config.js` mit echten Outputs zeigt die Oberfläche einen deutlichen Einrichtungshinweis. Es gibt keine Fake-Buchungen und keine Demo-Daten im Produktionspfad. Die API-Tests verwenden Moto als DynamoDB-Emulator; sie ersetzen keinen AWS-Integrationstest.

## AWS-Einrichtung

Voraussetzungen: AWS CLI, SAM CLI, Deployment-Rolle im gewünschten **Café-AWS-Konto**. Der Stack erzeugt kostenpflichtige Ressourcen; in dieser Änderung wird nichts automatisch bereitgestellt.

1. In SES **eu-central-1** die Absenderdomain und DKIM verifizieren. SES-Produktionszugriff beantragen, damit auch unverifizierte Empfänger angeschrieben werden können. Zustellbarkeit, Bounces und Complaints vor Livebetrieb einrichten.
2. In iCloud einen **eigenen Kalender** für Buchungen anlegen. Apple Account mit 2FA und app-spezifischem Passwort verwenden. Passwort nicht in Git, Frontend-Konfiguration oder CLI-History schreiben.
3. Kalenderadresse ermitteln:

   ```bash
   python scripts/discover_calendar.py
   ```

   Die Adresse muss die konkrete CalDAV-Kalendersammlung sein (z. B. ein `https://p…-caldav.icloud.com/.../calendars/.../`-Pfad), **kein** öffentlicher `webcal://`-Link. Discovery benötigt Zugriff auf iCloud und ist hier noch nicht live verifiziert.
4. Über die AWS-Konsole einen Secrets-Manager-Secret mit diesen drei Feldern anlegen; AWS-managed Schlüssel verwenden (ein eigener KMS-Schlüssel benötigt zusätzliche `kms:Decrypt`-Berechtigung):

   ```json
   {"username":"APPLE-ACCOUNT-EMAIL","password":"APP-SPEZIFISCHES-PASSWORT","calendar_url":"HTTPS-CALDAV-COLLECTION-URL"}
   ```

5. Stack bereitstellen:

   ```bash
   sam build
   sam deploy --guided --region eu-central-1
   ```

   Parameter: `FromEmail`, `CalendarSecretArn`, weltweit eindeutiger `CognitoDomainPrefix`. Stackname z. B. `kleine-auszeit-booking`. IAM-Erstellung bestätigen. Keine Secrets in Parameterdateien speichern.
6. Öffentliche Frontend-Konfiguration generieren und Dateien hochladen:

   ```bash
   aws cloudformation describe-stacks --stack-name kleine-auszeit-booking --region eu-central-1 > /tmp/booking-outputs.json
   python scripts/configure.py < /tmp/booking-outputs.json
   aws s3 sync web/ s3://WEB_BUCKET_NAME --delete --region eu-central-1
   ```

   `WEB_BUCKET_NAME` steht im Stack-Output. `--delete` nur für den **neuen, dedizierten** Webbucket verwenden. Der Stack verwendet zunächst die ausgegebene CloudFront-Domain und HTTPS. Für eine eigene Buchungsdomain zusätzlich ACM-Zertifikat (us-east-1), CloudFront-Alias, DNS und die Cognito Callback-/Logout-URLs sowie API-CORS anpassen.
7. Teammitglied anlegen und zur Gruppe hinzufügen (Pool-ID aus Stack-Output):

   ```bash
   aws cognito-idp admin-create-user --region eu-central-1 --user-pool-id POOL_ID --username TEAM_EMAIL --user-attributes Name=email,Value=TEAM_EMAIL Name=email_verified,Value=true
   aws cognito-idp admin-add-user-to-group --region eu-central-1 --user-pool-id POOL_ID --username TEAM_EMAIL --group-name admins
   ```

   Cognito versendet die Einladung; beim ersten Login Passwort wechseln und TOTP einrichten. Jeder Mitarbeitende bekommt einen eigenen Zugang. Die Seite öffnet den Hosted Login; Token verbleiben nur in `sessionStorage`. Nach 60 Minuten erneut anmelden. Keine Refresh-Tokens werden gespeichert.
8. CloudWatch-Alarm `WorkerErrors` an ein bestehendes SNS-Alarmtopic anbinden. Ohne AlarmAction wird der Alarmstatus angezeigt, aber keine Benachrichtigung versendet.

## Apple Kalender und Zustellung

**Richtung: Buchungssystem → iCloud.** Eine Buchung ist ein Kalendereintrag, z. B. `4 Personen · Keramikmalen · Sandra`. Stornierung löscht diesen Eintrag. Änderungen oder manuelle Sperrzeiten in Apple Kalender werden nicht zurückgelesen. Die Verwaltung muss im Buchungssystem stattfinden.

Der Worker verwendet eine stabile Buchungs-UID und liest vor der Synchronisierung den aktuellen Status. Ein verspäteter Bestätigungsauftrag stellt dadurch eine bereits stornierte Buchung nicht dauerhaft wieder her. PUT/DELETE sind wiederholbar; DELETE 404 gilt als Erfolg. Authentifizierte HTTP-Redirects werden verweigert, damit Apple-Zugangsdaten nicht an einen fremden Host geraten.

Jede Buchung/Stornierung erzeugt **in derselben Datenbanktransaktion** einen Outbox-Eintrag. Der Worker verarbeitet E-Mail und Kalender unabhängig. Ein iCloud-Ausfall blockiert somit nicht die Bestätigungsmail. Fehler bleiben in der Outbox und werden jede Minute erneut versucht; `attempts` und `lastFailureAt` helfen bei der Diagnose. Erfolgreiche Aufträge werden gelöscht. Reservierte Worker-Concurrency 1 verhindert reguläre parallele Verarbeitung.

E-Mail ist **at least once**: Bei einem Abbruch genau zwischen erfolgreichem SES-Aufruf und Statusspeicherung kann eine Mail doppelt eintreffen. SES bietet hier keinen Idempotency-Key. Es wird keine Exactly-once-Zustellung behauptet. Erfolgreich gespeicherte Zustellungen werden bei Kalenderfehlern nicht erneut versendet.

## Datenmodell und Konsistenz

Eine Tabelle mit `pk`/`sk`:

| Partition | Sortierschlüssel | Inhalt |
| --- | --- | --- |
| `SLOTS` | Slot-UUID | Zeit, Titel, Kapazität, verbleibende Plätze, geschlossen |
| `BOOKING` | Buchungs-UUID | Kundendaten, Zeitkopie, Status, Admin-Auditfelder |
| `SLOT#<id>` | Buchungs-UUID | Referenz für Buchungsliste eines Slots |
| `REQUEST` | Anfrage-UUID | Dauerhafte Idempotenz mit Payload-Fingerprint |
| `OUTBOX` | Buchungs-UUID + Aktion | Unabhängige E-Mail-/Kalenderzustände |

Buchung: bedingter Platzabzug + Buchung + Slotreferenz + Idempotenz + Outbox in einer Transaktion. Stornierung: bedingter Statuswechsel + Platzfreigabe + Outbox in einer Transaktion. Alte/geschlossene Slots sind nicht buchbar. API Gateway prüft JWT, die Anwendung zusätzlich exakt die Gruppe `admins`; öffentliche Routen liefern keine Kundendaten.

API-Routen:

| Methode | Route | Zugriff |
| --- | --- | --- |
| GET | `/slots` | öffentlich |
| POST | `/bookings` | öffentlich, `Idempotency-Key` UUID erforderlich |
| GET / POST | `/admin/slots` | Admin; POST mit `Idempotency-Key` |
| PATCH | `/admin/slots/{id}` | Admin; `{ "closed": true }` |
| GET | `/admin/bookings?slotId=...` | Admin |
| POST | `/admin/bookings` | Admin; `Idempotency-Key` |
| POST | `/admin/bookings/{id}/cancel` | Admin |

Serien werden einzeln und wiederholbar geschrieben, nicht als eine große Transaktion. Bei einem Teilfehler dieselbe Anfrage mit derselben UUID wiederholen. Bereits gebuchte Plätze werden dabei niemals zurückgesetzt. Neue, identische Serien mit **anderen** Anfrage-IDs können absichtlich dieselbe Uhrzeit erzeugen; der Admin ist für die Raumplanung verantwortlich.

## Abnahme vor Livebetrieb

- 18 Personen buchen, weitere 3 ablehnen, 2 zulassen; auch mit parallelen echten HTTP-Anfragen testen.
- Wiederholung derselben Anfrage erzeugt keine zweite Buchung. Zweimal stornieren gibt Plätze nur einmal frei.
- Einmaliges Event und wiederkehrende Malslots inklusive Feiertagsausnahme anlegen.
- Kunden- und Adminbuchung bestätigen; tatsächlichen Eingang der E-Mail und iCloud-Eintrag samt Personenzahl prüfen.
- Stornierung: Mail kommt an, Apple-Eintrag verschwindet, Plätze sind wieder frei.
- Temporär falsches iCloud-Passwort: Buchung und Mail funktionieren, Alarm wird aktiv; nach Korrektur holt der Worker nach.
- Admin ohne Gruppe / abgelaufenes Token bekommt keinen Zugriff; erste Anmeldung mit MFA testen.
- Mobile Oberfläche, Datenschutzhinweis-Link und angebotsspezifische Preise prüfen. Datenschutzerklärung um eingesetzte Dienste und Datenverarbeitung ergänzen.
- Aufbewahrungs-/Löschfristen für Kunden- und Kalenderdaten festlegen und umsetzen. Derzeit keine automatische Löschung; PITR und Retain sind aktiv.
- Öffentliche Buchungen haben API-Throttling, aber noch keine CAPTCHA- oder E-Mail-Verifikation. Vor öffentlicher Bewerbung Schutz gegen automatisierte Reservierungen festlegen.
- Bestehende Amelia-Buchungen übernehmen, bevor dieselben Kapazitäten hier öffentlich angeboten werden.

## Quellen

- [DynamoDB TransactWriteItems](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItems.html)
- [Apple: app-spezifische Passwörter](https://support.apple.com/en-us/102654)
