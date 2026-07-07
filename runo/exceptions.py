class RunoError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, status_code: int = 500):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


class SchemaInvalidError(RunoError):
    def __init__(self, message: str):
        super().__init__("SCHEMA_INVALID", message, retryable=False, status_code=422)


class FetchBlockedError(RunoError):
    def __init__(
        self,
        message: str = (
            "Target site's bot-protection blocked the request after every free "
            "fetch strategy (plain, TLS impersonation, stealth headless, archive "
            "fallback) was exhausted."
        ),
    ):
        super().__init__("FETCH_BLOCKED", message, retryable=True, status_code=502)


class FetchTimeoutError(RunoError):
    def __init__(self, message: str = "Page did not respond within timeout."):
        super().__init__("TIMEOUT", message, retryable=True, status_code=504)


class TypeCoercionError(RunoError):
    def __init__(self, message: str):
        super().__init__("TYPE_COERCION_FAILED", message, retryable=False, status_code=422)


class LLMError(RunoError):
    def __init__(self, message: str = "Gemini returned an unusable response."):
        super().__init__("LLM_ERROR", message, retryable=True, status_code=502)


class LLMUnavailableError(LLMError):
    def __init__(self, message: str = "Gemini is temporarily unavailable. Retries exhausted."):
        RunoError.__init__(
            self, "LLM_UNAVAILABLE", message, retryable=True, status_code=503,
        )


class LLMRateLimitedError(LLMError):
    def __init__(
        self,
        message: str = "Gemini rate limit hit. Retries exhausted.",
        retry_after_s: float | None = None,
    ):
        RunoError.__init__(
            self, "LLM_RATE_LIMITED", message, retryable=True, status_code=429,
        )
        self.retry_after_s = retry_after_s


class LLMTimeoutError(LLMError):
    def __init__(self, message: str = "Gemini call exceeded retry deadline."):
        RunoError.__init__(
            self, "LLM_TIMEOUT", message, retryable=True, status_code=504,
        )


class LLMTruncatedError(LLMError):
    def __init__(
        self,
        message: str = (
            "Gemini response could not be parsed as JSON after a bumped "
            "output budget retry."
        ),
    ):
        RunoError.__init__(
            self, "LLM_TRUNCATED", message, retryable=False, status_code=502,
        )


class LLMBlockedError(LLMError):
    def __init__(
        self,
        message: str = (
            "Gemini blocked the response (safety or policy). "
            "Try adjusting field hints or schema."
        ),
    ):
        RunoError.__init__(
            self, "LLM_BLOCKED", message, retryable=False, status_code=422,
        )


class LLMEmptyResponseError(LLMError):
    def __init__(self, message: str = "Gemini returned an empty response."):
        RunoError.__init__(
            self, "LLM_EMPTY", message, retryable=True, status_code=502,
        )


class LLMBadRequestError(LLMError):
    def __init__(
        self,
        message: str = (
            "Gemini rejected the request (prompt too long, invalid schema, "
            "or model unavailable for this key)."
        ),
    ):
        RunoError.__init__(
            self, "LLM_BAD_REQUEST", message, retryable=False, status_code=400,
        )


class URLUnreachableError(RunoError):
    def __init__(self, message: str = "DNS or network failure."):
        super().__init__("URL_UNREACHABLE", message, retryable=True, status_code=502)


class CrawlLimitReachedError(RunoError):
    def __init__(self, message: str = "max_pages hit before crawl completed."):
        super().__init__("CRAWL_LIMIT_REACHED", message, retryable=False, status_code=200)


class RateLimitedError(RunoError):
    def __init__(self, message: str = "Rate limit exceeded. Try again shortly."):
        super().__init__("RATE_LIMITED", message, retryable=True, status_code=429)


class QuotaExceededError(RunoError):
    def __init__(self, message: str = "Monthly request quota exceeded. Upgrade your plan."):
        super().__init__("QUOTA_EXCEEDED", message, retryable=False, status_code=429)


class TierRequiredError(RunoError):
    def __init__(
        self,
        message: str = (
            "This URL requires CAPTCHA-solving or residential proxies, "
            "available on Pro and Scale tiers."
        ),
        required_tier: str = "pro",
    ):
        super().__init__("TIER_REQUIRED", message, retryable=False, status_code=402)
        self.required_tier = required_tier
