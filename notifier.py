"""
Email notifier for new apartment listings.

Sends a digest email whenever new listings pass all hard filters.
Uses aiosmtplib for async SMTP so it fits into the async event loop.
"""
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

import aiosmtplib

from config import Config
from models import Listing

logger = logging.getLogger(__name__)


def _render_listing_html(listing: Listing, rank: int) -> str:
    """Render a single listing as an HTML table row card."""
    price_str = f"${listing.price_min:,}" if listing.price_min else "N/A"
    if listing.price_max and listing.price_max != listing.price_min:
        price_str += f" – ${listing.price_max:,}"

    sqft_str = f"{listing.sqft_min:,}" if listing.sqft_min else "?"
    if listing.sqft_max and listing.sqft_max != listing.sqft_min:
        sqft_str += f" – {listing.sqft_max:,}"

    beds = listing.bedrooms or "?"
    baths = listing.bathrooms or "?"
    metro = listing.nearest_metro or "Unknown"
    dist = f"{listing.metro_distance_miles:.2f} mi" if listing.metro_distance_miles else "?"
    walk = "Yes" if listing.is_walkable else "No"
    gym = "Yes" if listing.has_gym else "Unknown"
    light_bar = "★" * listing.natural_light_score + "☆" * (5 - listing.natural_light_score)
    photo_html = ""
    if listing.photos:
        photo_html = f'<img src="{listing.photos[0]}" style="max-width:100%;border-radius:6px;margin-bottom:8px;" />'

    source_label = "Apartments.com" if listing.source == "apartments_com" else "Zillow"

    return f"""
<div style="font-family:sans-serif;border:1px solid #e2e8f0;border-radius:10px;
            padding:20px;margin-bottom:24px;background:#ffffff;max-width:680px;">
  <div style="font-size:11px;color:#718096;margin-bottom:4px;">
    #{rank} &nbsp;•&nbsp; {source_label} &nbsp;•&nbsp;
    <span style="color:{'#38a169' if listing.is_walkable else '#e53e3e'};">
      {'✓ Metro walkable' if listing.is_walkable else '✗ Not metro walkable'}
    </span>
  </div>
  {photo_html}
  <h3 style="margin:0 0 4px;font-size:17px;">
    <a href="{listing.url}" style="color:#2b6cb0;text-decoration:none;">
      {listing.title or listing.address or 'View Listing'}
    </a>
  </h3>
  <div style="color:#4a5568;font-size:13px;margin-bottom:12px;">
    {listing.address or ''}
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <tr>
      <td style="padding:4px 0;width:50%;"><b>Rent:</b> {price_str}/mo</td>
      <td style="padding:4px 0;"><b>Beds / Baths:</b> {beds} bd / {baths} ba</td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>Sq Ft:</b> {sqft_str}</td>
      <td style="padding:4px 0;"><b>Nearest Metro:</b> {metro} ({dist})</td>
    </tr>
    <tr>
      <td style="padding:4px 0;"><b>Metro walkable:</b> {walk}</td>
      <td style="padding:4px 0;"><b>Gym / Fitness:</b> {gym}</td>
    </tr>
    <tr>
      <td style="padding:4px 0;" colspan="2">
        <b>Natural light score:</b> {light_bar} ({listing.natural_light_score}/5)
      </td>
    </tr>
  </table>
  <div style="margin-top:12px;">
    <a href="{listing.url}"
       style="background:#2b6cb0;color:#fff;padding:8px 16px;border-radius:6px;
              text-decoration:none;font-size:13px;font-weight:600;">
      View Listing →
    </a>
  </div>
</div>
"""


def _build_email(listings: List[Listing]) -> MIMEMultipart:
    """Construct the MIME email for a batch of new listings."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"[AptAlert] {len(listings)} new listing{'s' if len(listings) != 1 else ''} "
        f"in Rosslyn / Clarendon / Ballston"
    )
    msg["From"] = Config.SMTP_USER
    msg["To"] = Config.NOTIFY_EMAIL

    # ── Plain text ────────────────────────────────────────────────────────────
    lines = [f"New apartment listings in Arlington, VA ({len(listings)} found)\n"]
    for i, l in enumerate(listings, 1):
        price = f"${l.price_min:,}/mo" if l.price_min else "N/A"
        lines.append(
            f"{i}. {l.title or l.address}\n"
            f"   {price} | {l.bedrooms}bd/{l.bathrooms}ba | {l.sqft_min or '?'} sqft\n"
            f"   Metro: {l.nearest_metro or '?'} ({f'{l.metro_distance_miles:.2f}' if l.metro_distance_miles else '?'} mi "
            f"| {'walkable' if l.is_walkable else 'not walkable'})\n"
            f"   Gym: {'yes' if l.has_gym else 'unknown'} | "
            f"Natural light: {l.natural_light_score}/5\n"
            f"   {l.url}\n"
        )
    plain_body = "\n".join(lines)

    # ── HTML ─────────────────────────────────────────────────────────────────
    listing_cards = "".join(
        _render_listing_html(l, i) for i, l in enumerate(listings, 1)
    )
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="background:#f7fafc;padding:24px;">
  <div style="max-width:700px;margin:0 auto;">
    <h2 style="font-family:sans-serif;color:#1a202c;margin-bottom:4px;">
      New Apartments in Rosslyn / Clarendon / Ballston
    </h2>
    <p style="font-family:sans-serif;color:#718096;font-size:13px;margin-top:0;">
      {len(listings)} new listing{'s' if len(listings) != 1 else ''} found ·
      2 bed / 2 bath · ≤$3,500/mo · 1,000+ sqft
    </p>
    {listing_cards}
    <p style="font-family:sans-serif;color:#a0aec0;font-size:11px;margin-top:24px;">
      This alert was generated automatically.
      Always verify details directly with the listing source.
    </p>
  </div>
</body>
</html>
"""

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


async def send_notification(listings: List[Listing]) -> bool:
    """
    Send a notification email for the given new listings.
    Returns True if sent successfully, False otherwise.
    Silently skips if SMTP is not configured.
    """
    if not listings:
        return False

    if not Config.SMTP_USER or not Config.SMTP_PASSWORD or not Config.NOTIFY_EMAIL:
        logger.info("Email not configured — skipping notification for %d listings", len(listings))
        return False

    msg = _build_email(listings)

    try:
        await aiosmtplib.send(
            msg,
            sender=Config.SMTP_USER,
            recipients=[Config.NOTIFY_EMAIL],
            hostname=Config.SMTP_HOST,
            port=Config.SMTP_PORT,
            username=Config.SMTP_USER,
            password=Config.SMTP_PASSWORD,
            start_tls=True,
        )
        logger.info(
            "Notification sent to %s for %d listings", Config.NOTIFY_EMAIL, len(listings)
        )
        return True
    except Exception as exc:
        logger.error("Failed to send notification email: %s", exc)
        return False
