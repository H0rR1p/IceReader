from __future__ import annotations

import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

from .config import CloudConfig


class Mailer:
    def __init__(self, config: CloudConfig):
        self.config = config

    def send(self, recipient: str, subject: str, text: str) -> str:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.smtp_from or "冰读 <noreply@localhost>"
        message["To"] = recipient
        message.set_content(text)
        if not self.config.smtp_host:
            outbox = self.config.database_path.parent / "mail-outbox"
            outbox.mkdir(parents=True, exist_ok=True)
            safe_recipient = recipient.replace("@", "_at_").replace("/", "_").replace("\\", "_")
            target = outbox / f"{int(time.time() * 1000)}-{safe_recipient}.eml"
            target.write_bytes(message.as_bytes())
            return "outbox"
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=20) as client:
            client.ehlo()
            if self.config.smtp_starttls:
                client.starttls()
                client.ehlo()
            if self.config.smtp_username:
                client.login(self.config.smtp_username, self.config.smtp_password)
            client.send_message(message)
        return "smtp"

