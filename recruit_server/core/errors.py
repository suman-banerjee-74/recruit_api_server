"""Public errors contain fixed messages, never raw Oracle responses or inputs."""


class APIError(Exception):
    def __init__(self, code, message, status=400, *, oracle_status=None, outcome=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.oracle_status = oracle_status
        self.outcome = outcome

    def public(self):
        result = {"code": self.code, "message": str(self)}
        if self.oracle_status is not None:
            result["oracle_status"] = self.oracle_status
        if self.outcome is not None:
            result.update(update_outcome=self.outcome, retry_safe=False)
        return result


def invalid(message="Invalid request parameters"):
    return APIError("invalid_parameters", message)
