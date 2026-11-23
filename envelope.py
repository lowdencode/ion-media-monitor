"""Wrap a built report into a Microsoft Graph message for the ION Outbox flow.

Usage:
    python3 envelope.py out.html subject.txt "evan@ion.video" envelope.json --bcc "anthony@ion.video;brent@ion.video"

The third argument is the visible To line. Everyone passed with --bcc is a blind copy, so
recipients never see each other's names or addresses.

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
extra = sys.argv[5:]
bcc_raw = extra[extra.index("--bcc") + 1] if "--bcc" in extra else ""


def split(raw):
    seen, out = set(), []
    for a in raw.replace(",", ";").split(";"):
        a = a.strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


html = open(html_path, encoding="utf-8").read()
subject = open(subject_path, encoding="utf-8").read().strip()
recipients = split(to)
bcc = [a for a in split(bcc_raw) if a.lower() not in {r.lower() for r in recipients}]
if not recipients:
    sys.exit("no recipients")
if any("@" not in a for a in recipients + bcc):
    sys.exit("bad address")

message = {
    "subject": subject,
    "body": {"contentType": "HTML", "content": html},
    "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
}
if bcc:
    message["bccRecipients"] = [{"emailAddress": {"address": a}} for a in bcc]
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
print(f"envelope: {len(payload.encode())} bytes, {len(recipients)} to, {len(bcc)} bcc, "
      f"{'with' if 'cid:att0' in html else 'no'} chart, {'with' if 'cid:logo' in html else 'no'} logo")
