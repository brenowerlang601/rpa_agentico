"""Gravação sintética no formato da extensão — sem dado real do ERP."""

import copy
import io
import json

URL = "https://erp.exemplo.local/app/form"

_GRAVACAO = {
    "name": "Cadastro de exemplo",
    "version": "1.0",
    "steps": [
        {"type": "navigation", "timestamp": "1000", "url": URL},
        {"type": "click", "timestamp": "2000", "url": URL, "xpath": "id(\"campo\")/input[1]",
         "elementTag": "INPUT", "targetText": "NOME"},
        {"type": "input", "timestamp": "3000", "url": URL, "xpath": "id(\"campo\")/input[1]",
         "elementTag": "INPUT", "value": "ABC"},
        {"type": "click", "timestamp": "4000", "url": URL, "xpath": "id(\"ok\")",
         "elementTag": "BUTTON", "elementText": "Confirmar"},
    ],
}


def gravacao() -> dict:
    """Cópia nova a cada chamada (o teste pode alterar à vontade)."""
    return copy.deepcopy(_GRAVACAO)


def arquivo(nome: str, conteudo) -> dict:
    """Corpo multipart do /api/upload. `conteudo` pode ser dict ou bytes crus."""
    raw = conteudo if isinstance(conteudo, bytes) else json.dumps(conteudo).encode("utf-8")
    return {"file": (io.BytesIO(raw), nome)}
