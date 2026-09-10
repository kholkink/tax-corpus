"""taxcorpus — структурный парсер Налогового кодекса РФ.

Слой 2 архитектуры: разбор текста кодекса в иерархию структурных единиц
(часть / раздел / глава / статья / пункт / подпункт / абзац) с каноническими
идентификаторами, валидацией и извлечением явных ссылок.
"""

__version__ = "0.1.0"


def load_dotenv(path: str = ".env") -> dict[str, str]:
    """Подхватывает KEY=VALUE из .env в os.environ (не перезаписывая заданное).
    Ключи и адреса провайдера живут в .env (в .gitignore), а не в коде."""
    import os
    from pathlib import Path

    loaded: dict[str, str] = {}
    f = Path(path)
    if not f.exists():
        return loaded
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
