import logging
from urllib.parse import urlparse

import resend

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        self.settings = get_settings()
        if self.settings.email_provider == "resend":
            resend.api_key = self.settings.resend_api_key

    def send_email(
        self, to_email: str, subject: str, html_content: str
    ) -> dict[str, str] | resend.Emails.SendResponse | None:
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
                params: resend.Emails.SendParams = {
                    "from": self.settings.email_from,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_content,
                }
                resp = resend.Emails.send(params)
                # Log the provider's message id. A successful send used to be
                # completely silent, which made "no email arrived" impossible to
                # triage: there was no way to tell a send we never attempted
                # from one the provider accepted and then dropped. With the id
                # here, any delivery question can be traced straight to the
                # provider's own record for that message.
                logger.info(
                    "[email] Sent '%s' to %s via Resend (id=%s)",
                    subject,
                    to_email,
                    (resp or {}).get("id") if isinstance(resp, dict) else resp,
                )
                return resp
            except Exception as e:
                logger.warning(
                    "[email] Failed to send '%s' to %s via Resend: %s",
                    subject,
                    to_email,
                    e,
                )
                return None

        # Unknown provider — log and return gracefully
        logger.warning(
            "[email] Provider '%s' not implemented — email not sent", self.settings.email_provider
        )
        return None

    def _dashboard_link(self, path: str) -> str:
        """Build an absolute link into the configured dashboard.

        ``path`` should start with a leading slash. Falls back to just the
        path when no ``dashboard_base_url`` has been configured — the
        operator is expected to set this for any non-dev deployment so the
        link renders correctly in outbound emails.
        """
        base = (self.settings.dashboard_base_url or "").rstrip("/")
        return f"{base}{path}" if base else path


    def _safe_callback(self, callback_url: str | None, purpose: str) -> str | None:
        """Return ``callback_url`` only if it points at a trusted front-end.

        A client-supplied callback is embedded in a link we email from our own
        sender, so an unvalidated value lets an attacker have us deliver a
        trustworthy-looking message pointing at their server — and, for password
        reset, hand them a live token. Anything that fails validation is dropped
        and the caller falls back to the configured dashboard link.

        Rejects rather than sanitizes: no suffix matching (``evil-payswitch.com``
        must not pass for ``payswitch.com``), and no userinfo in the netloc
        (``https://trusted.com@evil.com`` resolves to ``evil.com``).
        """
        if not callback_url:
            return None

        try:
            parsed = urlparse(callback_url)
        except ValueError:
            parsed = None

        reason = None
        if parsed is None:
            reason = "unparseable"
        elif parsed.username or parsed.password:
            reason = "embedded credentials"
        elif parsed.scheme not in ("http", "https"):
            reason = f"scheme {parsed.scheme!r}"
        elif parsed.scheme == "http" and (parsed.hostname or "") not in ("localhost", "127.0.0.1"):
            reason = "plaintext http"
        elif (parsed.hostname or "").lower() not in self.settings.trusted_callback_hosts():
            reason = "host not allow-listed"

        if reason:
            # Logged so an operator can spot targeted abuse; see VAPT finding 2.
            logger.warning(
                "[email] Rejected %s callback_url (%s): %s", purpose, reason, callback_url
            )
            return None

        return callback_url

    def send_password_reset_email(self, to_email: str, token: str, callback_url: str | None = None):
        safe = self._safe_callback(callback_url, "password-reset")
        if safe:
            link = f"{safe}?token={token}"
        else:
            link = f"{self._dashboard_link('/reset-password')}?token={token}"

        subject = "Reset your password"
        html = f"""
        <p>You requested a password reset.</p>
        <p>Click the link below to reset your password:</p>
        <p><a href="{link}">{link}</a></p>
        <p>If you did not request this, please ignore this email.</p>
        """
        return self.send_email(to_email, subject, html)

    def send_invite_email(self, to_email: str, temp_password: str, callback_url: str | None = None):
        link = self._safe_callback(callback_url, "invite") or self._dashboard_link("/login")

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
        link = self._safe_callback(callback_url, "provision") or self._dashboard_link("/setup")

        subject = "Your PaySwitch Organization is Ready"
        html = f"""
        <p>Congratulations!</p>
        <p>Your organization has been provisioned on the PaySwitch Credit Scoring Platform.</p>
        <p>Your admin credentials are:</p>
        <ul>
            <li>Email: {to_email}</li>
            <li>Temporary Password: <strong>{temp_password}</strong></li>
        </ul>
        <p>Please login at <a href="{link}">{link}</a> to access your API keys
        and complete setup.</p>
        """
        return self.send_email(to_email, subject, html)


email_service = EmailService()
