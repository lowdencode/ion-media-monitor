"""Wrap a built report into a Microsoft Graph message for the ION Outbox flow.

Usage:
    python3 envelope.py out.html subject.txt "anthony@ion.video;evan@ion.video" envelope.json

The Power Automate flow "ION Monitor send" picks the file up from /ION Monitor/Outbox,
swaps __CHART__ and __LOGO__ for the base64 of the collector's chart.png and
assets/ion-logo-email.png, creates the message
in evan@ion.video's mailbox, sends it and moves the file to /ION Monitor/Sent.

If out.html has no cid:att0 image, no attachment is added and the flow's
replace() is a no-op.
"""
import json
import sys

html_path, subject_path, to, out_path = sys.argv[1:5]
html = open(html_path, encoding="utf-8").read()
subject = open(subject_path, encoding="utf-8").read().strip()
recipients = [a.strip() for a in to.replace(",", ";").split(";") if a.strip()]
if not recipients:
    sys.exit("no recipients")

message = {
    "subject": subject,
    "body": {"contentType": "HTML", "content": html},
    "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
}
attachments = []
if "cid:logo" in html:
    attachments.append({
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "ion-logo.png",
        "contentType": "image/png",
        "contentId": "logo",
        "isInline": True,
        "contentBytes": "__LOGO__",
    })
if "cid:att0" in html:
    attachments += [{
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "iov-trading.png",
        "contentType": "image/png",
        "contentId": "att0",
        "isInline": True,
        "contentBytes": "__CHART__",
    }]
if attachments:
    message["attachments"] = attachments

# The Outlook connector's HTTP action only allows /me/messages, so the flow creates
# this message as a draft and then calls /me/messages/{id}/send.
payload = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
open(out_path, "w", encoding="ascii").write(payload)
print(f"envelope: {len(payload.encode())} bytes, {len(recipients)} recipient(s), "
      f"{'with' if 'cid:att0' in html else 'no'} chart, {'with' if 'cid:logo' in html else 'no'} logo")
