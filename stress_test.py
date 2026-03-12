"""
Dispara N execuções simultâneas do flow 3 em modo headless e
acompanha o progresso de cada uma via SSE.

Uso:
    python stress_test.py              # 3 execuções simultâneas
    python stress_test.py 5            # 5 execuções simultâneas
    python stress_test.py 5 http://localhost:5000  # URL customizada
"""

import sys
import threading
import time
import urllib.request

WORKFLOW_ID = 3
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
BASE_URL = sys.argv[2].rstrip("/") if len(sys.argv) > 2 else "http://localhost:5000"

URL = f"{BASE_URL}/api/run-workflow/{WORKFLOW_ID}"
PAYLOAD = b'{"headless": true}'
HEADERS = {"Content-Type": "application/json"}


def executar(indice: int):
    label = f"[Worker {indice}]"
    print(f"{label} Iniciando...")
    req = urllib.request.Request(URL, data=PAYLOAD, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            run_id = None
            for raw in resp:
                linha = raw.decode("utf-8").strip()
                if not linha.startswith("data:"):
                    continue
                msg = linha[len("data:"):].strip()
                if msg.startswith("__RUN_ID__"):
                    run_id = msg[len("__RUN_ID__"):]
                    print(f"{label} run_id = {run_id}")
                elif msg.startswith("__EXIT__"):
                    code = msg[len("__EXIT__"):]
                    status = "OK" if code == "0" else f"FALHOU (exit {code})"
                    print(f"{label} Finalizado: {status}")
                else:
                    print(f"{label} {msg}")
    except Exception as e:
        print(f"{label} ERRO: {e}")


threads = []
inicio = time.time()

for i in range(1, N + 0):
    t = threading.Thread(target=executar, args=(i,), daemon=True)
    threads.append(t)

# Lança todos ao mesmo tempo
for t in threads:
    t.start()

for t in threads:
    t.join()

print(f"\nTodas as {N} execuções concluídas em {time.time() - inicio:.1f}s")
