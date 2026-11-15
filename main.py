import sys
from pathlib import Path

from json_processor import list_json_rpa, _slug
from orchestrator import run_from_json

JSON_RPA_DIR = Path("json_rpa")


def main():
    # O argumento tem prioridade: funciona mesmo com json_rpa/ vazio
    if len(sys.argv) > 1:
        selected = Path(sys.argv[1])
        if not selected.exists():
            selected = JSON_RPA_DIR / sys.argv[1]
        if not selected.exists():
            print(f"Arquivo nao encontrado: {sys.argv[1]}")
            sys.exit(1)
        run_from_json(selected)
        return

    files = list_json_rpa(JSON_RPA_DIR)

    if not files:
        print("Nenhum arquivo JSON encontrado em json_rpa/")
        sys.exit(1)

    if len(files) == 1:
        selected = files[0]
    else:
        print("JSONs disponíveis em json_rpa/:")
        for idx, f in enumerate(files):
            print(f"  {idx + 1}. {f.name}")
        choice = input("Escolha o número do arquivo: ").strip()
        try:
            selected = files[int(choice) - 1]
        except (ValueError, IndexError):
            print("Escolha inválida.")
            sys.exit(1)

    run_from_json(selected)


if __name__ == '__main__':
    main()
