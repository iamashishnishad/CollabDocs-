import time


class RequestLoggingMiddleware:
    """Prints method, path, status code and elapsed milliseconds for every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(f"{request.method} {request.path} {response.status_code} {elapsed_ms:.2f}ms", flush=True)
        return response
