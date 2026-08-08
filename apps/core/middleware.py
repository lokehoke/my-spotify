import uuid


class RequestIDMiddleware:
    """request_id на каждый запрос: принимается X-Request-ID от прокси или генерируется.

    Возвращается в заголовке ответа и попадает в конверт ошибок (см. exceptions.py) —
    склейка ответа клиента с логами по ARCHITECTURE.md §7.13.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = (request.headers.get("X-Request-ID") or uuid.uuid4().hex)[:64]
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response
