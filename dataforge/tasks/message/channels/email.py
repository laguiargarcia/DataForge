import smtplib
from email.message import EmailMessage


class EmailChannel:
    name = "email"
    required_env = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM")

    def send(self, recipient: str, subject: str, body: str, env: dict[str, str]) -> None:
        for key in self.required_env:
            if key not in env:
                raise ValueError(f"missing env var {key} for channel email")

        msg = EmailMessage()
        msg["From"] = env["SMTP_FROM"]
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.set_content(body)

        host = env["SMTP_HOST"]
        port = int(env["SMTP_PORT"])

        if port == 465:
            with smtplib.SMTP_SSL(host, port) as s:
                s.login(env["SMTP_USER"], env["SMTP_PASS"])
                s.sendmail(env["SMTP_FROM"], [recipient], msg.as_string())
        else:
            with smtplib.SMTP(host, port) as s:
                s.starttls()
                s.login(env["SMTP_USER"], env["SMTP_PASS"])
                s.sendmail(env["SMTP_FROM"], [recipient], msg.as_string())
