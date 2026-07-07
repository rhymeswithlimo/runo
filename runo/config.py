from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" so unrelated env vars don't crash boot. The only value you
    # strictly need is a Gemini API key (GEMINI_API_KEY, or GEMINI_API_KEYS for
    # round-robin across several). Everything else is a tunable with a default.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )

    # ── Gemini (the one required credential) ─────────────────────────────────
    gemini_api_key: str = ""
    gemini_api_keys: str = ""  # Comma-separated; round-robin across multiple keys

    # ── Fetch / headless rendering ───────────────────────────────────────────
    playwright_headless: bool = True
    prewarm_browser: bool = True
    headless_networkidle_ms: int = 500   # Cap for page.wait_for_load_state("networkidle")
    max_concurrency: int = 10
    headless_concurrency: int = 4  # Playwright thread-pool size and semaphore ceiling
    fetch_timeout_ms: int = 10000
    headless_timeout_ms: int = 15000
    headless_upstream_url: str = ""  # If set, forward Playwright requests to a remote worker

    # ── LLM prompt shaping ───────────────────────────────────────────────────
    lite_thinking_budget: int = 512  # Lite CoT minimum (Gemini API floor). Closes the prompt-following gap. +~0.4s P50.
    lite_thinking_budget_complex: int = 1024  # If >0, switch lite to this CoT budget for ≥6-field or array<> schemas
    bm25_budget_tokens: int = 12000          # Token budget for BM25 chunk filter; raise = fewer trims, more input
    bm25_budget_tokens_fallback: int = 24000  # Larger budget for null-rate fallback path
    bm25_hero_boost: float = 1.0  # Additive BM25 score boost on chunks within the first 1500 chars
    bm25_linklist_penalty: float = 0.5  # Additive BM25 score penalty on chunks dominated by link-list rows
    list_preamble_enabled: bool = True  # Pull repeated list/card structure into preamble for array<> schemas
    canonical_preamble_enabled: bool = True  # Prepend ## Page Identifiers (title/h1/og/meta) before LLM input
    description_candidates_enabled: bool = True  # ## Candidate descriptions block when schema has description-shaped fields
    structured_partial_skip_threshold: float = 0.7  # If prefilled >= this fraction of fields, skip LLM

    # ── Field-level result cache ─────────────────────────────────────────────
    # Per-field cache so requests with overlapping schemas on the same URL share
    # work. In-memory in the open-source build (resets on restart).
    field_cache_enabled: bool = True
    field_cache_skip_volatile: bool = True       # Skip price/stock-named fields entirely
    field_cache_volatile_ttl_s: int = 600        # Used when skip_volatile is False

    # ── LLM retry + safeguards (Gemini 503/429 resilience) ───────────────────
    # Every knob below only affects behavior inside an except branch — zero
    # overhead on the success path.
    llm_retry_deadline_ms: int = 45000           # Hard wall-clock cap per extraction
    llm_retry_max_attempts_unavailable: int = 4  # 503/5xx/overloaded attempts (incl. first)
    llm_retry_max_attempts_rate_limited: int = 3 # 429 attempts (incl. first); rotates key
    llm_retry_rotate_keys_on_rate_limit: bool = True
    llm_truncation_max_bump: float = 2.0         # Max output-budget multiplier on truncation retries
    llm_emit_warnings: bool = True               # Surface coercion warnings in response

    # ── Quality / resilience feature flags (all reversible) ──────────────────
    stealth_enabled: bool = True          # playwright-stealth patches on headless
    http2_enabled: bool = True            # HTTP/2 on httpx client
    realistic_headers: bool = True        # Sec-CH-UA, Sec-Fetch-*, etc.
    rotate_user_agent: bool = True        # rotate UA across a stable Chrome/FF pool
    block_detection_enabled: bool = True  # proactive CF/Datadome/PX detection
    retry_before_escalate: bool = False   # 250–750ms retry on 429/503 before headless
    negative_cache_ttl_s: int = 600       # blocked-host short-TTL cache; 0 disables
    partial_structured_fallback: bool = True  # pre-fill fields before LLM
    oembed_enabled: bool = True           # oEmbed discovery + fetch
    twitter_cards_enabled: bool = True    # Twitter Card meta on structured path
    crawler_host_jitter_ms_min: int = 250
    crawler_host_jitter_ms_max: int = 750
    crawler_respect_robots: bool = True
    crawler_sitemap_seed: bool = False    # opt-in via crawl.use_sitemap too

    # ── Bypass ladder (free tiers only: T1/T2/T3/T6) ─────────────────────────
    # The paid T4 (CAPTCHA solving) and T5 (residential proxy) tiers from the
    # hosted product are not part of the open-source build.
    #
    # T1: TLS-impersonating plain fetch via curl_cffi. Defeats JA3/JA4 checks.
    tls_impersonate: str = "chrome124"    # chrome120|chrome124|safari17_0|firefox125|off
    # T2: hardened headless engine. `patchright` is a CDP-leak-patched Playwright
    # fork; `camoufox` is a hardened Firefox. Falls back to vanilla playwright.
    headless_engine: str = "patchright"   # patchright|camoufox|playwright
    fingerprint_injection: bool = True    # canvas/WebGL/audio bundle in headless
    # T3: session warming + cookie persistence per host.
    session_warming_enabled: bool = True
    # T6: archive fallback (Google Cache / Wayback / AMP / reader view).
    archive_fallback_enabled: bool = True

    # ── Security / hardening ─────────────────────────────────────────────────
    # All guards below only fire on malicious / pathological input.
    ssrf_guard_enabled: bool = True            # Reject private/loopback/link-local/metadata IPs and non-http(s) schemes
    max_html_bytes: int = 10_485_760           # 10 MB cap on raw HTML before parsing
    max_response_bytes: int = 25_165_824       # 24 MB hard cap on response-body download
    crawl_wall_clock_s: int = 600              # Hard wall-clock cap on a single /crawl call
    follow_pattern_max_wildcards: int = 8      # Reject pathological glob patterns
    follow_pattern_max_len: int = 256


settings = Settings()
