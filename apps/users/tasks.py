from celery import shared_task
from django.core.management import call_command
from django.utils import timezone

from apps.users.models import RevokedTokenChain


@shared_task
def flush_expired_tokens() -> None:
    """Ночная чистка (§7.4): протухшие outstanding/blacklist-записи simplejwt и
    отзывы цепочек, которые уже пережили срок жизни refresh-токена."""
    call_command("flushexpiredtokens")
    RevokedTokenChain.objects.filter(expires_at__lte=timezone.now()).delete()
