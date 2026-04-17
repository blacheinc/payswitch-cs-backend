import logging

import resend
from app.config import get_settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        self.settings = get_settings()
        if self.settings.email_provider == "resend":
            resend.api_key = self.settings.resend_api_key

    def send_email(self, to_email: str, subject: str, html_content: str) -> dict | None:
        """Send an email using the configured provider.

        Never raises — delivery failures are logged and swallowed so that the
        calling business flow (invite, password reset, provisioning, etc.)
        completes successfully regardless of email provider availability.

        Returns the provider response dict on success, or None on failure.
        """
        if self.settings.email_provider == "console":
            print(f"--- EMAIL TO {to_email} ---")
            print(f"Subject: {subject}")
            print(html_content)
            print("---------------------------")
            return {"id": "console-mock-id"}

        if self.settings.email_provider == "resend":
            try:
                params = {
                    "from": self.settings.email_from,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_content,
                }
                return resend.Emails.send(params)
            except Exception as e:
                logger.warning(
                    "[email] Failed to send '%s' to %s via Resend: %s",
                    subject, to_email, e,
                )
                return None

        # Unknown provider — log and return gracefully
        logger.warning("[email] Provider '%s' not implemented — email not sent", self.settings.email_provider)
        return None

    def send_password_reset_email(
        self, to_email: str, token: str, callback_url: str | None = None
    ):
        if callback_url:
            link = f"{callback_url}?token={token}"
        else:
            # Fallback to a default or just the token
            link = f"https://dashboard.payswitch.com.gh/reset-password?token={token}"

        subject = "Reset your password"
        html = f"""
        <p>You requested a password reset.</p>
        <p>Click the link below to reset your password:</p>
        <p><a href="{link}">{link}</a></p>
        <p>If you did not request this, please ignore this email.</p>
        """
        return self.send_email(to_email, subject, html)

    def send_invite_email(
        self, to_email: str, temp_password: str, callback_url: str | None = None
    ):
        link = (
            callback_url if callback_url else "https://dashboard.payswitch.com.gh/login"
        )

        subject = "You have been invited to PaySwitch Credit Scoring"
        html = f"""
        <p>Welcome!</p>
        <p>You have been invited to join the PaySwitch Credit Scoring Platform.</p>
        <p>Your temporary password is: <strong>{temp_password}</strong></p>
        <p>Please login at <a href="{link}">{link}</a> and change your password immediately.</p>
        """
        return self.send_email(to_email, subject, html)

    def send_provision_email(
        self, to_email: str, temp_password: str, callback_url: str | None = None
    ):
        link = (
            callback_url if callback_url else "https://dashboard.payswitch.com.gh/setup"
        )

        subject = "Your PaySwitch Organization is Ready"
        html = f"""
        <p>Congratulations!</p>
        <p>Your organization has been provisioned on the PaySwitch Credit Scoring Platform.</p>
        <p>Your admin credentials are:</p>
        <ul>
            <li>Email: {to_email}</li>
            <li>Temporary Password: <strong>{temp_password}</strong></li>
        </ul>
        <p>Please login at <a href="{link}">{link}</a> to access your API keys and complete setup.</p>
        """
        return self.send_email(to_email, subject, html)


email_service = EmailService()
