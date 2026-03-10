import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
import asyncio
import time
from app.core.config import settings

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_email(to_email: str, subject: str, html_body: str):
        if not settings.SMTP_SERVER or not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
            logger.warning("SMTP settings not configured. Skipping email send.")
            return

        msg = MIMEMultipart()
        msg['From'] = settings.FROM_EMAIL or settings.SMTP_USERNAME
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(html_body, 'html'))

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                # Use a timeout to prevent hanging. Dropped source_address=('0.0.0.0', 0) to avoid IPv4/IPv6 gaierror
                with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=15) as server:
                    server.starttls()
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                    server.send_message(msg)
                logger.info(f"Email sent successfully to {to_email}")
                return
            except Exception as e:
                logger.error(f"Attempt {attempt}/{max_retries}: Failed to send email to {to_email}: {str(e)}")
                if attempt == max_retries:
                    raise e
                time.sleep(2)

    @staticmethod
    def send_email_background(to_email: str, subject: str, html_body: str):
        asyncio.create_task(asyncio.to_thread(
            EmailService.send_email,
            to_email=to_email,
            subject=subject,
            html_body=html_body
        ))
