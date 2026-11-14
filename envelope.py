"""Wrap a built report into a Microsoft Graph sendMail request for the ION Outbox flow.

Usage:
    python3 envelope.py out.html subject.txt "anthony@ion.video;evan@ion.video" envelope.json

The Power Automate flow "ION Monitor send" picks the file up from /ION Monitor/Outbox,
swaps __CHART__ for the base64 of the collector's chart.png, posts it to
/me/sendMail from evan@ion.video and moves the file to /ION Monitor/Sent.

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
if "cid:att0" in html:
    message["attachments"] = [{
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "iov-trading.png",
        "contentType": "image/png",
        "contentId": "att0",
        "isInline": True,
        "contentBytes": "__CHART__",
    }]

payload = json.dumps({"message": message, "saveToSentItems": True}, ensure_ascii=True, separators=(",", ":"))
open(out_path, "w", encoding="ascii").write(payload)
print(f"envelope: {len(payload.encode())} bytes, {len(recipients)} recipient(s), "
      f"{'with' if 'attachments' in message else 'no'} chart")
