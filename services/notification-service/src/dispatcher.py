import logging
from dataclasses import dataclass
from enum import Enum
 
logger = logging.getLogger(__name__)
 
 
class NotificationChannel(str, Enum):
    EMAIL = "email"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    SMS = "sms"
 
 
class NotificationType(str, Enum):
    EXECUTION_CONFIRMED = "execution_confirmed"
    EXECUTION_FAILED = "execution_failed"
    HUMAN_APPROVAL_NEEDED = "human_approval_needed"
    AGENT_SUSPENDED = "agent_suspended"
    MARKETPLACE_SALE = "marketplace_sale"
 
 
@dataclass
class Notification:
    type: NotificationType
    recipient_user_id: str
    subject: str
    body: str
 
 
# Which channels each notification type goes to by default — time-sensitive
# ones (needs a human to act) go to more channels than informational ones.
_DEFAULT_CHANNELS = {
    NotificationType.EXECUTION_CONFIRMED: [NotificationChannel.EMAIL],
    NotificationType.EXECUTION_FAILED: [NotificationChannel.EMAIL, NotificationChannel.DISCORD],
    NotificationType.HUMAN_APPROVAL_NEEDED: [NotificationChannel.EMAIL, NotificationChannel.SMS, NotificationChannel.DISCORD],
    NotificationType.AGENT_SUSPENDED: [NotificationChannel.EMAIL, NotificationChannel.DISCORD],
    NotificationType.MARKETPLACE_SALE: [NotificationChannel.EMAIL],
}
 
 
class NotificationDispatcher:
    def __init__(self, email_channel, discord_channel, telegram_channel, sms_channel, db_session_factory):
        self._channels = {
            NotificationChannel.EMAIL: email_channel,
            NotificationChannel.DISCORD: discord_channel,
            NotificationChannel.TELEGRAM: telegram_channel,
            NotificationChannel.SMS: sms_channel,
        }
        self.db_session_factory = db_session_factory
 
    async def dispatch(self, notification: Notification, channels: list[NotificationChannel] | None = None):
        target_channels = channels or _DEFAULT_CHANNELS.get(notification.type, [NotificationChannel.EMAIL])
        recipient_contacts = await self._get_recipient_contacts(notification.recipient_user_id)
 
        results = []
        for channel_type in target_channels:
            contact = recipient_contacts.get(channel_type.value)
            if not contact:
                logger.info("No %s contact on file for user %s, skipping", channel_type, notification.recipient_user_id)
                continue
 
            channel = self._channels[channel_type]
            try:
                await channel.send(contact, notification.subject, notification.body)
                results.append((channel_type, "sent"))
            except Exception:
                logger.exception("Failed to send %s notification via %s", notification.type, channel_type)
                results.append((channel_type, "failed"))
 
        return results
 
    async def _get_recipient_contacts(self, user_id: str) -> dict[str, str]:
        async with self.db_session_factory() as session:
            # TODO: query db.models.users.User for email, plus whatever
            # linked-account fields you store for Discord/Telegram/SMS.
            return {}