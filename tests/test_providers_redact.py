"""Профили провайдера (P4) и маскировка ПДн (F9), в т.ч. в сессии дела."""

import json

import pytest

from taxcorpus import providers as P
from taxcorpus.redact import Redactor


def test_default_profile_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    monkeypatch.setenv("TAXCORPUS_MODEL", "deepseek-chat")
    monkeypatch.delenv("TAXCORPUS_PROVIDER", raising=False)
    monkeypatch.delenv("TAXCORPUS_ALLOW_CLOUD_SENSITIVE", raising=False)
    p = P.choose("standard", config_path=tmp_path / "none.json")
    assert p.model == "deepseek-chat" and p.location == "cloud" and not p.fallbacks and "DeepSeek" in p.badge()
    with pytest.raises(P.ProviderError):
        P.choose("sensitive", config_path=tmp_path / "none.json")   # локального профиля нет
    monkeypatch.setenv("TAXCORPUS_ALLOW_CLOUD_SENSITIVE", "1")
    assert P.choose("sensitive", config_path=tmp_path / "none.json").name == "default"


def test_profiles_from_config(monkeypatch, tmp_path):
    cfg = tmp_path / "providers.json"
    cfg.write_text(json.dumps({"default": "cloud1", "local": "loc", "profiles": {
        "cloud1": {"base_url": "https://x/anthropic", "api_key_env": "X_KEY", "model": "m1", "location": "cloud"},
        "loc": {"base_url": "http://127.0.0.1:4000", "api_key_env": "L_KEY", "model": "qwen", "location": "local",
                "label": "локальная"},
        "anth": {"base_url": None, "api_key_env": "A_KEY", "model": "claude-opus-5", "fallbacks": True},
    }}), encoding="utf-8")
    monkeypatch.setenv("X_KEY", "k1")
    monkeypatch.delenv("TAXCORPUS_PROVIDER", raising=False)
    assert P.choose("standard", config_path=cfg).name == "cloud1"
    loc = P.choose("sensitive", config_path=cfg)
    assert loc.name == "loc" and loc.location == "local" and "локально" in loc.badge()
    assert P.choose("standard", "anth", config_path=cfg).fallbacks is True
    with pytest.raises(P.ProviderError):
        P.choose("standard", "nope", config_path=cfg)
    monkeypatch.setenv("TAXCORPUS_PROVIDER", "anth")
    assert P.choose("standard", config_path=cfg).name == "anth"


def test_redactor_round_trip(tmp_path):
    r = Redactor(tmp_path / "map.json")
    text = ("ООО «Ромашка», ИНН 7701234567, КПП 770101001, директор Иванов Иван Иванович (паспорт 45 12 №123456), "
            "тел. +7 (495) 123-45-67, e-mail ivanov@mail.ru, счёт 40702810900000012345, СНИЛС 123-456-789 01; "
            "сумма 1 200 000 руб., срок 20.03.2026; см. также И.И. Иванов и Петров П.П.")
    masked = r.redact(text)
    for secret in ("7701234567", "770101001", "Иванов Иван Иванович", "45 12 №123456", "+7 (495) 123-45-67",
                   "ivanov@mail.ru", "40702810900000012345", "123-456-789 01", "И.И. Иванов", "Петров П.П."):
        assert secret not in masked, secret
    assert "1 200 000 руб." in masked and "20.03.2026" in masked and "ООО «Ромашка»" in masked
    assert r.unredact(masked) == text
    # повторная маскировка того же значения даёт тот же плейсхолдер; словарь сохранён
    assert r.redact("ИНН 7701234567 снова") == "ИНН [ИНН-1] снова"
    assert r.redact("выдано Ивановой Анне Петровне и Сидорову Петру Петровичу") == "выдано [ФИО-4] и [ФИО-5]"
    r2 = Redactor(tmp_path / "map.json")
    assert r2.unredact("[ФИО-1]") == "Иванов Иван Иванович" and r2.stats()["ИНН"] >= 1


def test_session_masks_pii_for_cloud_provider(tmp_path):
    from taxcorpus.agent import TaxAgent
    from taxcorpus.case_session import CaseSession
    from taxcorpus.parser import parse_document
    from taxcorpus.tools import LocalCorpus
    from taxcorpus.workspace import Workspace
    from tests.test_agent import TEXT, FakeClient, _block, _response

    ws = Workspace.create("sens", "Чувствительное дело", as_of="2026-09-10", root=tmp_path, confidentiality="sensitive")
    (ws.path / "notes" / "задача.md").write_text("Клиент Сидоров Пётр Петрович, ИНН 500100732259.", encoding="utf-8")
    responses = [
        _response([_block(type="tool_use", id="t1", name="read_file", input={"path": "notes/задача.md"})], "tool_use"),
        _response([_block(type="text", text="Вывод для [ФИО-1] (ИНН [ИНН-1]): срок — п. 2 ст. 88 НК РФ.")], "end_turn"),
    ]
    client = FakeClient(responses)
    _, records, _ = parse_document(TEXT, "nk1")
    agent = TaxAgent(client, LocalCorpus.from_records(records, {"nk1": "2026-08-04"}), fallbacks=False)
    agent.provider = P.Provider("cloud", "m", "cloud", "облако")
    session = CaseSession(ws, agent)
    turn = session.send("Проверь Сидорова Петра Петровича, ИНН 500100732259")
    # к модели ушли плейсхолдеры, в ответе юристу — реальные данные
    sent_user = client.requests[0]["messages"][0]["content"]
    assert "Сидоров" not in sent_user and "[ИНН-1]" in sent_user
    tool_result = client.requests[1]["messages"][-1]["content"][0]["content"]
    assert "500100732259" not in tool_result and "[ФИО-" in tool_result
    assert "Сидорова Петра Петровича" in turn.text and "500100732259" in turn.text
    assert "[ФИО-" not in turn.text and "[ИНН-" not in turn.text
    assert (ws.path / "redaction_map.json").exists()
    # у обычного дела маскировки нет
    ws2 = Workspace.create("plain", "Обычное", as_of="2026-09-10", root=tmp_path)
    assert CaseSession(ws2, agent).redactor is None
