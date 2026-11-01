from apps.messaging.core.redact import mask_phone


def test_masks_all_but_last_three():
    assert mask_phone("+254712345678").endswith("678")
    assert "712345" not in mask_phone("+254712345678")


def test_handles_missing_values():
    assert mask_phone(None) == "***"
    assert mask_phone("") == "***"
