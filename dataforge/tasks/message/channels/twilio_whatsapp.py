from twilio.rest import Client


class WhatsAppChannel:
    name = "whatsapp"
    required_env = ("TWILIO_SID", "TWILIO_TOKEN", "TWILIO_WHATSAPP_FROM")

    def send(self, recipient: str, subject: str, body: str, env: dict[str, str]) -> None:
        for k in self.required_env:
            if k not in env:
                raise ValueError(f"missing env var {k} for channel whatsapp")
        client = Client(env["TWILIO_SID"], env["TWILIO_TOKEN"])
        client.messages.create(
            from_=env["TWILIO_WHATSAPP_FROM"],
            to=f"whatsapp:{recipient}",
            body=f"{subject}\n\n{body}" if subject else body,
        )
