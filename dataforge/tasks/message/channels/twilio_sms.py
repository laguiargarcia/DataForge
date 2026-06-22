from twilio.rest import Client


class SmsChannel:
    name = "sms"
    required_env = ("TWILIO_SID", "TWILIO_TOKEN", "TWILIO_SMS_FROM")

    def send(self, recipient: str, subject: str, body: str, env: dict[str, str]) -> None:
        for k in self.required_env:
            if k not in env:
                raise ValueError(f"missing env var {k} for channel sms")
        client = Client(env["TWILIO_SID"], env["TWILIO_TOKEN"])
        client.messages.create(
            from_=env["TWILIO_SMS_FROM"],
            to=recipient,
            body=f"{subject}\n\n{body}" if subject else body,
        )
