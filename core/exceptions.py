from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    """Uniform {"error": ...} bodies; maps DoesNotExist -> 404, IntegrityError -> 409."""
    if isinstance(exc, ObjectDoesNotExist):
        return Response({"error": "Resource not found."}, status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, IntegrityError):
        return Response({"error": "Conflict: the request violates a data integrity rule."},
                        status=status.HTTP_409_CONFLICT)
    response = exception_handler(exc, context)
    if response is not None and not (isinstance(response.data, dict) and "error" in response.data):
        detail = response.data
        if isinstance(detail, dict) and set(detail) == {"detail"}:
            response.data = {"error": detail["detail"]}
        else:
            response.data = {"error": "Validation failed." if response.status_code == 400 else "Request failed.",
                             "details": detail}
    return response
