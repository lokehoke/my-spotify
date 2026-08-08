from rest_framework import exceptions
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """Единый машиночитаемый конверт ошибок (ARCHITECTURE.md §7.13).

    {"error": {"code": ..., "message": ..., "details": ..., "request_id": ...}}
    Клиенты матчатся по `code`, не по тексту. Неожиданные 500 сюда не попадают —
    их обрабатывает Django (и Sentry, когда появится).
    """
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    request = context.get("request")

    if isinstance(exc, exceptions.ValidationError):
        code = "validation_error"
        message = "Некорректные данные запроса."
        details = exc.detail
    else:
        # get_codes() отдаёт код экземпляра (no_active_account, device_revoked...),
        # default_code класса — только фолбэк
        codes = exc.get_codes() if isinstance(exc, exceptions.APIException) else None
        if isinstance(codes, str):
            code = codes
        else:
            code = getattr(exc, "default_code", "error") or "error"
        raw = exc.detail if isinstance(exc, exceptions.APIException) else str(exc)
        if isinstance(raw, dict):
            # simplejwt кладёт в detail словарь {detail, code, messages}
            code = str(raw.get("code", code))
            message = str(raw.get("detail", "Ошибка запроса."))
            details = raw
        elif isinstance(raw, list):
            message = str(raw[0]) if raw else "Ошибка запроса."
            details = raw
        else:
            message = str(raw)
            details = None

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": getattr(request, "request_id", None),
        }
    }
    return response
