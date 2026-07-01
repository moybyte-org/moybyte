from pathlib import Path


def test_firmware_runtime_contract_mentions_lilygo_target():
    text = Path("docs/firmware_runtime_contract.md").read_text(encoding="utf-8")

    assert "LilyGO T-Deck Plus" in text
    assert "moybyte check-portable" in text
    assert "lilygo_t_deck_plus" in text
