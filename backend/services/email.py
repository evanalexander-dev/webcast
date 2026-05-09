"""
Webcast - Email Service
Sends attendance reports via SMTP.
"""
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import List, Tuple, Optional

from config import (
    SMTP_SERVER, SMTP_PORT, SMTP_USE_SSL,
    SMTP_USERNAME, SMTP_PASSWORD, ADMIN_BCC_EMAIL,
    ATTENDANCE_MULTIPLIER
)


def calculate_attendance(peak_viewers: int) -> int:
    """Calculate estimated attendance from peak viewers."""
    return round(peak_viewers * ATTENDANCE_MULTIPLIER)


def create_attendance_email(ward_name: str, stream_date: datetime,
                            peak_viewers: int, estimated_attendance: int) -> Tuple[str, str]:
    """
    Create attendance email subject and body.
    Returns (subject, html_body).
    """
    date_str = stream_date.strftime("%B %d, %Y")
    
    subject = f"{ward_name} - Stream Attendance Report - {date_str}"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                background-color: #1e3a5f;
                color: white;
                padding: 20px;
                border-radius: 8px 8px 0 0;
                text-align: center;
            }}
            .content {{
                background-color: #f8f9fa;
                padding: 30px;
                border-radius: 0 0 8px 8px;
                border: 1px solid #e9ecef;
                border-top: none;
            }}
            .stat-box {{
                background-color: white;
                border-radius: 8px;
                padding: 20px;
                margin: 15px 0;
                text-align: center;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            .stat-number {{
                font-size: 48px;
                font-weight: bold;
                color: #1e3a5f;
            }}
            .stat-label {{
                color: #666;
                font-size: 14px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            .note {{
                font-size: 12px;
                color: #888;
                margin-top: 20px;
                padding-top: 15px;
                border-top: 1px solid #ddd;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1 style="margin: 0;">{ward_name}</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Stream Attendance Report</p>
        </div>
        <div class="content">
            <p>Here is the attendance summary for the meeting streamed on <strong>{date_str}</strong>:</p>
            
            <div class="stat-box">
                <div class="stat-label">Total Views</div>
                <div class="stat-number">{peak_viewers}</div>
            </div>
            
            <div class="stat-box">
                <div class="stat-label">Estimated Attendance</div>
                <div class="stat-number">{estimated_attendance}</div>
            </div>
            
            <p class="note">
                The estimated attendance is calculated by multiplying total YouTube views 
                by {ATTENDANCE_MULTIPLIER} to account for households with multiple viewers. 
                This is an approximation based on typical viewing patterns.
            </p>
        </div>
    </body>
    </html>
    """
    
    return subject, html_body


def send_email(to_addresses: List[str], subject: str, html_body: str,
               bcc_addresses: List[str] = None) -> Tuple[bool, str]:
    """
    Send an email via SMTP.
    Returns (success, message).
    """
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        return False, "SMTP credentials not configured"
    
    if not to_addresses:
        return False, "No recipient addresses provided"
    
    # Create message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USERNAME
    msg["To"] = ", ".join(to_addresses)
    
    # Add BCC (admin)
    all_recipients = to_addresses.copy()
    if bcc_addresses:
        all_recipients.extend(bcc_addresses)
    elif ADMIN_BCC_EMAIL:
        all_recipients.append(ADMIN_BCC_EMAIL)
    
    # Plain text version (fallback)
    plain_text = f"""
{subject}

This email contains HTML content. Please view it in an HTML-capable email client.
    """
    
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    
    try:
        if SMTP_USE_SSL:
            # SSL connection (port 465)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, all_recipients, msg.as_string())
        else:
            # STARTTLS connection (port 587)
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, all_recipients, msg.as_string())
        
        return True, f"Email sent to {len(to_addresses)} recipient(s)"
        
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed"
    except smtplib.SMTPRecipientsRefused:
        return False, "All recipients were refused"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {str(e)}"
    except Exception as e:
        return False, f"Error sending email: {str(e)}"


def send_attendance_report(ward_name: str, stream_date: datetime,
                           peak_viewers: int, to_addresses: List[str]) -> Tuple[bool, str]:
    """
    Send attendance report email for a ward.
    Convenience function that creates and sends the email.
    """
    estimated_attendance = calculate_attendance(peak_viewers)
    subject, html_body = create_attendance_email(
        ward_name, stream_date, peak_viewers, estimated_attendance
    )
    return send_email(to_addresses, subject, html_body)


def send_test_email(to_address: str) -> Tuple[bool, str]:
    """Send a test email to verify SMTP configuration."""
    subject = "Webcast - Test Email"
    html_body = """
    <!DOCTYPE html>
    <html>
    <body style="font-family: sans-serif; padding: 20px;">
        <h2>Test Email</h2>
        <p>If you're reading this, your SMTP configuration is working correctly!</p>
        <p style="color: #666; font-size: 12px;">
            Sent from Webcast
        </p>
    </body>
    </html>
    """
    return send_email([to_address], subject, html_body)


if __name__ == "__main__":
    # Test email creation
    subject, body = create_attendance_email(
        "Peak View Ward",
        datetime.now(),
        peak_viewers=15,
        estimated_attendance=41
    )
    print(f"Subject: {subject}")
    print("\nHTML Body preview:")
    print(body[:500] + "...")
    
    # Check if credentials are configured
    if SMTP_USERNAME and SMTP_PASSWORD:
        print("\n\nSMTP credentials are configured")
        print(f"Server: {SMTP_SERVER}:{SMTP_PORT}")
        print(f"SSL: {SMTP_USE_SSL}")
        print(f"Username: {SMTP_USERNAME}")
    else:
        print("\n\nSMTP credentials NOT configured")
        print("Set SMTP_USERNAME and SMTP_PASSWORD environment variables")
