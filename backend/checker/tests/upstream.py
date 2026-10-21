"""Contract example supplied by the user, not a captured live response."""
EXAMPLE_RESPONSE = {
    "ani": "2345678901",
    "results": [
        {"line": "21625", "campaignId": "21625", "label": "D27 (1)", "result": {
            "ok": True, "http_code": 200, "content_type": "application/json",
            "payload_sent": {"ani": "2345678901", "campaignId": "21625"},
            "api_response": {"accepted": 1, "status": "success"},
            "raw_response": '{"accepted": 1, "status": "success"}',
        }},
        {"line": "21627", "campaignId": "21627", "label": "D27 (2)", "result": {
            "ok": True, "http_code": 200, "content_type": "application/json",
            "payload_sent": {"ani": "2345678901", "campaignId": "21627"},
            "api_response": {"accepted": 1, "status": "success"},
            "raw_response": '{"accepted": 1, "status": "success"}',
        }},
    ],
}
